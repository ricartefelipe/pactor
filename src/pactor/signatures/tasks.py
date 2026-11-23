"""Tasks de assinatura: envio, documentos concluídos, cancelamento e recuperação.

Todas falam com o provedor fora de transação (``SideEffect.perform``) e
registram o resultado em transações curtas no tenant do envelope.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from celery import shared_task

from pactor.core import consumers
from pactor.core.outbox import EventEnvelope
from pactor.core.tasks import SideEffect, side_effect_consumer
from pactor.signatures import services
from pactor.signatures.services import DispatchPlan


@dataclass(frozen=True, slots=True)
class EnvelopeRef:
    organization_id: uuid.UUID
    envelope_id: uuid.UUID


def _ref(envelope: EventEnvelope) -> EnvelopeRef:
    if envelope.organization_id is None:
        raise ValueError("evento de assinatura sem organização")
    return EnvelopeRef(envelope.organization_id, uuid.UUID(str(envelope.payload["envelope_id"])))


def _plan(envelope: EventEnvelope) -> DispatchPlan | None:
    ref = _ref(envelope)
    return services.plan_dispatch(ref.organization_id, ref.envelope_id)


def _fetch(ref: EnvelopeRef) -> None:
    services.fetch_completed_documents(ref.organization_id, ref.envelope_id)


def _void(ref: EnvelopeRef) -> None:
    services.perform_void(ref.organization_id, ref.envelope_id)


dispatch_envelope = side_effect_consumer(
    "pactor.signatures.dispatch_envelope",
    SideEffect(prepare=_plan, perform=services.perform_dispatch),
)

fetch_documents = side_effect_consumer(
    "pactor.signatures.fetch_documents",
    SideEffect(prepare=_ref, perform=_fetch),
)

void_envelope = side_effect_consumer(
    "pactor.signatures.void_envelope",
    SideEffect(prepare=_ref, perform=_void),
)


@shared_task(name="pactor.signatures.reconcile", ignore_result=True)
def reconcile() -> int:
    with consumers.isolated_task(None):
        return services.reconcile()


@shared_task(name="pactor.signatures.process_pending_events", ignore_result=True)
def process_pending_events() -> int:
    with consumers.isolated_task(None):
        return services.process_pending_events()
