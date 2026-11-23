"""Obrigações contratuais e suas ocorrências."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.utils import timezone

from pactor.core.models import TenantModel
from pactor.deadlines.calendar import Frequency


class ObligationCategory(models.TextChoices):
    REPORT = "report", "Entrega de relatório"
    CERTIFICATE = "certificate", "Renovação de certificado"
    DOCUMENTATION = "documentation", "Apresentação de documentação"
    REVIEW = "review", "Revisão periódica"
    MILESTONE = "milestone", "Confirmação de marco"
    OTHER = "other", "Outra"


class ResponsibleParty(models.TextChoices):
    ORGANIZATION = "organization", "Organização"
    COUNTERPARTY = "counterparty", "Contraparte"


class Obligation(TenantModel):
    contract = models.ForeignKey(
        "contracts.Contract", on_delete=models.PROTECT, related_name="obligations"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=16, choices=ObligationCategory.choices)
    responsible = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    responsible_party = models.CharField(max_length=14, choices=ResponsibleParty.choices)
    requires_evidence = models.BooleanField(default=False)
    requires_approval = models.BooleanField(default=False)
    frequency = models.CharField(max_length=10, choices=[(f.value, f.value) for f in Frequency])
    interval = models.PositiveSmallIntegerField(default=1)
    first_due = models.DateField()
    until = models.DateField(null=True, blank=True)
    count = models.PositiveIntegerField(null=True, blank=True)
    reminder_days_before = models.PositiveSmallIntegerField(default=7)
    escalation_days_after = models.PositiveSmallIntegerField(default=3)
    depends_on = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="dependents"
    )
    schedule_version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return self.title


class OccurrenceStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    SUBMITTED = "submitted", "Aguardando aprovação da conclusão"
    COMPLETED = "completed", "Concluída"
    CANCELLED = "cancelled", "Cancelada"


class ObligationOccurrence(TenantModel):
    """Ocorrência estável: identificada por (obrigação, versão do cronograma, sequência)."""

    obligation = models.ForeignKey(Obligation, on_delete=models.PROTECT, related_name="occurrences")
    schedule_version = models.PositiveIntegerField()
    sequence = models.PositiveIntegerField()
    due_date = models.DateField()
    status = models.CharField(
        max_length=10, choices=OccurrenceStatus.choices, default=OccurrenceStatus.PENDING
    )
    evidence = models.ForeignKey(
        "files.StoredFile", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    note = models.CharField(max_length=1000, blank=True)
    submitted_by = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["obligation", "schedule_version", "sequence"],
                name="occurrence_stable_identity",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="completed") | models.Q(completed_at__isnull=False),
                name="occurrence_completed_timestamp",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organization", "due_date"], name="occurrence_due_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.obligation_id}#{self.schedule_version}.{self.sequence}"
