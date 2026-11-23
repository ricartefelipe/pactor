"""Valores contratados, parcelas e marcos, índices, reajustes e confirmações.

O módulo acompanha informações contratuais: não há integração bancária nem
conciliação. Confirmações registram o que foi informado por um usuário.
"""

from __future__ import annotations

from typing import ClassVar

from django.db import models

from pactor.core.models import TenantModel


class IndexSeries(TenantModel):
    """Série de índice de reajuste cadastrada pela organização, com a fonte declarada."""

    name = models.CharField(max_length=80)
    source = models.CharField("fonte declarada", max_length=200)
    is_demonstrative = models.BooleanField(
        default=False, help_text="Dados de exemplo, não oficiais."
    )

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["organization", "name"], name="index_series_name_unique"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class IndexValue(TenantModel):
    """Valor percentual de um período (primeiro dia do mês). Somente-inserção."""

    series = models.ForeignKey(IndexSeries, on_delete=models.PROTECT, related_name="values")
    period = models.DateField()
    rate_percent = models.DecimalField(max_digits=12, decimal_places=6)
    source_note = models.CharField(max_length=300)
    registered_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["series", "period"], name="index_value_period_unique"),
            models.CheckConstraint(condition=models.Q(period__day=1), name="index_value_first_day"),
        ]

    def __str__(self) -> str:
        return f"{self.series_id}:{self.period}:{self.rate_percent}"


class InstallmentKind(models.TextChoices):
    INSTALLMENT = "installment", "Parcela"
    MILESTONE = "milestone", "Marco"


class InstallmentStatus(models.TextChoices):
    PLANNED = "planned", "Prevista"
    CONFIRMED = "confirmed", "Confirmada"
    CANCELLED = "cancelled", "Cancelada"


class Installment(TenantModel):
    """Compromisso previsto (projeção) de pagamento ou recebimento."""

    contract = models.ForeignKey(
        "contracts.Contract", on_delete=models.PROTECT, related_name="installments"
    )
    sequence = models.PositiveIntegerField()
    kind = models.CharField(max_length=12, choices=InstallmentKind.choices)
    description = models.CharField(max_length=200)
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="BRL")
    status = models.CharField(
        max_length=10, choices=InstallmentStatus.choices, default=InstallmentStatus.PLANNED
    )
    version = models.PositiveIntegerField(default=1)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["contract", "sequence"], name="installment_sequence_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="installment_amount_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(currency="BRL"), name="installment_currency_brl"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contract_id}#{self.sequence}"


class PaymentConfirmation(TenantModel):
    """Confirmação registrada de uma parcela. Somente-inserção: não muda depois."""

    installment = models.OneToOneField(
        Installment, on_delete=models.PROTECT, related_name="confirmation"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    confirmed_on = models.DateField()
    reference = models.CharField("referência informada", max_length=120)
    confirmed_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="confirmation_amount_non_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"confirmação:{self.installment_id}"


class AdjustmentRecord(TenantModel):
    """Reajuste aplicado: índice, período, taxa e efeito em cada parcela futura."""

    contract = models.ForeignKey(
        "contracts.Contract", on_delete=models.PROTECT, related_name="adjustments"
    )
    index_value = models.ForeignKey(IndexValue, on_delete=models.PROTECT, related_name="+")
    rate_percent = models.DecimalField(max_digits=12, decimal_places=6)
    applies_from = models.DateField()
    affected = models.JSONField(help_text="Lista de {installment_id, before, after}.")
    applied_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            # O mesmo valor de índice reajusta um contrato uma única vez.
            models.UniqueConstraint(
                fields=["contract", "index_value"], name="adjustment_once_per_index_value"
            ),
        ]

    def __str__(self) -> str:
        return f"reajuste:{self.contract_id}:{self.applies_from}"
