"""Transactional Outbox, relay recuperável e consumidores deduplicados."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from pactor.core import consumers, outbox
from pactor.core.models import ConsumedEvent, ConsumerFailure, OutboxEvent, OutboxStatus
from pactor.core.outbox import EventEnvelope, PublishError
from pactor.core.tenancy import current_tenant, tenant_context
from pactor.identity.models import Department
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


def _emit(org_id: uuid.UUID | None = None, topic: str = "teste.evento") -> OutboxEvent:
    with transaction.atomic():
        return outbox.emit(topic, {"k": "v"}, organization_id=org_id)


def test_emit_requires_transaction() -> None:
    with pytest.raises(outbox.OutboxRequiresTransactionError):
        outbox.emit("x", {}, organization_id=None)


def test_event_is_discarded_with_business_rollback() -> None:
    with pytest.raises(RuntimeError), transaction.atomic():
        outbox.emit("x", {}, organization_id=None)
        raise RuntimeError
    assert OutboxEvent.objects.count() == 0


def test_failure_between_commit_and_publish_is_recovered_by_relay() -> None:
    """O gatilho pós-commit falha (broker fora); o relay periódico publica depois."""

    def broken_kick() -> None:
        raise ConnectionError("broker indisponível")

    with pytest.raises(ConnectionError), transaction.atomic():
        event = outbox.emit("x", {}, organization_id=None, on_commit=broken_kick)
    # O commit aconteceu antes do on_commit falhar: o evento está durável.
    assert OutboxEvent.objects.get(id=event.id).status == OutboxStatus.PENDING
    published: list[uuid.UUID] = []
    result = outbox.relay_once(lambda env: published.append(env.id))
    assert result.published == 1 and published == [event.id]
    assert OutboxEvent.objects.get(id=event.id).status == OutboxStatus.PUBLISHED


def test_publish_failure_schedules_retry_with_backoff_then_dead() -> None:
    event = _emit()

    def failing(_: EventEnvelope) -> None:
        raise PublishError("timeout")

    assert outbox.relay_once(failing).failed == 1
    stored = OutboxEvent.objects.get(id=event.id)
    assert stored.status == OutboxStatus.PENDING and stored.available_at > timezone.now()
    assert outbox.relay_once(failing).failed == 0  # ainda em backoff
    OutboxEvent.objects.filter(id=event.id).update(
        attempts=outbox.MAX_PUBLISH_ATTEMPTS - 1, available_at=timezone.now()
    )
    outbox.relay_once(failing)
    assert OutboxEvent.objects.get(id=event.id).status == OutboxStatus.DEAD
    assert outbox.requeue_dead([event.id]) == 1
    assert outbox.relay_once(lambda _: None).published == 1


def test_expired_claim_is_republished_after_crash() -> None:
    """Relay cai depois de reivindicar: a concessão expira e outro relay publica."""
    event = _emit()
    assert [e.id for e in outbox.claim_batch()] == [event.id]
    assert outbox.claim_batch() == []  # concessão vigente
    later = timezone.now() + outbox.CLAIM_LEASE + timedelta(seconds=1)
    assert [e.id for e in outbox.claim_batch(now=later)] == [event.id]


def test_mark_failed_ignores_events_no_longer_claimed() -> None:
    event = _emit()
    outbox.mark_failed(event.id, "erro")
    assert OutboxEvent.objects.get(id=event.id).attempts == 0


def test_consumer_applies_event_once_even_when_redelivered() -> None:
    org, _ = factories.organization()
    event = _emit(org.id)
    message = EventEnvelope.from_model(event).to_message()
    calls: list[uuid.UUID | None] = []

    def handler(envelope: EventEnvelope) -> None:
        calls.append(current_tenant())
        assert envelope.organization_id is not None
        Department.objects.create(
            organization_id=envelope.organization_id, name=f"via {envelope.id}"
        )

    assert consumers.consume_once("c1", message, handler) is True
    assert consumers.consume_once("c1", message, handler) is False
    assert calls == [org.id]
    assert consumers.consume_once("c2", message, lambda _: None) is True
    assert ConsumedEvent.objects.filter(event_id=event.id).count() == 2


def test_failed_handler_rolls_back_effects_and_consumption() -> None:
    org, _ = factories.organization()
    message = EventEnvelope.from_model(_emit(org.id)).to_message()

    def failing(envelope: EventEnvelope) -> None:
        assert envelope.organization_id is not None
        Department.objects.create(organization_id=envelope.organization_id, name="parcial")
        raise ValueError("falha no meio")

    with pytest.raises(ValueError, match="falha no meio"):
        consumers.consume_once("c1", message, failing)
    consumers.record_failure("c1", message, ValueError("falha no meio"), final=False)
    with tenant_context(org.id):
        assert Department.objects.filter(name="parcial").count() == 0
    assert ConsumedEvent.objects.count() == 0
    assert ConsumerFailure.objects.get().status == "retrying"
    assert consumers.consume_once("c1", message, lambda _: None) is True
    assert ConsumerFailure.objects.get().status == "resolved"


def test_worker_reused_between_tenants_does_not_leak_context() -> None:
    """Mesmo processo e conexão processam eventos de A e depois de B."""
    org_a, _ = factories.organization("A")
    org_b, _ = factories.organization("B")
    factories.department(org_a, "Segredo de A")
    seen: dict[str, list[str]] = {}

    def handler(envelope: EventEnvelope) -> None:
        seen[str(envelope.organization_id)] = list(
            Department.objects.values_list("name", flat=True)
        )

    with consumers.isolated_task("t-a"):
        consumers.consume_once(
            "leitor", EventEnvelope.from_model(_emit(org_a.id)).to_message(), handler
        )
    assert current_tenant() is None
    with consumers.isolated_task("t-b"):
        consumers.consume_once(
            "leitor", EventEnvelope.from_model(_emit(org_b.id)).to_message(), handler
        )
    assert seen == {str(org_a.id): ["Segredo de A"], str(org_b.id): []}
    assert current_tenant() is None


def test_system_event_without_organization_runs_without_tenant() -> None:
    message = EventEnvelope.from_model(_emit(None)).to_message()
    observed: list[uuid.UUID | None] = []
    assert consumers.consume_once("sys", message, lambda _: observed.append(current_tenant()))
    assert observed == [None]
