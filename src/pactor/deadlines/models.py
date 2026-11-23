"""Calendários de feriados, prazos de contrato, renovação e alertas."""

from __future__ import annotations

from typing import ClassVar

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from pactor.core.models import TenantModel
from pactor.deadlines.calendar import DayCount, Roll


class HolidayCalendar(TenantModel):
    """Feriados configurados pela organização. Não há lista oficial embutida."""

    name = models.CharField(max_length=120)
    is_default = models.BooleanField(default=False)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["organization", "name"], name="holiday_calendar_name_unique"
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(is_default=True),
                name="holiday_calendar_single_default",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Holiday(TenantModel):
    calendar = models.ForeignKey(HolidayCalendar, on_delete=models.CASCADE, related_name="holidays")
    day = models.DateField()
    name = models.CharField(max_length=120)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["calendar", "day"], name="holiday_day_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.day} {self.name}"


class DeadlineKind(models.TextChoices):
    EXPIRY = "expiry", "Vencimento"
    TERMINATION_NOTICE = "termination_notice", "Prazo de aviso"
    NEGOTIATION_WINDOW = "negotiation_window", "Janela de negociação"
    RENEWAL_DECISION = "renewal_decision", "Decisão de renovação"
    ADJUSTMENT = "adjustment", "Reajuste"
    REMINDER = "reminder", "Lembrete"


class DeadlineAnchor(models.TextChoices):
    EFFECTIVE_END = "effective_end", "Término da vigência"
    EFFECTIVE_START = "effective_start", "Início da vigência"
    FIXED = "fixed", "Data fixa"


class DeadlineStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    DONE = "done", "Cumprido"
    CANCELLED = "cancelled", "Cancelado"
    UNSCHEDULED = "unscheduled", "Sem data (âncora indefinida)"


class ContractDeadline(TenantModel):
    """Prazo derivado de uma regra explícita e registrada."""

    contract = models.ForeignKey(
        "contracts.Contract", on_delete=models.PROTECT, related_name="deadlines"
    )
    kind = models.CharField(max_length=20, choices=DeadlineKind.choices)
    title = models.CharField(max_length=200)
    anchor = models.CharField(max_length=16, choices=DeadlineAnchor.choices)
    fixed_date = models.DateField(null=True, blank=True)
    offset_days = models.IntegerField(default=0)
    day_count = models.CharField(
        max_length=10, choices=[(d.value, d.value) for d in DayCount], default=DayCount.CALENDAR
    )
    roll = models.CharField(
        max_length=10, choices=[(r.value, r.value) for r in Roll], default=Roll.NONE
    )
    holiday_calendar = models.ForeignKey(
        HolidayCalendar, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    notify_days_before = models.PositiveSmallIntegerField(default=0)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=DeadlineStatus.choices, default=DeadlineStatus.PENDING
    )
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.CheckConstraint(
                condition=~models.Q(anchor="fixed") | models.Q(fixed_date__isnull=False),
                name="deadline_fixed_has_date",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contract_id}:{self.kind}:{self.due_date}"


class RenewalMode(models.TextChoices):
    NONE = "none", "Sem renovação"
    MANUAL = "manual", "Renovação por decisão"
    AUTOMATIC = "automatic", "Renovação automática autorizada"


class RenewalPolicy(TenantModel):
    contract = models.OneToOneField(
        "contracts.Contract", on_delete=models.PROTECT, related_name="renewal_policy"
    )
    mode = models.CharField(max_length=10, choices=RenewalMode.choices, default=RenewalMode.NONE)
    term_months = models.PositiveSmallIntegerField(default=12)
    max_renewals = models.PositiveSmallIntegerField(null=True, blank=True)
    authorized_by = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    authorized_at = models.DateTimeField(null=True, blank=True)
    non_renewal_registered_at = models.DateTimeField(null=True, blank=True)
    non_renewal_reason = models.CharField(max_length=500, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.CheckConstraint(
                condition=~models.Q(mode="automatic")
                | (models.Q(authorized_by__isnull=False) & models.Q(authorized_at__isnull=False)),
                name="renewal_automatic_requires_authorization",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contract_id}:{self.mode}"


class RenewalRecord(TenantModel):
    """Renovação executada. Somente-inserção: base, resultado e regra usados."""

    contract = models.ForeignKey(
        "contracts.Contract", on_delete=models.PROTECT, related_name="renewals"
    )
    sequence = models.PositiveSmallIntegerField()
    previous_end = models.DateField()
    new_end = models.DateField()
    rule = models.JSONField()

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["contract", "sequence"], name="renewal_sequence_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.contract_id}#{self.sequence}"


class AlertKind(models.TextChoices):
    REMINDER = "reminder", "Lembrete"
    DUE = "due", "Vencimento"
    ESCALATION = "escalation", "Escalonamento"


class AlertStatus(models.TextChoices):
    PENDING = "pending", "Agendado"
    SENT = "sent", "Disparado"
    CANCELLED = "cancelled", "Cancelado"


class Alert(TenantModel):
    """Alerta idempotente: um por (alvo, tipo, data). Disparo recuperável após indisponibilidade."""

    target_type = models.CharField(max_length=20)
    target_id = models.UUIDField()
    contract = models.ForeignKey("contracts.Contract", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=12, choices=AlertKind.choices)
    fire_on = models.DateField()
    fire_at = models.DateTimeField()
    recipient_ids = ArrayField(models.UUIDField(), default=list)
    subject = models.CharField(max_length=200)
    status = models.CharField(
        max_length=10, choices=AlertStatus.choices, default=AlertStatus.PENDING
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["target_type", "target_id", "kind", "fire_on"], name="alert_once"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["fire_at"], name="alert_pending_idx", condition=models.Q(status="pending")
            ),
        ]

    def __str__(self) -> str:
        return f"{self.target_type}:{self.kind}:{self.fire_on}"
