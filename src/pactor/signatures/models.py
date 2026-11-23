"""Envelopes de assinatura, participantes, documentos e eventos recebidos."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.utils import timezone

from pactor.core.models import TenantModel
from pactor.signatures.ports import EnvelopeStatus, RecipientStatus


class EnvelopeState(models.TextChoices):
    PENDING_DISPATCH = "pending_dispatch", "Aguardando envio"
    DISPATCH_UNKNOWN = "dispatch_unknown", "Envio com resultado desconhecido"
    SENT = EnvelopeStatus.SENT.value, "Enviado"
    DELIVERED = EnvelopeStatus.DELIVERED.value, "Entregue"
    COMPLETED = EnvelopeStatus.COMPLETED.value, "Concluído"
    DECLINED = EnvelopeStatus.DECLINED.value, "Recusado"
    VOIDING = "voiding", "Cancelamento solicitado"
    VOIDED = EnvelopeStatus.VOIDED.value, "Cancelado"
    FAILED = "failed", "Falhou"


ACTIVE_ENVELOPE_STATES = (
    EnvelopeState.PENDING_DISPATCH,
    EnvelopeState.DISPATCH_UNKNOWN,
    EnvelopeState.SENT,
    EnvelopeState.DELIVERED,
    EnvelopeState.VOIDING,
)


class Envelope(TenantModel):
    """Envelope enviado ao provedor para uma revisão aprovada e estável.

    O ``id`` do envelope é a referência idempotente enviada ao provedor; o
    envelope nunca muda de revisão.
    """

    contract = models.ForeignKey(
        "contracts.Contract", on_delete=models.PROTECT, related_name="envelopes"
    )
    revision = models.ForeignKey("contracts.Revision", on_delete=models.PROTECT, related_name="+")
    fingerprint = models.CharField(max_length=64)
    provider = models.CharField(max_length=20)
    provider_account_id = models.CharField(max_length=80)
    provider_envelope_id = models.CharField(max_length=80, blank=True)
    state = models.CharField(
        max_length=20, choices=EnvelopeState.choices, default=EnvelopeState.PENDING_DISPATCH
    )
    subject = models.CharField(max_length=200)
    dispatch_attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["contract"],
                condition=models.Q(state__in=[s.value for s in ACTIVE_ENVELOPE_STATES]),
                name="envelope_single_active_per_contract",
            ),
            models.UniqueConstraint(
                fields=["provider", "provider_account_id", "provider_envelope_id"],
                condition=~models.Q(provider_envelope_id=""),
                name="envelope_provider_id_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"envelope:{self.id}:{self.state}"


class Recipient(TenantModel):
    envelope = models.ForeignKey(Envelope, on_delete=models.CASCADE, related_name="recipients")
    recipient_key = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    email = models.EmailField()
    routing_order = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=12,
        choices=[(s.value, s.value) for s in RecipientStatus],
        default=RecipientStatus.CREATED.value,
    )
    changed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["envelope", "recipient_key"], name="recipient_key_unique"
            ),
            models.UniqueConstraint(fields=["envelope", "email"], name="recipient_email_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.envelope_id}:{self.recipient_key}:{self.status}"


class DocumentKind(models.TextChoices):
    SENT = "sent", "Documento enviado"
    SIGNED = "signed", "Documento assinado retornado"
    EVIDENCE = "evidence", "Evidências do provedor"


class EnvelopeDocument(TenantModel):
    """Bytes preservados separadamente: o que foi enviado e o que voltou assinado."""

    envelope = models.ForeignKey(Envelope, on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField(max_length=10, choices=DocumentKind.choices)
    name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=80)
    storage_path = models.CharField(max_length=300)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["envelope", "kind"],
                condition=models.Q(kind="sent"),
                name="envelope_single_sent_document",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.envelope_id}:{self.kind}:{self.sha256[:12]}"


class EnvelopeRoute(models.Model):
    """Roteamento de webhooks: id do provedor → organização e envelope.

    Global (sem RLS) porque o webhook chega sem contexto de organização. Contém
    apenas identificadores; o processamento do evento acontece no tenant.
    """

    provider = models.CharField(max_length=20)
    provider_account_id = models.CharField(max_length=80)
    provider_envelope_id = models.CharField(max_length=80)
    organization_id = models.UUIDField()
    envelope_id = models.UUIDField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["provider", "provider_account_id", "provider_envelope_id"],
                name="envelope_route_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.provider_envelope_id}"


class EventOutcome(models.TextChoices):
    PENDING = "pending", "Aguardando processamento"
    APPLIED = "applied", "Aplicado"
    STALE = "stale", "Ignorado: fora de ordem ou anterior"
    OLD_ENVELOPE = "old_envelope", "Ignorado: envelope não vigente"
    UNKNOWN_ENVELOPE = "unknown_envelope", "Ignorado: envelope desconhecido"


class SignatureEvent(models.Model):
    """Inbox de eventos do provedor. ``dedup_key`` único descarta repetições."""

    provider = models.CharField(max_length=20)
    dedup_key = models.CharField(max_length=128)
    provider_envelope_id = models.CharField(max_length=80)
    organization_id = models.UUIDField(null=True, blank=True)
    envelope_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=20)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(
        max_length=20, choices=EventOutcome.choices, default=EventOutcome.PENDING
    )
    state = models.JSONField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["provider", "dedup_key"], name="signature_event_dedup"),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["received_at"],
                name="signature_event_pending_idx",
                condition=models.Q(outcome="pending"),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.dedup_key}:{self.outcome}"


class SimulatedEnvelope(models.Model):
    """Estado do lado do provedor no simulador local (não é dado de cliente).

    Existe apenas para o adaptador de simulação e nunca é usado em produção.
    """

    provider_envelope_id = models.CharField(max_length=80, primary_key=True)
    account_id = models.CharField(max_length=80)
    reference = models.CharField(max_length=80)
    subject = models.CharField(max_length=200)
    document = models.BinaryField()
    document_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=20)
    recipients = models.JSONField()
    sequence = models.PositiveIntegerField(default=0)
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["account_id", "reference"], name="simulated_reference_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"simulador:{self.provider_envelope_id}:{self.status}"
