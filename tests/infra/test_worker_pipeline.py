"""Outbox → RabbitMQ → worker Celery → e-mail no Mailpit, com republicação deduplicada."""

from __future__ import annotations

import time

import httpx
import pytest

from pactor.core import outbox
from pactor.core.models import ConsumedEvent, OutboxEvent, OutboxStatus
from pactor.core.outbox import EventEnvelope
from pactor.core.tasks import publish_to_broker
from pactor.core.tenancy import tenant_context
from pactor.identity import services
from pactor.identity.models import MembershipKind
from tests import factories
from tests.infra.conftest import wait_until

CONSUMER = "pactor.notifications.invitation_email"


def _messages_to(mailpit: httpx.Client, address: str) -> list[dict[str, object]]:
    response = mailpit.get("/search", params={"query": f"to:{address}"})
    response.raise_for_status()
    messages = response.json()["messages"]
    assert isinstance(messages, list)
    return messages


@pytest.mark.usefixtures("celery_worker_process")
def test_invitation_email_is_delivered_once_by_real_worker(mailpit: httpx.Client) -> None:
    org, owner = factories.organization("Infra")
    with tenant_context(org.id):
        services.invite(
            factories.principal(org, owner),
            email="convidado@exemplo.test",
            kind=MembershipKind.INTERNAL,
            roles=["approver"],
        )
    result = outbox.relay_once(publish_to_broker)
    assert result.published >= 1
    wait_until(lambda: ConsumedEvent.objects.filter(consumer=CONSUMER).exists())
    wait_until(lambda: len(_messages_to(mailpit, "convidado@exemplo.test")) == 1)

    # Republicação do mesmo evento (ex.: relay caiu antes de marcar publicado).
    event = OutboxEvent.objects.get(topic="identity.invitation_created")
    assert event.status == OutboxStatus.PUBLISHED
    publish_to_broker(EventEnvelope.from_model(event))
    # O worker recebe a repetição, encontra o consumo registrado e não reenvia.
    time.sleep(3)
    assert len(_messages_to(mailpit, "convidado@exemplo.test")) == 1
    message = mailpit.get(
        f"/message/{_messages_to(mailpit, 'convidado@exemplo.test')[0]['ID']}"
    ).json()
    assert "/convites/aceitar?token=" in message["Text"]
