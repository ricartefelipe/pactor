"""Validação estrutural e extração de texto de PDF e DOCX.

Executado em subprocesso isolado (``python -m pactor.files.sandbox``) com
limites de CPU, memória e escrita em disco. As funções também são puras o
suficiente para teste direto. Qualquer construção potencialmente ativa ou
perigosa causa rejeição com motivo específico; não há tentativa de "limpar"
o arquivo.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from enum import StrEnum

import docx
from docx.opc.exceptions import PackageNotFoundError
from lxml.etree import XMLSyntaxError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PDF_PAGES = 500
MAX_TEXT_CHARS = 2_000_000
MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_UNCOMPRESSED = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
_PDF_FORBIDDEN = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/RichMedia", b"/XFA")
_EXTERNAL_TARGET = re.compile(rb'TargetMode\s*=\s*"External"', re.IGNORECASE)
_MACRO_MARKERS = ("vbaproject.bin", "vbadata.xml")


class FileKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


class RejectionReason(StrEnum):
    TYPE_MISMATCH = "type_mismatch"
    MALFORMED = "malformed"
    ENCRYPTED = "encrypted"
    TOO_MANY_PAGES = "too_many_pages"
    ACTIVE_CONTENT = "active_content"
    ZIP_BOMB = "zip_bomb"
    INVALID_PATH = "invalid_path"
    MACROS = "macros"
    EXTERNAL_REFERENCE = "external_reference"
    PROCESSING_LIMIT = "processing_limit"


@dataclass(frozen=True, slots=True)
class Inspection:
    ok: bool
    reason: RejectionReason | None
    detail: str
    pages: int
    text: str


def rejection(reason: RejectionReason, detail: str) -> Inspection:
    return Inspection(ok=False, reason=reason, detail=detail, pages=0, text="")


def detect_kind(content: bytes) -> FileKind | None:
    if content.startswith(b"%PDF-"):
        return FileKind.PDF
    if content.startswith(b"PK\x03\x04"):
        return FileKind.DOCX
    return None


def inspect_pdf(content: bytes) -> Inspection:
    for marker in _PDF_FORBIDDEN:
        if marker in content:
            return rejection(RejectionReason.ACTIVE_CONTENT, marker.decode())
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            return rejection(RejectionReason.ENCRYPTED, "PDF protegido por senha")
        pages = len(reader.pages)
        if pages > MAX_PDF_PAGES:
            return rejection(RejectionReason.TOO_MANY_PAGES, f"{pages} páginas")
        parts: list[str] = []
        size = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            size += len(text)
            if size > MAX_TEXT_CHARS:
                break
            parts.append(text)
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        return rejection(RejectionReason.MALFORMED, type(exc).__name__)
    return Inspection(
        ok=True, reason=None, detail="", pages=pages, text="\n".join(parts)[:MAX_TEXT_CHARS]
    )


def _check_zip(archive: zipfile.ZipFile) -> Inspection | None:
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES:
        return rejection(RejectionReason.ZIP_BOMB, f"{len(entries)} entradas")
    total = 0
    for entry in entries:
        name = entry.filename
        normalized = posixpath.normpath(name)
        if (
            name.startswith(("/", "\\"))
            or ".." in normalized.split("/")
            or ":" in name
            or "\\" in name
        ):
            return rejection(RejectionReason.INVALID_PATH, name[:120])
        if any(marker in name.lower() for marker in _MACRO_MARKERS):
            return rejection(RejectionReason.MACROS, name[:120])
        total += entry.file_size
        if total > MAX_ZIP_UNCOMPRESSED:
            return rejection(RejectionReason.ZIP_BOMB, "tamanho descompactado excessivo")
        if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
            return rejection(RejectionReason.ZIP_BOMB, f"taxa de compressão em {name[:80]}")
    return None


def _check_docx_parts(archive: zipfile.ZipFile) -> Inspection | None:
    names = set(archive.namelist())
    if "word/document.xml" not in names or "[Content_Types].xml" not in names:
        return rejection(RejectionReason.TYPE_MISMATCH, "estrutura DOCX ausente")
    try:
        if b"macroEnabled" in archive.read("[Content_Types].xml"):
            return rejection(RejectionReason.MACROS, "tipo de conteúdo com macros")
        external = next(
            (
                n
                for n in sorted(names)
                if n.endswith(".rels") and _EXTERNAL_TARGET.search(archive.read(n))
            ),
            None,
        )
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        return rejection(RejectionReason.MALFORMED, type(exc).__name__)
    if external is not None:
        return rejection(RejectionReason.EXTERNAL_REFERENCE, external[:120])
    return None


def inspect_docx(content: bytes) -> Inspection:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, ValueError) as exc:
        return rejection(RejectionReason.MALFORMED, type(exc).__name__)
    with archive:
        problem = _check_zip(archive) or _check_docx_parts(archive)
    if problem is not None:
        return problem
    try:
        document = docx.Document(io.BytesIO(content))
        text = "\n".join(p.text for p in document.paragraphs)
    except (PackageNotFoundError, XMLSyntaxError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        return rejection(RejectionReason.MALFORMED, type(exc).__name__)
    return Inspection(ok=True, reason=None, detail="", pages=0, text=text[:MAX_TEXT_CHARS])


def inspect(kind: FileKind, content: bytes) -> Inspection:
    detected = detect_kind(content)
    if detected is not kind:
        return rejection(RejectionReason.TYPE_MISMATCH, "conteúdo não corresponde à extensão")
    return inspect_pdf(content) if kind is FileKind.PDF else inspect_docx(content)
