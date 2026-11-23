"""Pipeline de arquivos: recebimento, quarentena, antimalware, validação, extração e liberação.

* ``receive`` (transação da requisição): limites de tamanho, tipo e cota do
  plano; grava os bytes em caminho gerado pelo servidor, com status
  ``quarantined``, e emite evento na outbox.
* ``process`` (worker da fila ``extraction``): lê os bytes, confere o hash,
  consulta o ClamAV e inspeciona o formato em subprocesso isolado, tudo fora
  de transação; grava o resultado em transação curta.
* Scanner indisponível mantém o arquivo em quarentena e agenda nova
  tentativa. Nenhum caminho libera arquivo sem resposta limpa do scanner.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess  # nosec B404 - subprocesso de inspeção com argumentos fixos, sem shell
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePath

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.db.models import F, Sum
from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.audit import services as audit
from pactor.contracts import services as contracts
from pactor.contracts.pdf import PdfDocument, render_pdf
from pactor.contracts.selectors import get_visible_contract, require_editable
from pactor.core import outbox
from pactor.core.errors import ConflictError, DomainValidationError, NotFoundError
from pactor.core.tenancy import require_tenant, tenant_context
from pactor.files.inspection import FileKind, Inspection, RejectionReason, rejection
from pactor.files.models import ContractAttachment, FilePurpose, FileStatus, StoredFile
from pactor.files.scanner import ClamdScanner, ScannerUnavailableError, ScanVerdict
from pactor.obligations.models import ObligationOccurrence
from pactor.plans import services as plans

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf": FileKind.PDF, ".docx": FileKind.DOCX}
CONTENT_TYPES = {
    FileKind.PDF: "application/pdf",
    FileKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
SANDBOX_TIMEOUT_SECONDS = 60
_MAX_NAME = 200


class ScanRetryError(Exception):
    """Scanner indisponível: o arquivo continua em quarentena e a task tenta de novo."""


def _clean_name(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name.strip()
    cleaned = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')
    return cleaned[:_MAX_NAME] or "arquivo"


def storage_used() -> int:
    total = (
        StoredFile.objects.filter(organization_id=require_tenant())
        .exclude(status__in=[FileStatus.REJECTED, FileStatus.INFECTED])
        .aggregate(total=Sum("size"))["total"]
    )
    return int(total or 0)


def receive(principal: Principal, *, name: str, content: bytes, purpose: FilePurpose) -> StoredFile:
    plans.ensure_writable()
    display = _clean_name(name)
    kind = ALLOWED_EXTENSIONS.get(PurePath(display).suffix.lower())
    if kind is None:
        raise DomainValidationError("Formato não aceito. Envie PDF ou DOCX.")
    if not content:
        raise DomainValidationError("Arquivo vazio.")
    if len(content) > settings.PACTOR_MAX_UPLOAD_BYTES:
        raise DomainValidationError(
            "Arquivo acima do tamanho máximo.",
            details={"max_bytes": settings.PACTOR_MAX_UPLOAD_BYTES},
        )
    plans.ensure_capacity(storage_bytes=storage_used() + len(content))
    organization_id = require_tenant()
    file_id = uuid.uuid4()
    path = default_storage.save(f"files/{organization_id}/{file_id}.bin", ContentFile(content))
    stored = StoredFile.objects.create(
        id=file_id,
        organization_id=organization_id,
        original_name=display,
        kind=kind.value,
        purpose=purpose.value,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_path=path,
        uploaded_by_id=principal.actor_id,
    )
    outbox.emit("files.received", {"file_id": str(stored.id)}, organization_id=organization_id)
    audit.record(
        principal,
        action="file.received",
        resource_type="file",
        resource_id=stored.id,
        changes={
            "name": display,
            "kind": kind.value,
            "size": len(content),
            "sha256": stored.sha256,
        },
    )
    return stored


def _run_isolated(arguments: list[str], workdir: str) -> subprocess.CompletedProcess[bytes]:
    """Executa o interpretador atual em modo isolado (-I), sem shell e com ambiente mínimo.

    Os argumentos são fixos (módulo interno, tipo validado e caminho gerado pelo
    servidor); nenhum dado do usuário compõe a linha de comando.
    """
    return subprocess.run(  # noqa: S603  # nosec B603
        arguments,
        capture_output=True,
        timeout=SANDBOX_TIMEOUT_SECONDS,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        cwd=workdir,
    )


def run_sandbox(kind: FileKind, content: bytes) -> Inspection:
    """Inspeciona em subprocesso com limites de recursos; falhas de limite viram rejeição."""
    with tempfile.TemporaryDirectory(prefix="pactor-inspecao-") as workdir:
        source = Path(workdir) / f"entrada.{kind.value}"
        source.write_bytes(content)
        try:
            completed = _run_isolated(
                [sys.executable, "-I", "-m", "pactor.files.sandbox", kind.value, str(source)],
                workdir,
            )
        except subprocess.TimeoutExpired:
            return rejection(RejectionReason.PROCESSING_LIMIT, "tempo excedido")
    if completed.returncode != 0:
        return rejection(
            RejectionReason.PROCESSING_LIMIT, f"código de saída {completed.returncode}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return rejection(RejectionReason.PROCESSING_LIMIT, "saída inválida")
    reason = payload.get("reason")
    return Inspection(
        ok=bool(payload["ok"]),
        reason=RejectionReason(reason) if reason else None,
        detail=str(payload.get("detail", ""))[:300],
        pages=int(payload.get("pages", 0)),
        text=str(payload.get("text", "")),
    )


def default_scanner() -> ClamdScanner:
    return ClamdScanner(settings.CLAMAV_HOST, settings.CLAMAV_PORT)


@dataclass(frozen=True, slots=True)
class _Pending:
    organization_id: uuid.UUID
    file_id: uuid.UUID
    kind: FileKind
    storage_path: str
    sha256: str
    name: str


def _load_pending(organization_id: uuid.UUID, file_id: uuid.UUID) -> _Pending | None:
    with tenant_context(organization_id):
        stored = StoredFile.objects.filter(id=file_id).first()
        if stored is None or stored.status != FileStatus.QUARANTINED:
            return None
        return _Pending(
            organization_id,
            stored.id,
            FileKind(stored.kind),
            stored.storage_path,
            stored.sha256,
            stored.original_name,
        )


def _finish(pending: _Pending, status: FileStatus, **fields: object) -> None:
    with tenant_context(pending.organization_id):
        stored = StoredFile.objects.select_for_update().get(id=pending.file_id)
        if stored.status != FileStatus.QUARANTINED:
            return
        stored.status = status.value
        for key, value in fields.items():
            setattr(stored, key, value)
        stored.updated_at = timezone.now()
        if status is FileStatus.RELEASED:
            stored.released_at = stored.updated_at
        stored.save()
        if status is FileStatus.RELEASED:
            outbox.emit(
                "files.released",
                {"file_id": str(stored.id)},
                organization_id=stored.organization_id,
            )
        audit.record(
            None,
            action=f"file.{status.value}",
            resource_type="file",
            resource_id=stored.id,
            changes={"reason": stored.rejection_reason, "engine": stored.scan_engine},
        )


def process(
    organization_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    scanner: ClamdScanner | None = None,
) -> None:
    if connection.in_atomic_block:
        raise RuntimeError("o processamento de arquivos não pode rodar dentro de transação")
    pending = _load_pending(organization_id, file_id)
    if pending is None:
        return
    with default_storage.open(pending.storage_path, "rb") as handle:
        content = bytes(handle.read())
    if hashlib.sha256(content).hexdigest() != pending.sha256:
        _finish(
            pending,
            FileStatus.REJECTED,
            rejection_reason="hash_mismatch",
            rejection_detail="bytes alterados após o recebimento",
        )
        return
    try:
        result = (scanner or default_scanner()).scan(content)
    except ScannerUnavailableError as exc:
        with tenant_context(organization_id):
            StoredFile.objects.filter(id=file_id, status=FileStatus.QUARANTINED).update(
                scan_attempts=F("scan_attempts") + 1,
                last_error=str(exc)[:500],
                updated_at=timezone.now(),
            )
        raise ScanRetryError(str(exc)) from exc
    if result.verdict is ScanVerdict.INFECTED:
        _finish(
            pending,
            FileStatus.INFECTED,
            rejection_reason="malware",
            rejection_detail=result.signature[:300],
            scan_engine=result.engine[:200],
        )
        return
    inspection = run_sandbox(pending.kind, content)
    if not inspection.ok:
        _finish(
            pending,
            FileStatus.REJECTED,
            rejection_reason=inspection.reason.value if inspection.reason else "rejected",
            rejection_detail=inspection.detail,
            scan_engine=result.engine[:200],
        )
        return
    preview_path = ""
    if pending.kind is FileKind.DOCX:
        preview = render_pdf(
            PdfDocument(
                title=pending.name,
                header_lines=["Pré-visualização (somente texto)"],
                body=inspection.text,
                footer_lines=[],
            )
        )
        preview_path = default_storage.save(
            f"files/{organization_id}/{file_id}-preview.pdf", ContentFile(preview)
        )
    _finish(
        pending,
        FileStatus.RELEASED,
        scan_engine=result.engine[:200],
        page_count=inspection.pages,
        extracted_text=inspection.text,
        preview_path=preview_path,
    )


# ---------------------------------------------------------------------------
# Acesso, anexos e downloads
# ---------------------------------------------------------------------------
def _accessible(principal: Principal, stored: StoredFile) -> bool:
    """Autor do upload, ou quem enxerga um contrato ao qual o arquivo está vinculado.

    Vínculos: anexo ativo do contrato ou evidência de ocorrência de obrigação.
    """
    if stored.uploaded_by_id == principal.actor_id:
        return True
    attached = ContractAttachment.objects.filter(file=stored, removed_at__isnull=True).values_list(
        "contract_id", flat=True
    )
    evidence = ObligationOccurrence.objects.filter(evidence=stored).values_list(
        "obligation__contract_id", flat=True
    )
    for contract_id in sorted({*attached, *evidence}):
        try:
            get_visible_contract(principal, contract_id)
        except NotFoundError:
            continue
        return True
    return False


def get_file(principal: Principal, file_id: uuid.UUID) -> StoredFile:
    stored = StoredFile.objects.filter(
        organization_id=principal.organization_id, id=file_id
    ).first()
    if stored is None or not _accessible(principal, stored):
        raise NotFoundError("Arquivo não encontrado.")
    return stored


@dataclass(frozen=True, slots=True)
class Download:
    content: bytes
    content_type: str
    filename: str
    sha256: str


def download(principal: Principal, file_id: uuid.UUID, *, preview: bool = False) -> Download:
    """Entrega bytes apenas de arquivos liberados; quarentena e rejeitados são inacessíveis."""
    stored = get_file(principal, file_id)
    if stored.status != FileStatus.RELEASED:
        raise ConflictError(
            "Arquivo ainda não liberado para acesso.", details={"status": stored.status}
        )
    if preview:
        path = stored.preview_path or stored.storage_path
        content_type = "application/pdf"
    else:
        path = stored.storage_path
        content_type = CONTENT_TYPES[FileKind(stored.kind)]
    with default_storage.open(path, "rb") as handle:
        content = bytes(handle.read())
    if not preview and hashlib.sha256(content).hexdigest() != stored.sha256:
        raise ConflictError("Integridade do arquivo não confirmada.")
    audit.record(
        principal,
        action="file.downloaded",
        resource_type="file",
        resource_id=stored.id,
        changes={"preview": preview},
    )
    return Download(content, content_type, stored.original_name, stored.sha256)


def attach(
    principal: Principal, contract_id: uuid.UUID, file_id: uuid.UUID, *, include_in_submission: bool
) -> ContractAttachment:
    authorize(principal, Permission.CONTRACTS_EDIT)
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    stored = get_file(principal, file_id)
    if stored.status != FileStatus.RELEASED:
        raise ConflictError("Somente arquivos liberados podem ser anexados.")
    if ContractAttachment.objects.filter(
        contract=contract, file=stored, removed_at__isnull=True
    ).exists():
        raise ConflictError("Arquivo já anexado.")
    attachment = ContractAttachment.objects.create(
        organization_id=contract.organization_id,
        contract=contract,
        file=stored,
        include_in_submission=include_in_submission,
        added_by_id=principal.actor_id,
    )
    if include_in_submission:
        contracts.invalidate_for_content_change(contract, reason="attachments_changed")
    audit.record(
        principal,
        action="attachment.added",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "file_id": str(stored.id),
            "sha256": stored.sha256,
            "in_submission": include_in_submission,
        },
    )
    return attachment


def detach(principal: Principal, contract_id: uuid.UUID, attachment_id: uuid.UUID) -> None:
    authorize(principal, Permission.CONTRACTS_EDIT)
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    attachment = (
        ContractAttachment.objects.filter(
            contract=contract, id=attachment_id, removed_at__isnull=True
        )
        .select_for_update()
        .first()
    )
    if attachment is None:
        raise NotFoundError("Anexo não encontrado.")
    attachment.removed_at = timezone.now()
    attachment.save(update_fields=["removed_at"])
    if attachment.include_in_submission:
        contracts.invalidate_for_content_change(contract, reason="attachments_changed")
    audit.record(
        principal,
        action="attachment.removed",
        resource_type="contract",
        resource_id=contract.id,
        changes={"file_id": str(attachment.file_id)},
    )
