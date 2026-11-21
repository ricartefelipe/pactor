"""Tasks de consumo: retentativas, registro de falha e publicação no broker.

As tasks são invocadas em processo via ``apply`` apenas para exercitar a
lógica de retentativa e registro; o funcionamento com broker e worker reais
é comprovado em ``tests/infra``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

import pytest
from django.core import mail
from django.db import transaction
from django.test import override_settings

from pactor.access.policies import Principal
from pactor.config.celery import app
from pactor.core import outbox, tasks
from pactor.core.models import ConsumedEvent, ConsumerFailure, OutboxEvent
from pactor.core.outbox import EventEnvelope, PublishError
from pactor.core.tenancy import tenant_context
from pactor.identity import services
from pactor.identity.models import Department, Invitation, MembershipKind
from pactor.notifications import tasks as notifications
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


def _message(
    org_id: uuid.UUID | None, payload: Mapping[str, str] | None = None
) -> Mapping[str, object]:
    with transaction.atomic():
        event = outbox.emit("teste", dict(payload or {}), organization_id=org_id)
    return EventEnvelope.from_model(event).to_message()


def _invite(org_id: uuid.UUID, owner_principal: Principal, email: str) -> Invitation:
    with tenant_context(org_id):
        return services.invite(
            owner_principal,
            email=email,
            kind=MembershipKind.INTERNAL,
            roles=["approver"],
        )


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_invitation_email_consumer_sends_once() -> None:
    org, owner = factories.organization("Envio")
    invitation = _invite(org.id, factories.principal(org, owner), "envio@exemplo.test")
    message = EventEnvelope.from_model(
        OutboxEvent.objects.get(topic="identity.invitation_created")
    ).to_message()
    notifications.invitation_email.apply(kwargs={"message": message})
    notifications.invitation_email.apply(kwargs={"message": message})
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["envio@exemplo.test"]
    assert str(invitation.id) in mail.outbox[0].body
    assert ConsumedEvent.objects.filter(consumer=notifications.invitation_email.name).count() == 1


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_revoked_invitation_is_not_emailed() -> None:
    org, owner = factories.organization()
    principal = factories.principal(org, owner)
    invitation = _invite(org.id, principal, "revogado@exemplo.test")
    with tenant_context(org.id):
        services.revoke_invitation(principal, invitation.id)
    message = EventEnvelope.from_model(
        OutboxEvent.objects.get(topic="identity.invitation_created")
    ).to_message()
    notifications.invitation_email.apply(kwargs={"message": message})
    assert mail.outbox == []
    assert ConsumedEvent.objects.count() == 1


def test_side_effect_failure_is_retried_until_limit_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Em execução local as retentativas ocorrem em sequência até o limite."""
    org, owner = factories.organization()
    _invite(org.id, factories.principal(org, owner), "falha@exemplo.test")
    message = EventEnvelope.from_model(
        OutboxEvent.objects.get(topic="identity.invitation_created")
    ).to_message()

    def smtp_down(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("smtp fora do ar")

    monkeypatch.setattr(notifications, "send_mail", smtp_down)
    notifications.invitation_email.apply(kwargs={"message": message})
    failure = ConsumerFailure.objects.get()
    assert failure.status == "failed"
    assert failure.attempts == tasks.MAX_CONSUMER_RETRIES + 1
    assert "smtp fora do ar" in failure.last_error
    assert ConsumedEvent.objects.count() == 0


def test_transactional_event_consumer_task() -> None:
    org, _ = factories.organization()
    created: list[str] = []

    @tasks.event_consumer("pactor.tests.criar_departamento")
    def criar(envelope: EventEnvelope) -> None:
        assert envelope.organization_id is not None
        Department.objects.create(
            organization_id=envelope.organization_id, name="Criado pelo evento"
        )
        created.append(str(envelope.id))

    message = _message(org.id)
    criar.apply(kwargs={"message": message})
    criar.apply(kwargs={"message": message})
    assert len(created) == 1
    with tenant_context(org.id):
        assert Department.objects.filter(name="Criado pelo evento").count() == 1


def test_publish_to_real_broker_and_unroutable_topic() -> None:
    message = _message(None)
    envelope = EventEnvelope.from_message(message)
    tasks.publish_to_broker(envelope)  # tópico sem consumidores: apenas registra
    routed = EventEnvelope(
        id=envelope.id,
        topic="identity.invitation_created",
        organization_id=None,
        payload={"invitation_id": str(uuid.uuid4())},
        payload_version=1,
    )
    tasks.publish_to_broker(routed)


def test_publish_with_broker_unavailable_raises_publish_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("sem broker")

    monkeypatch.setattr(app, "send_task", broken)
    envelope = EventEnvelope(
        id=uuid.uuid4(),
        topic="identity.invitation_created",
        organization_id=None,
        payload={},
        payload_version=1,
    )
    with pytest.raises(PublishError, match="sem broker"):
        tasks.publish_to_broker(envelope)
    relay = tasks.relay_outbox.apply().result
    assert relay == {"published": 0, "failed": 0}


def test_kick_relay_tolerates_unavailable_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("sem broker")

    monkeypatch.setattr(tasks.relay_outbox, "apply_async", broken)
    tasks.kick_relay()
