"""Pipeline de arquivos: quarentena, antimalware, inspeção isolada, liberação e anexos."""

from __future__ import annotations

import subprocess
import uuid
from typing import cast

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client, override_settings

from pactor.access.policies import Role
from pactor.contracts import services as contracts
from pactor.contracts.models import Contract
from pactor.core.models import OutboxEvent
from pactor.core.outbox import EventEnvelope
from pactor.core.tenancy import tenant_context
from pactor.files import services, tasks
from pactor.files.inspection import FileKind, RejectionReason
from pactor.files.models import FileStatus, StoredFile
from pactor.files.scanner import ClamdScanner, ScannerUnavailableError, ScanResult, ScanVerdict
from pactor.identity.models import Organization, User
from pactor.plans.models import Plan
from tests import factories, file_samples

pytestmark = pytest.mark.django_db(transaction=True)


class FakeScanner:
    """Substituto determinístico do clamd para a lógica do pipeline.

    O comportamento com o ClamAV real é verificado em tests/infra/test_clamav.py.
    """

    def __init__(self, *, infected: bool = False, unavailable: bool = False) -> None:
        self.infected = infected
        self.unavailable = unavailable

    def scan(self, content: bytes) -> ScanResult:
        if self.unavailable:
            raise ScannerUnavailableError("scanner fora do ar")
        if self.infected or file_samples.EICAR in content:
            return ScanResult(ScanVerdict.INFECTED, "Teste-Assinatura", "fake 1.0")
        return ScanResult(ScanVerdict.CLEAN, "", "fake 1.0")


def _scanner(fake: FakeScanner) -> ClamdScanner:
    return cast("ClamdScanner", fake)


def _setup() -> tuple[Organization, User, Client]:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    return org, manager, factories.client_for(manager, org)


def _upload(client: Client, name: str, content: bytes) -> dict[str, object]:
    response = client.post("/api/v1/files", {"file": SimpleUploadedFile(name, content)})
    assert response.status_code == 202, response.json()
    body = response.json()
    assert isinstance(body, dict)
    return body


def _stored(org: Organization, file_id: object) -> StoredFile:
    with tenant_context(org.id):
        return StoredFile.objects.get(id=str(file_id))


def test_clean_pdf_is_quarantined_then_released_after_scan_and_inspection() -> None:
    org, _, client = _setup()
    uploaded = _upload(client, "anexo.pdf", file_samples.pdf("Texto pesquisável do anexo"))
    assert uploaded["status"] == "quarantined"
    assert client.get(f"/api/v1/files/{uploaded['id']}/content").status_code == 409
    with tenant_context(org.id):
        assert OutboxEvent.objects.filter(topic="files.received").count() == 1
    services.process(org.id, uuid.UUID(str(uploaded["id"])), scanner=_scanner(FakeScanner()))
    stored = _stored(org, uploaded["id"])
    assert stored.status == FileStatus.RELEASED
    assert "Texto pesquisável" in stored.extracted_text
    download = client.get(f"/api/v1/files/{uploaded['id']}/content")
    assert download.status_code == 200
    assert download["X-Pactor-SHA256"] == stored.sha256
    assert download["Content-Type"] == "application/pdf"
    with tenant_context(org.id):
        assert OutboxEvent.objects.filter(topic="files.released").count() == 1


def test_docx_gets_text_preview() -> None:
    org, _, client = _setup()
    uploaded = _upload(client, "minuta.docx", file_samples.docx_file("Minuta com prévia"))
    services.process(org.id, uuid.UUID(str(uploaded["id"])), scanner=_scanner(FakeScanner()))
    preview = client.get(f"/api/v1/files/{uploaded['id']}/preview")
    assert preview.status_code == 200
    assert preview.content.startswith(b"%PDF")


def test_infected_file_is_blocked_and_never_released() -> None:
    org, _, client = _setup()
    uploaded = _upload(client, "virus.pdf", b"%PDF-1.4 " + file_samples.EICAR)
    services.process(org.id, uuid.UUID(str(uploaded["id"])), scanner=_scanner(FakeScanner()))
    stored = _stored(org, uploaded["id"])
    assert stored.status == FileStatus.INFECTED
    assert stored.rejection_detail == "Teste-Assinatura"
    assert stored.extracted_text == ""
    assert client.get(f"/api/v1/files/{uploaded['id']}/content").status_code == 409


