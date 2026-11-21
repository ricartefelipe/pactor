"""Modelos de infraestrutura compartilhados: base de tenant, outbox e idempotência."""

from __future__ import annotations

import uuid
from typing import ClassVar

from django.db import models
from django.utils import timezone


class TenantModel(models.Model):
    """Base de toda entidade pertencente a uma organização.

    A restrição ``(organization, id)`` única é o alvo das FKs compostas que
    impedem referências entre organizações (ver ``core.db.AddTenantForeignKey``).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "identity.Organization",
        on_delete=models.PROTECT,
        related_name="+",
        editable=False,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        abstract = True
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organization", "id"],
                name="%(app_label)s_%(class)s_tenant_key",
            ),
        ]


class OutboxStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    CLAIMED = "claimed", "Em publicação"
    PUBLISHED = "published", "Publicado"
    DEAD = "dead", "Esgotado"


class OutboxEvent(models.Model):
    """Evento de negócio gravado na mesma transação da alteração que o originou.

    A tabela é global (sem RLS) porque o relay publica eventos de todas as
    organizações; o payload contém apenas identificadores, e os consumidores
    reconstituem o contexto de tenant antes de ler qualquer entidade.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization_id = models.UUIDField(null=True, blank=True, db_index=True)
    topic = models.CharField(max_length=100)
    payload = models.JSONField()
    payload_version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(
        max_length=12, choices=OutboxStatus.choices, default=OutboxStatus.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    claimed_until = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["available_at"],
                name="outbox_pending_idx",
                condition=models.Q(status__in=["pending", "claimed"]),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.topic}:{self.id}"


class ConsumedEvent(models.Model):
    """Deduplicação: cada consumidor aplica cada evento no máximo uma vez."""

    consumer = models.CharField(max_length=100)
    event_id = models.UUIDField()
    organization_id = models.UUIDField(null=True, blank=True)
    consumed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["consumer", "event_id"], name="consumed_event_once"),
        ]

    def __str__(self) -> str:
        return f"{self.consumer}:{self.event_id}"


class ConsumerFailureStatus(models.TextChoices):
    RETRYING = "retrying", "Em retentativa"
    FAILED = "failed", "Falhou"
    REQUEUED = "requeued", "Reenfileirado"
    RESOLVED = "resolved", "Resolvido"


class ConsumerFailure(models.Model):
    """Registro de falha de consumo, base para reprocessamento autorizado."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consumer = models.CharField(max_length=100)
    event = models.ForeignKey(OutboxEvent, on_delete=models.PROTECT, related_name="failures")
    organization_id = models.UUIDField(null=True, blank=True, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=ConsumerFailureStatus.choices)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["consumer", "event"], name="consumer_failure_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.consumer}:{self.event_id}:{self.status}"


class IdempotencyRecord(TenantModel):
    """Resultado persistido de uma operação sensível identificada por chave."""

    actor_id = models.UUIDField()
    scope = models.CharField(max_length=100)
    key = models.CharField(max_length=128)
    fingerprint = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(null=True)
    response_body = models.JSONField(null=True)
    completed_at = models.DateTimeField(null=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["organization", "actor_id", "scope", "key"],
                name="idempotency_key_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scope}:{self.key}"
