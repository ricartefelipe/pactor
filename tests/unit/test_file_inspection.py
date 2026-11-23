"""Inspeção estrutural de PDF e DOCX (executada no processo de teste)."""

from __future__ import annotations

import pytest

from pactor.files.inspection import FileKind, RejectionReason, detect_kind, inspect
from tests import file_samples


def test_valid_pdf_and_docx_are_accepted_with_text() -> None:
    result = inspect(FileKind.PDF, file_samples.pdf("Texto do anexo"))
    assert result.ok
    assert result.pages == 1
    assert "Texto do anexo" in result.text
    document = inspect(FileKind.DOCX, file_samples.docx_file("Parágrafo único"))
    assert document.ok
    assert "Parágrafo único" in document.text


@pytest.mark.parametrize(
    ("kind", "content", "reason"),
    [
        (FileKind.PDF, file_samples.encrypted_pdf(), RejectionReason.ENCRYPTED),
        (FileKind.PDF, file_samples.javascript_pdf(), RejectionReason.ACTIVE_CONTENT),
        (FileKind.PDF, b"%PDF-1.7 truncado", RejectionReason.MALFORMED),
        (FileKind.PDF, file_samples.docx_file(), RejectionReason.TYPE_MISMATCH),
        (FileKind.DOCX, file_samples.pdf(), RejectionReason.TYPE_MISMATCH),
        (FileKind.DOCX, file_samples.macro_docx(), RejectionReason.MACROS),
        (FileKind.DOCX, file_samples.macro_content_type_docx(), RejectionReason.MACROS),
        (FileKind.DOCX, file_samples.external_reference_docx(), RejectionReason.EXTERNAL_REFERENCE),
        (FileKind.DOCX, file_samples.traversal_docx(), RejectionReason.INVALID_PATH),
        (FileKind.DOCX, file_samples.zip_bomb_docx(), RejectionReason.ZIP_BOMB),
        (FileKind.DOCX, file_samples.not_a_docx_zip(), RejectionReason.TYPE_MISMATCH),
        (FileKind.DOCX, b"PK\x03\x04corrompido", RejectionReason.MALFORMED),
        (FileKind.PDF, file_samples.EICAR, RejectionReason.TYPE_MISMATCH),
    ],
)
def test_hostile_or_malformed_files_are_rejected_with_reason(
    kind: FileKind, content: bytes, reason: RejectionReason
) -> None:
    result = inspect(kind, content)
    assert not result.ok
    assert result.reason is reason
    assert result.text == ""


def test_detect_kind_by_magic_bytes() -> None:
    assert detect_kind(b"%PDF-1.4") is FileKind.PDF
    assert detect_kind(b"PK\x03\x04") is FileKind.DOCX
    assert detect_kind(b"MZ\x90\x00") is None