def test_unavailable_scanner_keeps_file_quarantined_and_retries() -> None:
    org, _, client = _setup()
    uploaded = _upload(client, "anexo.pdf", file_samples.pdf())
    file_id = uuid.UUID(str(uploaded["id"]))
    with pytest.raises(services.ScanRetryError):
        services.process(org.id, file_id, scanner=_scanner(FakeScanner(unavailable=True)))
    stored = _stored(org, file_id)
    assert stored.status == FileStatus.QUARANTINED
    assert stored.scan_attempts == 1
    assert "fora do ar" in stored.last_error
    services.process(org.id, file_id, scanner=_scanner(FakeScanner()))
    assert _stored(org, file_id).status == FileStatus.RELEASED


def test_unreachable_real_scanner_counts_as_unavailable() -> None:
    org, _, client = _setup()
    uploaded = _upload(client, "anexo.pdf", file_samples.pdf())
    with pytest.raises(services.ScanRetryError):
        services.process(
            org.id, uuid.UUID(str(uploaded["id"])), scanner=ClamdScanner("127.0.0.1", 1, timeout=1)
        )
    assert _stored(org, uploaded["id"]).status == FileStatus.QUARANTINED


@pytest.mark.parametrize(
    ("name", "content", "reason"),
    [
        ("macro.docx", file_samples.macro_docx(), "macros"),
        ("externo.docx", file_samples.external_reference_docx(), "external_reference"),
        ("bomba.docx", file_samples.zip_bomb_docx(), "zip_bomb"),
        ("travessia.docx", file_samples.traversal_docx(), "invalid_path"),
        ("protegido.pdf", file_samples.encrypted_pdf(), "encrypted"),
        ("script.pdf", file_samples.javascript_pdf(), "active_content"),
        ("falso.pdf", file_samples.docx_file(), "type_mismatch"),
        ("quebrado.pdf", b"%PDF-1.4 sem estrutura", "malformed"),
    ],
)
def test_hostile_files_are_rejected_by_isolated_inspection(
    name: str, content: bytes, reason: str
) -> None:
    org, _, client = _setup()
    uploaded = _upload(client, name, content)
    services.process(org.id, uuid.UUID(str(uploaded["id"])), scanner=_scanner(FakeScanner()))
    stored = _stored(org, uploaded["id"])
    assert stored.status == FileStatus.REJECTED
    assert stored.rejection_reason == reason


def test_tampered_bytes_in_quarantine_are_rejected() -> None:
    org, _, client = _setup()
    uploaded = _upload(client, "anexo.pdf", file_samples.pdf())
    stored = _stored(org, uploaded["id"])
    with default_storage.open(stored.storage_path, "wb") as handle:
        handle.write(file_samples.pdf("outro conteúdo"))
    services.process(org.id, stored.id, scanner=_scanner(FakeScanner()))
    assert _stored(org, stored.id).rejection_reason == "hash_mismatch"


def test_upload_validation_and_limits() -> None:
    org, _, client = _setup()
    assert (
        client.post(
            "/api/v1/files", {"file": SimpleUploadedFile("planilha.xlsx", b"x")}
        ).status_code
        == 400
    )
    assert (
        client.post("/api/v1/files", {"file": SimpleUploadedFile("vazio.pdf", b"")}).status_code
        == 400
    )
    with override_settings(PACTOR_MAX_UPLOAD_BYTES=10):
        assert (
            client.post(
                "/api/v1/files", {"file": SimpleUploadedFile("grande.pdf", b"%PDF-" + b"x" * 20)}
            ).status_code
            == 400
        )
    sanitized = _upload(client, "../../etc/<script>nome.pdf", file_samples.pdf())
    assert sanitized["original_name"] == "scriptnome.pdf"
    stored = _stored(org, sanitized["id"])
    assert stored.storage_path.startswith(f"files/{org.id}/")


def test_storage_quota_is_enforced() -> None:
    _, _, client = _setup()
    _upload(client, "a.pdf", file_samples.pdf())
    Plan.objects.filter(code="teste").update(max_storage_bytes=100)
    response = client.post(
        "/api/v1/files", {"file": SimpleUploadedFile("b.pdf", file_samples.pdf())}
    )
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "plan_limit_exceeded"


def test_quarantined_files_are_not_accessible_to_others_or_attachable() -> None:
    org, manager, client = _setup()
    uploaded = _upload(client, "anexo.pdf", file_samples.pdf())
    contract = factories.contract(org, manager)
    attach = client.post(
        f"/api/v1/contracts/{contract.id}/attachments",
        {"file_id": uploaded["id"]},
        content_type="application/json",
    )
    assert attach.status_code == 409
    other = factories.client_for(factories.member(org, [Role.CONTRACT_MANAGER]), org)
    assert other.get(f"/api/v1/files/{uploaded['id']}").status_code == 404
    other_org, _ = factories.organization()
    outsider = factories.client_for(factories.member(other_org, [Role.CONTRACT_MANAGER]), other_org)
    assert outsider.get(f"/api/v1/files/{uploaded['id']}").status_code == 404


