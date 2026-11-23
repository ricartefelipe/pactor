"""Contratos, rascunho editável, revisões imutáveis, comentários e concessões."""

from __future__ import annotations

from typing import ClassVar

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils import timezone

from pactor.access.policies import Confidentiality
from pactor.contracts.domain import CONTRACT_TYPE_LABELS, ContractType, DraftingStatus, PartyRole
from pactor.contracts.lifecycle import ConditionField, LifecycleStatus
from pactor.core.models import TenantModel

CONTRACT_TYPE_CHOICES = [(t.value, CONTRACT_TYPE_LABELS[t]) for t in ContractType]
CONFIDENTIALITY_CHOICES = [
    (Confidentiality.INTERNAL.value, "Interno"),
    (Confidentiality.RESTRICTED.value, "Restrito"),
    (Confidentiality.CONFIDENTIAL.value, "Confidencial"),
]
DRAFTING_CHOICES = [
    (DraftingStatus.DRAFT.value, "Em elaboração"),
    (DraftingStatus.IN_APPROVAL.value, "Em aprovação"),
    (DraftingStatus.APPROVED.value, "Aprovado"),
    (DraftingStatus.SIGNING.value, "Em assinatura"),
    (DraftingStatus.SIGNED.value, "Assinado"),
    (DraftingStatus.CANCELLED.value, "Cancelado"),
]
PARTY_ROLE_CHOICES = [
    (PartyRole.CONTRACTOR.value, "Contratante"),
    (PartyRole.CONTRACTED.value, "Contratada"),
    (PartyRole.LICENSOR.value, "Licenciante"),
    (PartyRole.LICENSEE.value, "Licenciada"),
    (PartyRole.DISCLOSING.value, "Parte reveladora"),
    (PartyRole.RECEIVING.value, "Parte receptora"),
    (PartyRole.MUTUAL.value, "Recíproca"),
]


LIFECYCLE_CHOICES = [
    (LifecycleStatus.NOT_STARTED.value, "Sem vigência"),
    (LifecycleStatus.AWAITING_START.value, "Aguardando início"),
    (LifecycleStatus.ACTIVE.value, "Vigente"),
    (LifecycleStatus.SUSPENDED.value, "Suspenso"),
    (LifecycleStatus.ENDED.value, "Encerrado"),
    (LifecycleStatus.TERMINATED.value, "Rescindido"),
    (LifecycleStatus.ARCHIVED.value, "Arquivado"),
]


class ContractSequence(TenantModel):
    """Numerador anual de referências por organização (bloqueado na geração)."""

    year = models.PositiveSmallIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["organization", "year"], name="contract_sequence_year"),
        ]

    def __str__(self) -> str:
        return f"{self.year}:{self.last_value}"


class Contract(TenantModel):
    reference = models.CharField(max_length=20)
    title = models.CharField(max_length=200)
    contract_type = models.CharField(max_length=12, choices=CONTRACT_TYPE_CHOICES)
    counterparty = models.ForeignKey(
        "counterparties.Counterparty", on_delete=models.PROTECT, related_name="contracts"
    )
    organization_role = models.CharField(max_length=12, choices=PARTY_ROLE_CHOICES)
    counterparty_role = models.CharField(max_length=12, choices=PARTY_ROLE_CHOICES)
    department = models.ForeignKey(
        "identity.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    owner = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    requester = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    confidentiality = models.CharField(
        max_length=12, choices=CONFIDENTIALITY_CHOICES, default=Confidentiality.INTERNAL.value
    )
    classification = models.CharField(
        "classificação informada",
        max_length=60,
        blank=True,
        help_text="Usada por regras de aprovação.",
    )
    tags = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    total_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="BRL")
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    drafting_status = models.CharField(
        max_length=12, choices=DRAFTING_CHOICES, default=DraftingStatus.DRAFT.value
    )
    current_revision = models.ForeignKey(
        "contracts.Revision", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_revision = models.ForeignKey(
        "contracts.Revision", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_fingerprint = models.CharField(max_length=64, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="derived"
    )
    # Vigência: independente da elaboração e da assinatura.
    lifecycle_status = models.CharField(
        max_length=16, choices=LIFECYCLE_CHOICES, default=LifecycleStatus.NOT_STARTED.value
    )
    signed_on = models.DateField(null=True, blank=True)
    effective_start = models.DateField(null=True, blank=True)
    effective_end = models.DateField(null=True, blank=True, help_text="Vazio: prazo indeterminado.")
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["organization", "reference"], name="contract_reference_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(currency="BRL"), name="contract_currency_brl"
            ),
            models.CheckConstraint(
                condition=models.Q(total_value__isnull=True) | models.Q(total_value__gte=0),
                name="contract_value_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(planned_end__isnull=True)
                | models.Q(planned_start__isnull=True)
                | models.Q(planned_end__gte=models.F("planned_start")),
                name="contract_planned_dates_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(approved_revision__isnull=True)
                | ~models.Q(approved_fingerprint=""),
                name="contract_approval_has_fingerprint",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_end__isnull=True)
                | models.Q(effective_start__isnull=True)
                | models.Q(effective_end__gte=models.F("effective_start")),
                name="contract_effective_dates_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(lifecycle_status="not_started")
                | models.Q(drafting_status="signed"),
                name="contract_lifecycle_requires_signature",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organization", "drafting_status"], name="contract_status_idx"),
            GinIndex(fields=["tags"], name="contract_tags_gin"),
        ]

    def __str__(self) -> str:
        return f"{self.reference} {self.title}"


