"""Reprocessamento autorizado de falhas de consumidores e de eventos esgotados.

Somente quem tem ``operations.reprocess`` (com MFA) e apenas para falhas da
própria organização. O reprocessamento republica o evento original, com o
mesmo identificador: a deduplicação do consumidor continua valendo.
"""

from __future__ import annotations

import uuid

from django.db.models import QuerySet
from django.utils import timezone
from kombu.exceptions import KombuError

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.audit import services as audit
from pactor.config.celery import app
from pactor.config.events import ROUTES
from pactor.core.errors import ConflictError, NotFoundError
from pactor.core.models import ConsumerFailure, ConsumerFailureStatus, OutboxEvent, OutboxStatus
from pactor.core.outbox import EventEnvelope, requeue_dead


class RequeueUnavailableError(ConflictError):
    code = "broker_unavailable"


def failures_for(principal: Principal) -> QuerySet[ConsumerFailure]:
    authorize(principal, Permission.OPERATIONS_REPROCESS)
    return (
        ConsumerFailure.objects.filter(organization_id=principal.organization_id)
        .select_related("event")
        .order_by("-updated_at", "id")
    )


def requeue_failure(principal: Principal, failure_id: uuid.UUID) -> ConsumerFailure:
    authorize(principal, Permission.OPERATIONS_REPROCESS)
    failure = (
        ConsumerFailure.objects.select_for_update()
        .select_related("event")
        .filter(organization_id=principal.organization_id, id=failure_id)
        .first()
    )
    if failure is None:
        raise NotFoundError("Falha não encontrada.")
    if failure.status != ConsumerFailureStatus.FAILED:
        raise ConflictError(
            "Somente falhas definitivas podem ser reprocessadas.",
            details={"status": failure.status},
        )
    route = next(
        (r for r in ROUTES.get(failure.event.topic, ()) if r.consumer == failure.consumer), None
    )
    if route is None:
        raise ConflictError("Consumidor não está mais roteado para este evento.")
    try:
        app.send_task(
            route.consumer,
            kwargs={"message": EventEnvelope.from_model(failure.event).to_message()},
            queue=route.queue,
            task_id=f"{failure.event_id}:{route.consumer}:reprocesso:{failure.attempts}",
            retry=True,
            retry_policy={"max_retries": 2},
        )
    except (KombuError, OSError) as exc:
        raise RequeueUnavailableError("Fila indisponível; tente novamente.") from exc
    failure.status = ConsumerFailureStatus.REQUEUED.value
    failure.updated_at = timezone.now()
    failure.save(update_fields=["status", "updated_at"])
    audit.record(
        principal,
        action="operations.requeued",
        resource_type="event",
        resource_id=failure.event_id,
        changes={"consumer": failure.consumer, "topic": failure.event.topic},
    )
    return failure


def requeue_dead_events(principal: Principal) -> int:
    """Devolve à fila os eventos esgotados da organização."""
    authorize(principal, Permission.OPERATIONS_REPROCESS)
    ids = list(
        OutboxEvent.objects.filter(
            organization_id=principal.organization_id, status=OutboxStatus.DEAD
        ).values_list("id", flat=True)
    )
    count = requeue_dead(ids)
    audit.record(
        principal,
        action="operations.dead_events_requeued",
        resource_type="outbox",
        resource_id=principal.organization_id,
        changes={"events": count},
    )
    return count
