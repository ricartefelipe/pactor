"""Correlação de logs com contrato, revisão e envelope em requisições e tasks."""

from __future__ import annotations

import uuid

import pytest
from django.db import transaction

from pactor.access.policies import Principal, Role
from pactor.contracts import selectors, services
from pactor.contracts.models import Contract, Revision
from pactor.core import outbox, tasks
from pactor.core.observability import current_log_context
from pactor.core.outbox import EventEnvelope
from pactor.core.tenancy import tenant_context
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


def test_route_parameters_are_bound_to_the_log_context(monkeypatch: pytest.MonkeyPatch) -> None:
    org, _ = factories.organization()
    owner = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, owner)
    with tenant_context(org.id):
        principal = factories.principal_for_owner(org, owner)
        services.publish_revision(principal, contract.id, expected_draft_version=1)
        revision = Revision.objects.get(contract=contract)
    seen: list[dict[str, str]] = []
    original = selectors.get_visible_contract

    def spy(who: Principal, contract_id: uuid.UUID, *, for_update: bool = False) -> Contract:
        seen.append(current_log_context())
        return original(who, contract_id, for_update=for_update)

    monkeypatch.setattr(selectors, "get_visible_contract", spy)
    response = factories.client_for(owner, org).get(
        f"/api/v1/contracts/{contract.id}/revisions/{revision.id}"
    )
    assert response.status_code == 200, response.json()
    assert seen[0]["contract_id"] == str(contract.id)
    assert seen[0]["revision_id"] == str(revision.id)
    assert seen[0]["organization_id"] == str(org.id)
    assert "contract_id" not in current_log_context()


def test_event_payload_identifiers_are_bound_while_the_consumer_runs() -> None:
    org, _ = factories.organization()
    contract_id, envelope_id = uuid.uuid4(), uuid.uuid4()
    seen: list[dict[str, str]] = []

    @tasks.event_consumer("pactor.tests.correlacao")
    def consumer(_envelope: EventEnvelope) -> None:
        seen.append(current_log_context())

    with transaction.atomic():
        event = outbox.emit(
            "teste",
            {"contract_id": str(contract_id), "envelope_id": str(envelope_id), "outro": "x"},
            organization_id=org.id,
        )
    consumer.apply(kwargs={"message": EventEnvelope.from_model(event).to_message()})
    assert seen[0]["contract_id"] == str(contract_id)
    assert seen[0]["envelope_id"] == str(envelope_id)
    assert "outro" not in seen[0]
    assert seen[0]["task_id"]
