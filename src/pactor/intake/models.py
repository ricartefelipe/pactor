"""Solicitações de contrato e seu histórico."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.utils import timezone

from pactor.contracts.models import CONTRACT_TYPE_CHOICES
from pactor.core.models import TenantModel


class Priority(models.TextChoices):
    LOW = "low", "Baixa"
    NORMAL = "normal", "Normal"
    HIGH = "high", "Alta"
    URGENT = "urgent", "Urgente"


class RequestStatus(models.TextChoices):
    SUBMITTED = "submitted", "Enviada"
    IN_REVIEW = "in_review", "Em análise"
    REJECTED = "rejected", "Recusada"
    CANCELLED = "cancelled", "Cancelada"
    CONVERTED = "converted", "Convertida em contrato"


class ContractRequest(TenantModel):
    reference = models.CharField(max_length=20)
    requester = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey(
        "identity.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    contract_type = models.CharField(max_length=12, choices=CONTRACT_TYPE_CHOICES)
    counterparty = models.ForeignKey(
        "counterparties.Counterparty",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    title = models.CharField(max_length=200)
    objective = models.TextField()
    estimated_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    desired_start = models.DateField(null=True, blank=True)
    desired_end = models.DateField(null=True, blank=True)
    assignee = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(
        max_length=10, choices=RequestStatus.choices, default=RequestStatus.SUBMITTED
    )
    contract = models.OneToOneField(
        "contracts.Contract",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="origin_request",
    )
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["organization", "reference"], name="request_reference_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(estimated_value__isnull=True) | models.Q(estimated_value__gte=0),
                name="request_value_non_negative",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="converted") | models.Q(contract__isnull=False),
                name="request_converted_has_contract",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} {self.title}"


class RequestEvent(TenantModel):
    """Histórico somente-inserção da solicitação."""

    request = models.ForeignKey(ContractRequest, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=40)
    from_status = models.CharField(max_length=10, blank=True)
    to_status = models.CharField(max_length=10, blank=True)
    note = models.CharField(max_length=500, blank=True)

    def __str__(self) -> str:
        return f"{self.request_id}:{self.action}"


class RequestSequence(TenantModel):
    """Numerador anual de solicitações por organização (bloqueado na geração)."""

    year = models.PositiveSmallIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["organization", "year"], name="request_sequence_year"),
        ]

    def __str__(self) -> str:
        return f"{self.year}:{self.last_value}"
