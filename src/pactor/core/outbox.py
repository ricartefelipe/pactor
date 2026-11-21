"""Transactional Outbox.

Fluxo:

1. :func:`emit` grava o evento na mesma transação da alteração de negócio.
   ``transaction.on_commit`` apenas antecipa o relay; a recuperação durável
   não depende dele.
2. O relay (task periódica) **reivindica** um lote em transação curta,
   marcando os eventos como ``claimed`` com prazo de concessão.
3. A publicação no broker acontece **fora** de transação.
4. O resultado é gravado em nova transação curta. Se o processo cair entre a
   publicação e a marcação, a concessão expira e o evento é republicado; os
   consumidores deduplicam pelo identificador estável do evento.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from pactor.core.models import OutboxEvent, OutboxStatus

logger = logging.getLogger(__name__)

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

MAX_PUBLISH_ATTEMPTS = 12
CLAIM_LEASE = timedelta(seconds=60)
_BACKOFF_BASE_SECONDS = 2
_BACKOFF_CAP_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Representação serializável (JSON) de um evento publicado."""

    id: uuid.UUID
    topic: str
    organization_id: uuid.UUID | None
    payload: Mapping[str, JsonValue]
    payload_version: int

    def to_message(self) -> dict[str, JsonValue]:
        return {
            "id": str(self.id),
            "topic": self.topic,
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "payload": dict(self.payload),
            "payload_version": self.payload_version,
        }

    @classmethod
    def from_message(cls, message: Mapping[str, object]) -> EventEnvelope:
        org = message.get("organization_id")
        payload = message.get("payload")
        version = message.get("payload_version")
        if not isinstance(payload, dict) or not isinstance(version, int):
            raise TypeError("mensagem de evento malformada")
        return cls(
            id=uuid.UUID(str(message["id"])),
            topic=str(message["topic"]),
            organization_id=uuid.UUID(str(org)) if org else None,
            payload=payload,
            payload_version=version,
        )

    @classmethod
    def from_model(cls, event: OutboxEvent) -> EventEnvelope:
        return cls(
            id=event.id,
            topic=event.topic,
            organization_id=event.organization_id,
            payload=event.payload,
            payload_version=event.payload_version,
        )


class OutboxRequiresTransactionError(RuntimeError):
    """``emit`` chamado fora de transação: o evento não ficaria atômico."""


def emit(
    topic: str,
    payload: Mapping[str, JsonValue],
    *,
    organization_id: uuid.UUID | None,
    payload_version: int = 1,
    on_commit: Callable[[], None] | None = None,
) -> OutboxEvent:
    if not connection.in_atomic_block:
        raise OutboxRequiresTransactionError(topic)
    event = OutboxEvent.objects.create(
        topic=topic,
        payload=dict(payload),
        payload_version=payload_version,
        organization_id=organization_id,
    )
    if on_commit is not None:
        transaction.on_commit(on_commit)
    return event


def backoff_for(attempts: int) -> timedelta:
    """Backoff exponencial limitado: 2s, 4s, 8s... até 15 minutos."""
    seconds = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS ** max(1, attempts))
    return timedelta(seconds=seconds)


def claim_batch(*, limit: int = 100, now: datetime | None = None) -> list[EventEnvelope]:
    moment = now or timezone.now()
    with transaction.atomic():
        due = (
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=OutboxStatus.PENDING, available_at__lte=moment)
                | Q(status=OutboxStatus.CLAIMED, claimed_until__lt=moment)
            )
            .order_by("available_at", "created_at", "id")[:limit]
        )
        events = list(due)
        if not events:
            return []
        OutboxEvent.objects.filter(id__in=[e.id for e in events]).update(
            status=OutboxStatus.CLAIMED,
            claimed_until=moment + CLAIM_LEASE,
            attempts=F("attempts") + 1,
        )
    return [EventEnvelope.from_model(e) for e in events]


def mark_published(event_id: uuid.UUID, *, now: datetime | None = None) -> None:
    OutboxEvent.objects.filter(id=event_id, status=OutboxStatus.CLAIMED).update(
        status=OutboxStatus.PUBLISHED,
        published_at=now or timezone.now(),
        claimed_until=None,
        last_error="",
    )


def mark_failed(event_id: uuid.UUID, error: str, *, now: datetime | None = None) -> None:
    moment = now or timezone.now()
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(id=event_id)
        if event.status != OutboxStatus.CLAIMED:
            return
        event.last_error = error[:2000]
        event.claimed_until = None
        if event.attempts >= MAX_PUBLISH_ATTEMPTS:
            event.status = OutboxStatus.DEAD
            logger.error("outbox_event_dead", extra={"event_id": str(event.id)})
        else:
            event.status = OutboxStatus.PENDING
            event.available_at = moment + backoff_for(event.attempts)
        event.save(update_fields=["last_error", "claimed_until", "status", "available_at"])


class PublishError(Exception):
    """Falha esperada ao publicar no broker (indisponibilidade, timeout)."""


type Publisher = Callable[[EventEnvelope], None]


@dataclass(frozen=True, slots=True)
class RelayResult:
    published: int
    failed: int


def relay_once(publisher: Publisher, *, limit: int = 100) -> RelayResult:
    """Publica um lote de eventos devidos. Seguro para execução concorrente."""
    published = failed = 0
    for envelope in claim_batch(limit=limit):
        try:
            publisher(envelope)
        except PublishError as exc:
            failed += 1
            mark_failed(envelope.id, str(exc))
        else:
            published += 1
            mark_published(envelope.id)
    return RelayResult(published=published, failed=failed)


def requeue_dead(event_ids: Sequence[uuid.UUID]) -> int:
    """Devolve eventos esgotados à fila (uso exclusivo do reprocessamento autorizado)."""
    return OutboxEvent.objects.filter(id__in=event_ids, status=OutboxStatus.DEAD).update(
        status=OutboxStatus.PENDING, attempts=0, available_at=timezone.now(), last_error=""
    )
