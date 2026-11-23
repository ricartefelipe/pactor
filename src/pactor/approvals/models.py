"""Workflows versionados, rodadas vinculadas à revisão, votos e delegações."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.utils import timezone

from pactor.approvals.domain import Decision, RoundOutcome, StepState
from pactor.core.models import TenantModel


class WorkflowDefinition(TenantModel):
    """Versão publicada de um workflow. Versões não são editadas: nova edição, nova versão."""

    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField()
    rules = models.JSONField()
    prohibit_self_approval = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    published_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["organization", "name", "version"], name="workflow_version_unique"
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(is_active=True),
                name="workflow_single_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


class RoundStatus(models.TextChoices):
    OPEN = RoundOutcome.OPEN.value, "Em andamento"
    APPROVED = RoundOutcome.APPROVED.value, "Aprovada"
    REJECTED = RoundOutcome.REJECTED.value, "Rejeitada"
    CHANGES_REQUESTED = RoundOutcome.CHANGES_REQUESTED.value, "Ajustes solicitados"
    SUPERSEDED = "superseded", "Substituída por nova revisão"


class ApprovalRound(TenantModel):
    """Rodada de aprovação de uma revisão específica e do conjunto exato submetido."""

    contract = models.ForeignKey(
        "contracts.Contract", on_delete=models.PROTECT, related_name="approval_rounds"
    )
    revision = models.ForeignKey("contracts.Revision", on_delete=models.PROTECT, related_name="+")
    fingerprint = models.CharField(max_length=64)
    workflow = models.ForeignKey(
        WorkflowDefinition, on_delete=models.PROTECT, related_name="rounds"
    )
    workflow_version = models.PositiveIntegerField()
    rule_index = models.PositiveSmallIntegerField()
    rule_snapshot = models.JSONField()
    prohibit_self_approval = models.BooleanField()
    status = models.CharField(max_length=20, choices=RoundStatus.choices, default=RoundStatus.OPEN)
    opened_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=60, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["contract"],
                condition=models.Q(status="open"),
                name="round_single_open_per_contract",
            ),
            models.CheckConstraint(
                condition=models.Q(status="open", closed_at__isnull=True)
                | (~models.Q(status="open") & models.Q(closed_at__isnull=False)),
                name="round_closed_consistent",
            ),
        ]

    def __str__(self) -> str:
        return f"rodada:{self.contract_id}:r{self.revision_id}:{self.status}"


class ApprovalStep(TenantModel):
    round = models.ForeignKey(ApprovalRound, on_delete=models.CASCADE, related_name="steps")
    key = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    order = models.PositiveSmallIntegerField()
    quorum = models.PositiveSmallIntegerField()
    state = models.CharField(
        max_length=10,
        choices=[(s.value, s.value) for s in StepState],
        default=StepState.WAITING.value,
    )

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["round", "key"], name="step_key_unique"),
            models.CheckConstraint(condition=models.Q(quorum__gte=1), name="step_quorum_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.round_id}:{self.key}"


class StepApprover(TenantModel):
    """Vaga de aprovador em uma etapa. Substituições encerram a vaga e criam outra."""

    step = models.ForeignKey(ApprovalStep, on_delete=models.CASCADE, related_name="approvers")
    user = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    active = models.BooleanField(default=True)
    replaced_reason = models.CharField(max_length=300, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["step", "user"], name="step_approver_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.step_id}:{self.user_id}"


class Vote(TenantModel):
    """Voto registrado. Uma vaga vota uma vez; uma pessoa vota uma vez por etapa."""

    round = models.ForeignKey(ApprovalRound, on_delete=models.PROTECT, related_name="votes")
    step = models.ForeignKey(ApprovalStep, on_delete=models.PROTECT, related_name="votes")
    approver = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    cast_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    delegation = models.ForeignKey(
        "approvals.Delegation", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decision = models.CharField(max_length=16, choices=[(d.value, d.value) for d in Decision])
    comment = models.TextField(blank=True)
    revision = models.ForeignKey("contracts.Revision", on_delete=models.PROTECT, related_name="+")
    fingerprint = models.CharField(max_length=64)
    cast_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["step", "approver"], name="vote_once_per_slot"),
            models.UniqueConstraint(
                fields=["step", "cast_by"], name="vote_once_per_person_per_step"
            ),
            models.CheckConstraint(
                condition=models.Q(delegation__isnull=True, cast_by=models.F("approver"))
                | (models.Q(delegation__isnull=False) & ~models.Q(cast_by=models.F("approver"))),
                name="vote_delegation_consistent",
            ),
        ]

    def __str__(self) -> str:
        return f"voto:{self.step_id}:{self.approver_id}:{self.decision}"


class Delegation(TenantModel):
    """Delegação temporária de voto (afastamentos). Intervalo semiaberto [início, fim)."""

    delegator = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    delegate = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=300)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")), name="delegation_interval"
            ),
            models.CheckConstraint(
                condition=~models.Q(delegator=models.F("delegate")), name="delegation_not_to_self"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.delegator_id}->{self.delegate_id}"