class ContractDraft(TenantModel):
    """Conteúdo em edição. Concorrência otimista pelo campo ``version``."""

    contract = models.OneToOneField(Contract, on_delete=models.CASCADE, related_name="draft")
    content = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"rascunho:{self.contract_id}:v{self.version}"


class RevisionSource(models.TextChoices):
    MANUAL = "manual", "Edição manual"
    TEMPLATE = "template", "Gerada por modelo"


class Revision(TenantModel):
    """Revisão publicada: imutável (trigger no banco impede UPDATE e DELETE)."""

    contract = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="revisions")
    number = models.PositiveIntegerField()
    previous = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    source = models.CharField(max_length=10, choices=RevisionSource.choices)
    content = models.TextField()
    content_sha256 = models.CharField(max_length=64)
    metadata = models.JSONField()
    attachments = models.JSONField(default=list)
    fingerprint = models.CharField(max_length=64)
    author = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    summary = models.CharField("resumo da alteração", max_length=300, blank=True)
    template_version_id = models.UUIDField(null=True, blank=True)
    generation_data = models.JSONField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["contract", "number"], name="revision_number_unique"),
        ]
        ordering = ("contract", "number")

    def __str__(self) -> str:
        return f"{self.contract_id}#r{self.number}"


class Comment(TenantModel):
    revision = models.ForeignKey(Revision, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    body = models.TextField()
    line = models.PositiveIntegerField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    def __str__(self) -> str:
        return f"comentário:{self.revision_id}"


class GrantSource(models.TextChoices):
    MANUAL = "manual", "Compartilhamento"
    APPROVAL = "approval", "Participação em aprovação"


class ContractGrant(TenantModel):
    """Vínculo explícito de um usuário ou equipe a um contrato."""

    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name="grants")
    user = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    team = models.ForeignKey(
        "identity.Team", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    can_edit = models.BooleanField(default=False)
    source = models.CharField(
        max_length=10, choices=GrantSource.choices, default=GrantSource.MANUAL
    )
    source_id = models.UUIDField(null=True, blank=True)
    granted_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.CheckConstraint(
                condition=(models.Q(user__isnull=False) & models.Q(team__isnull=True))
                | (models.Q(user__isnull=True) & models.Q(team__isnull=False)),
                name="grant_user_xor_team",
            ),
            models.CheckConstraint(
                condition=~models.Q(source="approval")
                | (models.Q(user__isnull=False) & models.Q(can_edit=False)),
                name="grant_approval_is_user_read_only",
            ),
            models.UniqueConstraint(
                fields=["contract", "user", "source", "source_id"],
                condition=models.Q(user__isnull=False),
                nulls_distinct=False,
                name="grant_user_unique",
            ),
            models.UniqueConstraint(
                fields=["contract", "team"],
                condition=models.Q(team__isnull=False),
                name="grant_team_unique",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["contract", "user"], name="grant_contract_user_idx"),
        ]

    def __str__(self) -> str:
        return f"grant:{self.contract_id}"


class LifecyclePolicy(TenantModel):
    """Política de vigência da organização (ex.: se suspensões são permitidas)."""

    allow_suspension = models.BooleanField(default=True)
    max_suspension_days = models.PositiveIntegerField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["organization"], name="lifecycle_policy_per_organization"
            ),
        ]

    def __str__(self) -> str:
        return f"política:{self.organization_id}"


class LifecycleEvent(TenantModel):
    """Histórico somente-inserção da vigência, com justificativa e data de efeito."""

    contract = models.ForeignKey(
        Contract, on_delete=models.PROTECT, related_name="lifecycle_events"
    )
    action = models.CharField(max_length=24)
    from_status = models.CharField(max_length=16)
    to_status = models.CharField(max_length=16)
    effective_date = models.DateField()
    reason = models.CharField(max_length=500, blank=True)
    actor = models.ForeignKey(
        "identity.User", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    def __str__(self) -> str:
        return f"{self.contract_id}:{self.action}"


class AmendmentChange(TenantModel):
    """Alteração de condição proposta por um aditivo (vale após a assinatura do aditivo)."""

    amendment = models.ForeignKey(
        Contract, on_delete=models.PROTECT, related_name="amendment_changes"
    )
    parent = models.ForeignKey(Contract, on_delete=models.PROTECT, related_name="incoming_changes")
    field = models.CharField(max_length=20, choices=[(f.value, f.value) for f in ConditionField])
    value = models.CharField(max_length=500, blank=True)
    effective_from = models.DateField()
    created_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")

    def __str__(self) -> str:
        return f"{self.amendment_id}:{self.field}"