def test_attachments_enter_fingerprint_and_invalidate_approval() -> None:
    org, manager, client = _setup()
    contract = factories.contract(org, manager)
    uploaded = _upload(client, "anexo.pdf", file_samples.pdf())
    services.process(org.id, uuid.UUID(str(uploaded["id"])), scanner=_scanner(FakeScanner()))
    with tenant_context(org.id):
        principal = factories.principal_for_owner(org, manager)
        without = contracts.publish_revision(principal, contract.id, expected_draft_version=1)
        Contract.objects.filter(id=contract.id).update(
            drafting_status="approved",
            approved_revision_id=without.id,
            approved_fingerprint=without.fingerprint,
        )
    attached = client.post(
        f"/api/v1/contracts/{contract.id}/attachments",
        {"file_id": uploaded["id"]},
        content_type="application/json",
    )
    assert attached.status_code == 201, attached.json()
    with tenant_context(org.id):
        stored = Contract.objects.get(id=contract.id)
        assert stored.drafting_status == "draft"
        assert stored.approved_revision_id is None
        principal = factories.principal_for_owner(org, manager)
        with_attachment = contracts.publish_revision(
            principal, contract.id, expected_draft_version=1
        )
    assert with_attachment.fingerprint != without.fingerprint
    assert with_attachment.attachments == [
        {
            "file_id": uploaded["id"],
            "name": "anexo.pdf",
            "sha256": _stored(org, uploaded["id"]).sha256,
        }
    ]
    listing = client.get(f"/api/v1/contracts/{contract.id}/attachments").json()
    assert [a["file"]["id"] for a in listing] == [uploaded["id"]]
    viewer = factories.client_for(factories.member(org, [Role.REVIEWER]), org)
    assert viewer.get(f"/api/v1/files/{uploaded['id']}/content").status_code == 200
    duplicate = client.post(
        f"/api/v1/contracts/{contract.id}/attachments",
        {"file_id": uploaded["id"]},
        content_type="application/json",
    )
    assert duplicate.status_code == 409
    removed = client.delete(f"/api/v1/contracts/{contract.id}/attachments/{listing[0]['id']}")
    assert removed.status_code == 204
    assert (
        client.delete(f"/api/v1/contracts/{contract.id}/attachments/{listing[0]['id']}").status_code
        == 404
    )
    assert viewer.get(f"/api/v1/files/{uploaded['id']}/content").status_code == 404


def test_process_refuses_to_run_inside_transaction_and_ignores_done_files() -> None:
    org, _, client = _setup()
    uploaded = _upload(client, "anexo.pdf", file_samples.pdf())
    file_id = uuid.UUID(str(uploaded["id"]))
    with transaction.atomic(), pytest.raises(RuntimeError, match="transação"):
        services.process(org.id, file_id, scanner=_scanner(FakeScanner()))
    services.process(org.id, file_id, scanner=_scanner(FakeScanner()))
    services.process(org.id, file_id, scanner=_scanner(FakeScanner(infected=True)))
    assert _stored(org, file_id).status == FileStatus.RELEASED


def test_file_task_processes_through_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    org, _, client = _setup()
    uploaded = _upload(client, "anexo.pdf", file_samples.pdf())
    monkeypatch.setattr(services, "default_scanner", lambda: _scanner(FakeScanner()))
    event = OutboxEvent.objects.get(topic="files.received")
    tasks.process_file.apply(kwargs={"message": EventEnvelope.from_model(event).to_message()})
    assert _stored(org, uploaded["id"]).status == FileStatus.RELEASED


@pytest.mark.parametrize(
    ("outcome", "detail"),
    [
        (subprocess.TimeoutExpired(cmd="sandbox", timeout=1), "tempo excedido"),
        (
            subprocess.CompletedProcess(args=[], returncode=-9, stdout=b"", stderr=b""),
            "código de saída -9",
        ),
        (
            subprocess.CompletedProcess(args=[], returncode=0, stdout=b"nao-json", stderr=b""),
            "saída inválida",
        ),
    ],
)
def test_sandbox_resource_failures_become_rejections(
    monkeypatch: pytest.MonkeyPatch, outcome: object, detail: str
) -> None:
    def fake_run(_arguments: list[str], _workdir: str) -> subprocess.CompletedProcess[bytes]:
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, subprocess.CompletedProcess)
        return outcome

    monkeypatch.setattr(services, "_run_isolated", fake_run)
    result = services.run_sandbox(FileKind.PDF, file_samples.pdf())
    assert not result.ok
    assert result.reason is RejectionReason.PROCESSING_LIMIT
    assert result.detail == detail
