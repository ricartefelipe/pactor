"""Métricas Prometheus e reprocessamento autorizado de falhas e eventos esgotados."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import transaction
from django.test import Client, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError

from pactor.access.policies import Role
from pactor.audit.models import AuditEvent
from pactor.config.celery import app
from pactor.core import outbox
from pactor.core.models import ConsumerFailure, ConsumerFailureStatus, OutboxEvent, OutboxStatus
from pactor.core.tenancy import tenant_context
from pactor.deadlines.models import Alert, AlertKind
from pactor.identity.models import Organization
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)
TOKEN = "token-de-metricas-de-teste"
INVITATION_CONSUMER = "pactor.notifications.invitation_email"


def _failure(
    org: Organization,
    *,
    topic: str = "identity.invitation_created",
    consumer: str = INVITATION_CONSUMER,
    status: ConsumerFailureStatus = ConsumerFailureStatus.FAILED,
) -> ConsumerFailure:
    with transaction.atomic():
        event = outbox.emit(topic, {"invitation_id": str(uuid.uuid4())}, organization_id=org.id)
    return ConsumerFailure.objects.create(
        consumer=consumer,
        event=event,
        organization_id=org.id,
        attempts=4,
        status=status,
        last_error="smtp fora do ar",
    )


def _metrics(client: Client) -> str:
    response = client.get("/metrics", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    return response.content.decode()


def test_metrics_endpoint_requires_its_own_token(client: Client) -> None:
    assert client.get("/metrics").status_code == 404
    with override_settings(PACTOR_METRICS_TOKEN=TOKEN):
        assert client.get("/metrics").status_code == 401
        wrong = client.get("/metrics", HTTP_AUTHORIZATION="Bearer outro")
        assert wrong.status_code == 401
        assert wrong["WWW-Authenticate"] == "Bearer"
        assert client.get("/metrics", HTTP_AUTHORIZATION=TOKEN).status_code == 401
        assert client.post("/metrics", HTTP_AUTHORIZATION=f"Bearer {TOKEN}").status_code == 405


@override_settings(PACTOR_METRICS_TOKEN=TOKEN)
def test_business_metrics_sum_every_organization_without_identifiers(client: Client) -> None:
    org_a, _ = factories.organization("A")
    org_b, _ = factories.organization("B")
    _failure(org_a)
    stuck = _failure(org_b, status=ConsumerFailureStatus.RETRYING)
    ConsumerFailure.objects.filter(id=stuck.id).update(
        updated_at=timezone.now() - timedelta(hours=1)
    )
    client.get("/health/live")
    body = _metrics(client)
    assert 'pactor_consumer_failures{status="failed"} 1.0' in body
    assert 'pactor_consumer_failures{status="retrying"} 1.0' in body
    assert "pactor_consumer_stuck 1.0" in body
    assert 'pactor_outbox_events{status="pending"}' in body
    assert "pactor_outbox_oldest_pending_age_seconds" in body
    assert "pactor_alerts_overdue" in body
    assert "pactor_http_request_duration_seconds" in body
    for org in (org_a, org_b):
        assert str(org.id) not in body


def _overdue_alert(org: Organization) -> None:
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, manager)
    now = timezone.now()
    with tenant_context(org.id):
        Alert.objects.create(
            organization_id=org.id,
            target_type="deadline",
            target_id=uuid.uuid4(),
            contract=contract,
            kind=AlertKind.DUE,
            fire_on=now.date(),
            fire_at=now - timedelta(minutes=5),
            subject="Prazo fictício",
        )


@override_settings(PACTOR_METRICS_TOKEN=TOKEN)
def test_overdue_alerts_are_counted_inside_each_tenant_context(client: Client) -> None:
    assert "pactor_alerts_overdue 0.0" in _metrics(client)
    for name in ("A", "B"):
        org, _ = factories.organization(name)
        _overdue_alert(org)
    # Fora de contexto o RLS não mostra nenhum alerta; o coletor percorre cada organização.
    assert not Alert.objects.exists()
    assert "pactor_alerts_overdue 2.0" in _metrics(client)


def test_multiprocess_exposition_reads_the_shared_directory(client: Client, tmp_path: Path) -> None:
    with override_settings(PACTOR_METRICS_TOKEN=TOKEN, PROMETHEUS_MULTIPROC_DIR=str(tmp_path)):
        body = _metrics(client)
    # Sem arquivos de workers no diretório, só as métricas de negócio aparecem.
    assert "pactor_http_request_duration_seconds" not in body
    assert "pactor_outbox_events" in body


def test_failures_listing_is_restricted_to_the_organization() -> None:
    org_a, owner_a = factories.organization("A")
    org_b, _ = factories.organization("B")
    own = _failure(org_a)
    _failure(org_b)
    listing = factories.client_for(owner_a, org_a).get("/api/v1/operations/failures")
    assert listing.status_code == 200
    assert [f["id"] for f in listing.json()] == [str(own.id)]
    assert listing.json()[0]["topic"] == "identity.invitation_created"
    manager = factories.member(org_a, [Role.CONTRACT_MANAGER])
    assert (
        factories.client_for(manager, org_a).get("/api/v1/operations/failures").status_code == 403
    )
    without_mfa = factories.client_for(owner_a, org_a, mfa=False)
    assert without_mfa.get("/api/v1/operations/failures").json()["error"]["code"] == (
        "mfa_required"
    )


def test_requeue_republishes_the_original_event_once(monkeypatch: pytest.MonkeyPatch) -> None:
    org, owner = factories.organization()
    failure = _failure(org)
    sent: list[dict[str, object]] = []

    def fake_send_task(name: str, **kwargs: object) -> None:
        sent.append({"name": name, **kwargs})

    monkeypatch.setattr(app, "send_task", fake_send_task)
    client = factories.client_for(owner, org)
    response = client.post(f"/api/v1/operations/failures/{failure.id}/requeue")
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "requeued"
    assert len(sent) == 1
    assert sent[0]["name"] == INVITATION_CONSUMER
    assert sent[0]["queue"] == "notifications"
    assert sent[0]["task_id"] == f"{failure.event_id}:{INVITATION_CONSUMER}:reprocesso:4"
    message = sent[0]["kwargs"]
    assert isinstance(message, dict)
    assert message["message"]["id"] == str(failure.event_id)
    with tenant_context(org.id):
        assert AuditEvent.objects.filter(action="operations.requeued").count() == 1
    again = client.post(f"/api/v1/operations/failures/{failure.id}/requeue")
    assert again.status_code == 409
    assert len(sent) == 1


def test_requeue_rejections(monkeypatch: pytest.MonkeyPatch) -> None:
    org_a, owner = factories.organization("A")
    org_b, _ = factories.organization("B")
    client = factories.client_for(owner, org_a)
    foreign = _failure(org_b)
    assert client.post(f"/api/v1/operations/failures/{foreign.id}/requeue").status_code == 404
    assert client.post(f"/api/v1/operations/failures/{uuid.uuid4()}/requeue").status_code == 404
    unrouted = _failure(org_a, consumer="pactor.consumidor.removido")
    response = client.post(f"/api/v1/operations/failures/{unrouted.id}/requeue")
    assert response.status_code == 409
    assert unrouted.status == ConsumerFailureStatus.FAILED

    def broker_down(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("broker fora do ar")

    monkeypatch.setattr(app, "send_task", broker_down)
    failure = _failure(org_a, topic="alerts.fired", consumer="pactor.notifications.alert_email")
    down = client.post(f"/api/v1/operations/failures/{failure.id}/requeue")
    assert down.status_code == 409
    assert down.json()["error"]["code"] == "broker_unavailable"
    failure.refresh_from_db()
    assert failure.status == ConsumerFailureStatus.FAILED


def test_dead_events_are_requeued_only_for_the_organization() -> None:
    org_a, owner = factories.organization("A")
    org_b, _ = factories.organization("B")
    with transaction.atomic():
        own = outbox.emit("files.released", {}, organization_id=org_a.id)
        foreign = outbox.emit("files.released", {}, organization_id=org_b.id)
    OutboxEvent.objects.filter(id__in=[own.id, foreign.id]).update(
        status=OutboxStatus.DEAD, attempts=10, last_error="falhou"
    )
    response = factories.client_for(owner, org_a).post("/api/v1/operations/dead-events/requeue")
    assert response.status_code == 200
    assert response.json() == {"requeued": 1}
    own.refresh_from_db()
    foreign.refresh_from_db()
    assert (own.status, own.attempts, own.last_error) == (OutboxStatus.PENDING, 0, "")
    assert foreign.status == OutboxStatus.DEAD
