"""Arquivos recebidos, sua situação no pipeline e vínculos com contratos."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.utils import timezone

from pactor.core.models import TenantModel


class FileStatus(models.TextChoices):
    QUARANTINED = "quarantined", "Em quarentena"
    RELEASED = "released", "Liberado"
    REJECTED = "rejected", "Rejeitado na validação"
    INFECTED = "infected", "Bloqueado pelo antimalware"


class FilePurpose(models.TextChoices):
    CONTRACT_ATTACHMENT = "contract_attachment", "Anexo de contrato"
    OBLIGATION_EVIDENCE = "obligation_evidence", "Evidência de obrigação"
    REQUEST_DOCUMENT = "request_document", "Documento de solicitação"


class StoredFile(TenantModel):
    """Arquivo enviado. Só vira acessível depois de ``released``.

    O nome original é guardado apenas para exibição; o caminho no
    armazenamento é sempre gerado pelo servidor.
    """

    original_name = models.CharField(max_length=200)
    kind = models.CharField(max_length=8)
    purpose = models.CharField(max_length=24, choices=FilePurpose.choices)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    storage_path = models.CharField(max_length=300)
    preview_path = models.CharField(max_length=300, blank=True)
    status = models.CharField(
        max_length=12, choices=FileStatus.choices, default=FileStatus.QUARANTINED
    )
    rejection_reason = models.CharField(max_length=40, blank=True)
    rejection_detail = models.CharField(max_length=300, blank=True)
    scan_engine = models.CharField(max_length=200, blank=True)
    scan_attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    page_count = models.PositiveIntegerField(default=0)
    extracted_text = models.TextField(blank=True)
    uploaded_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    released_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.CheckConstraint(
                condition=~models.Q(status="released") | models.Q(released_at__isnull=False),
                name="file_release_timestamp",
            ),
            models.CheckConstraint(
                condition=models.Q(status="released") | models.Q(extracted_text=""),
                name="file_text_only_when_released",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.id}:{self.status}"


class ContractAttachment(TenantModel):
    """Arquivo liberado anexado a um contrato.

    Anexos com ``include_in_submission`` entram no fingerprint da próxima
    revisão: alterá-los muda o conjunto submetido à aprovação.
    """

    contract = models.ForeignKey(
        "contracts.Contract", on_delete=models.PROTECT, related_name="attachments"
    )
    file = models.ForeignKey(StoredFile, on_delete=models.PROTECT, related_name="attachments")
    include_in_submission = models.BooleanField(default=True)
    added_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["contract", "file"],
                condition=models.Q(removed_at__isnull=True),
                name="attachment_active_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contract_id}:{self.file_id}"
