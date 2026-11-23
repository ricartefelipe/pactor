"""Envio de envelope pela outbox, RabbitMQ e worker real da fila de integrações."""

from __future__ import annotations

import pytest

from pactor.core import outbox
from pactor.core.tasks import publish_to_broker
from pactor.core.tenancy import tenant_context
from pactor.signatures.models import Envelope, SimulatedEnvelope
from tests.infra.conftest import wait_until
from tests.integration.test_signatures import _approved, _send


@pytest.mark.usefixtures("celery_worker_process")
def test_envelope_is_dispatched_by_real_worker() -> None:
    approved = _approved()
    envelope = _send(approved)
    assert outbox.relay_once(publish_to_broker).published >= 1

    def dispatched() -> bool:
        with tenant_context(approved.org.id):
            return Envelope.objects.get(id=envelope.id).state == "sent"

    wait_until(dispatched)
    assert SimulatedEnvelope.objects.filter(reference=str(envelope.id)).count() == 1
