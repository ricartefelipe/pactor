"""Consumidores de indexação (transacionais e deduplicados por evento)."""

from __future__ import annotations

import uuid

from pactor.contracts.models import Contract
from pactor.core.outbox import EventEnvelope
from pactor.core.tasks import event_consumer
from pactor.search.indexing import index_contract


def _contract_id(envelope: EventEnvelope) -> uuid.UUID:
    return uuid.UUID(str(envelope.payload["contract_id"]))


@event_consumer("pactor.search.index_revision")
def index_revision(envelope: EventEnvelope) -> None:
    """Indexa a revisão indicada no evento (nunca "a mais recente")."""
    index_contract(_contract_id(envelope), revision_number=int(str(envelope.payload["number"])))


@event_consumer("pactor.search.index_contract")
def index_current(envelope: EventEnvelope) -> None:
    """Metadados ou anexos mudaram: reindexa mantendo a revisão vigente."""
    contract = (
        Contract.objects.select_related("current_revision")
        .filter(id=_contract_id(envelope))
        .first()
    )
    if contract is not None:
        number = contract.current_revision.number if contract.current_revision else 0
        index_contract(contract.id, revision_number=number)
