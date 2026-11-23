"""Execução de consumidores de eventos com deduplicação e contexto de tenant.

Cada task Celery recebe apenas a mensagem JSON do evento. Antes de consultar
qualquer entidade, o consumidor fecha conexões antigas, abre uma transação
vinculada à organização do evento e registra o consumo; ao terminar, em
sucesso ou falha, as conexões são novamente liberadas. Assim um processo de
worker reutilizado entre organizações não carrega contexto de uma para outra.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from pactor.core.models import ConsumedEvent, ConsumerFailure, ConsumerFailureStatus
from pactor.core.observability import CONSUMER_FAILURES, bind_log_context
from pactor.core.outbox import EventEnvelope
from pactor.core.tenancy import tenant_context

logger = logging.getLogger(__name__)

type Handler = Callable[[EventEnvelope], None]


@contextmanager
def isolated_task(task_id: str | None) -> Iterator[None]:
    """Garante conexões limpas antes e depois da execução de uma task."""
    close_old_connections()
    try:
        with bind_log_context(task_id=task_id):
            yield
    finally:
        # Transações só são abertas por context managers e terminam com eles;
        # o vínculo de tenant é local à transação. Aqui resta devolver
        # conexões vencidas ou com erro antes da próxima task.
        close_old_connections()


@contextmanager
def _event_transaction(envelope: EventEnvelope) -> Iterator[None]:
    if envelope.organization_id is None:
        with transaction.atomic():
            yield
    else:
        with tenant_context(envelope.organization_id):
            yield


def _claim_consumption(consumer: str, envelope: EventEnvelope) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core_consumedevent (consumer, event_id, organization_id, consumed_at) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (consumer, event_id) DO NOTHING RETURNING id",
            [consumer, envelope.id, envelope.organization_id, timezone.now()],
        )
        return cursor.fetchone() is not None


def consume_once(consumer: str, message: Mapping[str, object], handler: Handler) -> bool:
    """Aplica ``handler`` uma única vez por (consumidor, evento).

    O registro de consumo e os efeitos do handler ficam na mesma transação:
    se o handler falhar, nada é gravado e a retentativa reaplica do zero.
    Devolve ``False`` quando o evento já havia sido consumido.
    """
    envelope = EventEnvelope.from_message(message)
    with (
        bind_log_context(organization_id=str(envelope.organization_id or "")),
        _event_transaction(envelope),
    ):
        if not _claim_consumption(consumer, envelope):
            logger.info("event_already_consumed", extra={"consumer": consumer})
            return False
        handler(envelope)
    ConsumerFailure.objects.filter(consumer=consumer, event_id=envelope.id).update(
        status=ConsumerFailureStatus.RESOLVED, updated_at=timezone.now()
    )
    return True


def record_failure(
    consumer: str, message: Mapping[str, object], error: BaseException, *, final: bool
) -> None:
    envelope = EventEnvelope.from_message(message)
    CONSUMER_FAILURES.labels(consumer).inc()
    status = ConsumerFailureStatus.FAILED if final else ConsumerFailureStatus.RETRYING
    with transaction.atomic():
        failure, _ = ConsumerFailure.objects.select_for_update().get_or_create(
            consumer=consumer,
            event_id=envelope.id,
            defaults={"organization_id": envelope.organization_id, "status": status},
        )
        failure.attempts += 1
        failure.status = status
        failure.last_error = f"{type(error).__name__}: {error}"[:2000]
        failure.updated_at = timezone.now()
        failure.save(update_fields=["attempts", "status", "last_error", "updated_at"])


def already_consumed(consumer: str, envelope: EventEnvelope) -> bool:
    return ConsumedEvent.objects.filter(consumer=consumer, event_id=envelope.id).exists()


def prepare_in_context[T](envelope: EventEnvelope, prepare: Callable[[EventEnvelope], T]) -> T:
    """Executa a etapa de leitura de um efeito externo no contexto do tenant."""
    with _event_transaction(envelope):
        return prepare(envelope)


def mark_consumed(consumer: str, envelope: EventEnvelope) -> None:
    with _event_transaction(envelope):
        _claim_consumption(consumer, envelope)
    ConsumerFailure.objects.filter(consumer=consumer, event_id=envelope.id).update(
        status=ConsumerFailureStatus.RESOLVED, updated_at=timezone.now()
    )
