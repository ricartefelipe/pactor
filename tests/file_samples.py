"""Arquivos de teste gerados em memória (válidos e hostis)."""

from __future__ import annotations

import io
import zipfile

import docx
from pypdf import PdfWriter

from pactor.contracts.pdf import PdfDocument, render_pdf

# Arquivo de teste padrão da indústria antivírus (inofensivo por definição).
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def pdf(text: str = "Cláusula 1. Objeto do contrato (exemplo fictício).") -> bytes:
    return render_pdf(
        PdfDocument(title="Anexo fictício", header_lines=[], body=text, footer_lines=[])
    )


def encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("senha")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def javascript_pdf() -> bytes:
    """PDF mínimo com ação de abertura em JavaScript."""
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R/OpenAction 3 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
        b"3 0 obj<</S/JavaScript/JS(app.alert(1))>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


def docx_file(text: str = "Termo aditivo fictício para testes.") -> bytes:
    document = docx.Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _rewrite_docx(
    extra: dict[str, bytes] | None = None, replace: dict[str, bytes] | None = None
) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(docx_file()))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = (replace or {}).get(item.filename, source.read(item.filename))
            target.writestr(item.filename, data)
        for name, data in (extra or {}).items():
            target.writestr(name, data)
    return buffer.getvalue()


def macro_docx() -> bytes:
    return _rewrite_docx(extra={"word/vbaProject.bin": b"\x00macro"})


def external_reference_docx() -> bytes:
    ns = b"http://schemas.openxmlformats.org"
    rels = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Relationships xmlns="' + ns + b'/package/2006/relationships">'
        b'<Relationship Id="rId99" Type="'
        + ns
        + b'/officeDocument/2006/relationships/attachedTemplate" '
        b'Target="http://exemplo.invalid/modelo.dotm" TargetMode="External"/></Relationships>'
    )
    return _rewrite_docx(extra={"word/_rels/settings.xml.rels": rels})


def traversal_docx() -> bytes:
    return _rewrite_docx(extra={"../../etc/evil.txt": b"x"})


def zip_bomb_docx() -> bytes:
    return _rewrite_docx(extra={"word/media/bomba.bin": b"\0" * (20 * 1024 * 1024)})


def macro_content_type_docx() -> bytes:
    source = zipfile.ZipFile(io.BytesIO(docx_file()))
    types = source.read("[Content_Types].xml").replace(
        b"document.main+xml", b"document.macroEnabled.main+xml"
    )
    return _rewrite_docx(replace={"[Content_Types].xml": types})


def not_a_docx_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("leia.txt", b"zip qualquer")
    return buffer.getvalue()
