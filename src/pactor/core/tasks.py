"""Tasks de infraestrutura: relay da outbox e fábricas de consumidores.

Consumidores são tasks Celery que recebem somente a mensagem JSON do evento
(identificadores), reconstituem o contexto de tenant e deduplicam por
(consumidor, evento). Retentativas são limitadas, com backoff exponencial, e
cada falha fica registrada em ``ConsumerFailure`` para reprocessamento
autorizado.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from celery import Task, shared_task
from kombu.exceptions import KombuError

from pactor.config.celery import app
from pactor.config.events import ROUTES
from pactor.core import consumers, outbox
from pactor.core.outbox import EventEnvelope, PublishError

logger = logging.getLogger(__name__)

MAX_CONSUMER_RETRIES = 6
_RETRY_BASE_SECONDS = 5
_RETRY_CAP_SECONDS = 600


def retry_countdown(retries: int) -> int:
    return int(min(_RETRY_CAP_SECONDS, _RETRY_BASE_SECONDS * 2**retries))


def publish_to_broker(envelope: EventEnvelope) -> None:
    """Publica o evento para cada consumidor roteado do tópico."""
    routes = ROUTES.get(envelope.topic, ())
    if not routes:
        logger.info("outbox_event_without_consumers", extra={"topic": envelope.topic})
    for route in routes:
        try:
            app.send_task(
                route.consumer,
                kwargs={"message": envelope.to_message()},
                queue=route.queue,
                # Identificador estável: republicações do mesmo evento chegam
                # ao consumidor com o mesmo task_id e são deduplicadas.
                task_id=f"{envelope.id}:{route.consumer}",
                retry=True,
                retry_policy={"max_retries": 2, "interval_start": 0.2, "interval_step": 0.5},
            )
        except (KombuError, OSError) as exc:
            raise PublishError(f"{route.consumer}: {exc}") from exc


@shared_task(name="pactor.core.relay_outbox", ignore_result=True)
def relay_outbox() -> dict[str, int]:
    with consumers.isolated_task(None):
        result = outbox.relay_once(publish_to_broker)
    return {"published": result.published, "failed": result.failed}


def kick_relay() -> None:
    """Antecipa o relay após o commit. Falhar aqui é tolerado: o beat recupera."""
    try:
        relay_outbox.apply_async(retry=False)
    except (KombuError, OSError):
        logger.warning("relay_kick_failed")


type Handler = Callable[[EventEnvelope], None]
type ConsumerTask = Task[[Mapping[str, object]], None]


@dataclass(frozen=True, slots=True)
class SideEffect[T]:
    """Consumidor com efeito externo (e-mail, chamada HTTP).

    ``prepare`` roda em transação curta e devolve os dados do efeito (ou
    ``None`` para ignorar); ``perform`` roda fora de transação. O consumo só é
    registrado depois do efeito: a garantia é *pelo menos uma vez*, por isso
    ``perform`` deve tolerar repetição.
    """

    prepare: Callable[[EventEnvelope], T | None]
    perform: Callable[[T], None]


def _run_with_retries(
    task: ConsumerTask, name: str, message: Mapping[str, object], body: Callable[[], None]
) -> None:
    with consumers.isolated_task(task.request.id):
        try:
            body()
        except Exception as exc:
            final = task.request.retries >= MAX_CONSUMER_RETRIES
            consumers.record_failure(name, message, exc, final=final)
            logger.exception("event_consumer_failed", extra={"consumer": name, "final": final})
            if final:
                return
            raise task.retry(exc=exc, countdown=retry_countdown(task.request.retries)) from exc


def event_consumer(name: str) -> Callable[[Handler], ConsumerTask]:
    """Transforma um handler transacional em task deduplicada."""

    def decorator(handler: Handler) -> ConsumerTask:
        @shared_task(bind=True, name=name, ignore_result=True, max_retries=MAX_CONSUMER_RETRIES)
        def task(self: ConsumerTask, message: Mapping[str, object]) -> None:
            def body() -> None:
                consumers.consume_once(name, message, handler)

            _run_with_retries(self, name, message, body)

        return task

    return decorator


def side_effect_consumer[T](name: str, effect: SideEffect[T]) -> ConsumerTask:
    @shared_task(bind=True, name=name, ignore_result=True, max_retries=MAX_CONSUMER_RETRIES)
    def task(self: ConsumerTask, message: Mapping[str, object]) -> None:
        def body() -> None:
            envelope = EventEnvelope.from_message(message)
            if consumers.already_consumed(name, envelope):
                return
            data = consumers.prepare_in_context(envelope, effect.prepare)
            if data is not None:
                effect.perform(data)
            consumers.mark_consumed(name, envelope)

        _run_with_retries(self, name, message, body)

    return task


__all__ = ["SideEffect", "event_consumer", "kick_relay", "relay_outbox", "side_effect_consumer"]
