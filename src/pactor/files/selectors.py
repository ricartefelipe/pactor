"""Consultas de arquivos usadas por outros contextos."""

from __future__ import annotations

from pactor.contracts.domain import AttachmentDigest
from pactor.contracts.models import Contract
from pactor.files.models import ContractAttachment, FileStatus


def submission_attachments(contract: Contract) -> list[AttachmentDigest]:
    """Anexos liberados que compõem o conjunto submetido do contrato."""
    attachments = (
        ContractAttachment.objects.filter(
            organization_id=contract.organization_id,
            contract=contract,
            removed_at__isnull=True,
            include_in_submission=True,
            file__status=FileStatus.RELEASED,
        )
        .select_related("file")
        .order_by("file_id")
    )
    return [
        AttachmentDigest(str(a.file_id), a.file.original_name, a.file.sha256) for a in attachments
    ]
