"""Conferência dos documentos armazenados contra os hashes do banco."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from pactor.access.policies import Role
from pactor.core.tenancy import tenant_context
from pactor.files import services as files
from pactor.files.models import FilePurpose, FileStatus, StoredFile
from pactor.identity.models import Organization
from pactor.integrity import Problem, Source, UnknownOrganizationsError, verify_documents
from pactor.signatures.models import EnvelopeDocument
from tests import factories, file_samples
from tests.integration.test_signatures import _approved, _dispatched

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def media(tmp_path: Path) -> Iterator[Path]:
    with override_settings(MEDIA_ROOT=tmp_path):
        yield tmp_path


def _stored_file() -> tuple[Organization, StoredFile]:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    with tenant_context(org.id):
        stored = files.receive(
            factories.principal(org, manager),
            name="anexo.pdf",
            content=file_samples.pdf("conteúdo fictício"),
            purpose=FilePurpose.CONTRACT_ATTACHMENT,
        )
    return org, stored


def test_intact_files_and_signature_documents_pass(media: Path) -> None:
    _stored_file()
    approved = _approved()
    _dispatched(approved)
    with tenant_context(approved.org.id):
        assert EnvelopeDocument.objects.exists()
    report = verify_documents()
    assert report.ok
    assert report.checked >= 2
    out = io.StringIO()
    call_command("verify_documents", stdout=out)
    assert "0 divergências" in out.getvalue()


def test_missing_truncated_and_altered_documents_are_reported(media: Path) -> None:
    org, stored = _stored_file()
    approved = _approved()
    _dispatched(approved)
    with tenant_context(approved.org.id):
        document = EnvelopeDocument.objects.get()
    original = (media / stored.storage_path).read_bytes()
    (media / stored.storage_path).write_bytes(original[:-1] + bytes([original[-1] ^ 0xFF]))
    (media / document.storage_path).unlink()

    report = verify_documents()
    problems = {(f.source, f.record_id): f.problem for f in report.findings}
    assert problems == {
        (Source.FILE, stored.id): Problem.HASH_MISMATCH,
        (Source.SIGNATURE_DOCUMENT, document.id): Problem.MISSING,
    }

    (media / stored.storage_path).write_bytes(original[:10])
    only_org = verify_documents([org.id])
    assert [(f.source, f.problem) for f in only_org.findings] == [
        (Source.FILE, Problem.SIZE_MISMATCH)
    ]

    err = io.StringIO()
    with pytest.raises(CommandError, match="2 divergências"):
        call_command("verify_documents", stdout=io.StringIO(), stderr=err)
    assert f"tamanho divergente: arquivo {stored.id}" in err.getvalue()
    assert f"ausente: documento de assinatura {document.id}" in err.getvalue()


def test_missing_preview_is_reported(media: Path) -> None:
    org, stored = _stored_file()
    with tenant_context(org.id):
        StoredFile.objects.filter(id=stored.id).update(preview_path="files/inexistente.pdf")
    report = verify_documents([org.id])
    assert [(f.source, f.problem) for f in report.findings] == [(Source.PREVIEW, Problem.MISSING)]


def test_unknown_organization_is_an_error_not_an_empty_success(media: Path) -> None:
    unknown = uuid.uuid4()
    with pytest.raises(UnknownOrganizationsError):
        verify_documents([unknown])
    with pytest.raises(CommandError, match="Organização inexistente"):
        call_command("verify_documents", "--organizacao", str(unknown), stdout=io.StringIO())


def test_file_rejected_for_altered_bytes_is_only_checked_for_presence(media: Path) -> None:
    org, stored = _stored_file()
    with tenant_context(org.id):
        StoredFile.objects.filter(id=stored.id).update(
            status=FileStatus.REJECTED, rejection_reason="hash_mismatch"
        )
    (media / stored.storage_path).write_bytes(b"bytes alterados na quarentena")
    assert verify_documents([org.id]).ok
    (media / stored.storage_path).unlink()
    assert [(f.source, f.problem) for f in verify_documents([org.id]).findings] == [
        (Source.FILE, Problem.MISSING)
    ]
