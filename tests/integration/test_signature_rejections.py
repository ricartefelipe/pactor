"""Caminhos de rejeição e recuperação de assinaturas, tasks e simulador."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import transaction
from django.test import Client, override_settings
from django.utils import timezone

from pactor.config.env import ConfigurationError
from pactor.core.errors import ConflictError, DomainValidationError, NotFoundError
from pactor.core.models import OutboxEvent
from pactor.core.outbox import EventEnvelope
from pactor.core.tenancy import tenant_context
from pactor.signatures import providers, services, tasks
from pactor.signatures.models import EnvelopeDocument
from pactor.signatures.ports import EnvelopeHandle, EnvelopeRequest, ProviderRejectedError
from pactor.signatures.simulator import SimulatorProvider
from tests import factories
from tests.integration.test_signatures import _approved, _contract, _envelope, _send

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    ("signers", "message"),
    [
        ([], "1 a"),
        (
            [services.SignerInput("A", "a@x.test", 1), services.SignerInput("B", "A@x.test", 2)],
            "repetido",
        ),
        ([services.SignerInput(" ", "a@x.test", 1)], "Nome e e-mail"),
        ([services.SignerInput("A", "sem-arroba", 1)], "Nome e e-mail"),
        ([services.SignerInput("A", "a@x.test", 0)], "positiva"),
    ],
)
def test_signer_validation(signers: list[services.SignerInput], message: str) -> None:
    a = _approved()
    with tenant_context(a.org.id), pytest.raises(DomainValidationError, match=message):
        services.send_for_signature(
            factories.principal_for_owner(a.org, a.manager),
            a.contract.id,
            signers=signers,
            expected_fingerprint=a.revision.fingerprint,
        )


class _RejectingProvider(SimulatorProvider):
    def create_envelope(self, request: EnvelopeRequest) -> EnvelopeHandle:
        raise ProviderRejectedError(f"recusado: {request.reference}")


def test_rejected_dispatch_fails_envelope_and_restores_approval() -> None:
    a = _approved()
    envelope = _send(a)
    services.dispatch(a.org.id, envelope.id, provider=_RejectingProvider())
    assert _envelope(a, envelope.id).state == "failed"
    assert _contract(a).drafting_status == "approved"


def test_provider_account_must_match_envelope() -> None:
    a = _approved()
    envelope = _send(a)
    with pytest.raises(ProviderRejectedError, match="conta"):
        services.dispatch(
            a.org.id, envelope.id, provider=SimulatorProvider(account_id="outra-conta")
        )


def test_network_step_refuses_to_run_inside_transaction() -> None:
    a = _approved()
    envelope = _send(a)
    plan = services.plan_dispatch(a.org.id, envelope.id)
    assert plan is not None
    with transaction.atomic(), pytest.raises(RuntimeError, match="transação"):
        services.perform_dispatch(plan)


def test_dispatch_of_already_sent_envelope_is_noop() -> None:
    a = _approved()
    envelope = _send(a)
    services.dispatch(a.org.id, envelope.id)
    assert services.plan_dispatch(a.org.id, envelope.id) is None


def test_void_flow_and_rejections() -> None:
    a = _approved()
    envelope = _send(a)
    services.dispatch(a.org.id, envelope.id)
    principal = factories.principal_for_owner
    with tenant_context(a.org.id), pytest.raises(NotFoundError):
        services.request_void(principal(a.org, a.manager), a.contract.id, reason="x")
    with tenant_context(a.org.id):
        services.request_void(
            principal(a.org, a.manager), envelope.id, reason="Signatário incorreto"
        )
    services.perform_void(a.org.id, envelope.id)
    services.perform_void(a.org.id, envelope.id)
    assert _envelope(a, envelope.id).state == "voided"
    assert _contract(a).drafting_status == "approved"


def test_fetch_documents_only_for_completed_envelopes() -> None:
    a = _approved()
    envelope = _send(a)
    services.dispatch(a.org.id, envelope.id)
    assert services.fetch_completed_documents(a.org.id, envelope.id) == 0


def test_simulator_rules() -> None:
    a = _approved()
    envelope = _send(a)
    services.dispatch(a.org.id, envelope.id)
    stored = _envelope(a, envelope.id)
    simulator = SimulatorProvider()
    with pytest.raises(ConflictError, match="ordem"):
        simulator.sign(stored.provider_envelope_id, "2")
    with pytest.raises(NotFoundError):
        simulator.sign(stored.provider_envelope_id, "9")
    with pytest.raises(NotFoundError):
        simulator.decline(stored.provider_envelope_id, "9")
    with pytest.raises(ConflictError):
        simulator.download_documents(stored.provider_envelope_id)
    with pytest.raises(NotFoundError):
        simulator.get_state("SIM-inexistente")
    simulator.sign(stored.provider_envelope_id, "1")
    simulator.sign(stored.provider_envelope_id, "2")
    with pytest.raises(ConflictError):
        simulator.sign(stored.provider_envelope_id, "1")
    with pytest.raises(ConflictError):
        simulator.decline(stored.provider_envelope_id, "1")
    with pytest.raises(ConflictError):
        simulator.void(stored.provider_envelope_id, "tarde demais")
    assert simulator.find_by_reference("inexistente") is None


def test_consumer_tasks_run_dispatch_documents_and_void() -> None:
    a = _approved()
    envelope = _send(a)
    requested = OutboxEvent.objects.get(topic="signatures.envelope_requested")
    tasks.dispatch_envelope.apply(
        kwargs={"message": EventEnvelope.from_model(requested).to_message()}
    )
    stored = _envelope(a, envelope.id)
    assert stored.state == "sent"
    simulator = SimulatorProvider()
    for key in ("1", "2"):
        webhook = simulator.sign(stored.provider_envelope_id, key)
        services.ingest_webhook("simulator", dict(webhook.headers), webhook.body)
    completed = OutboxEvent.objects.get(topic="signatures.envelope_completed")
    tasks.fetch_documents.apply(
        kwargs={"message": EventEnvelope.from_model(completed).to_message()}
    )
    with tenant_context(a.org.id):
        assert EnvelopeDocument.objects.filter(envelope_id=envelope.id).count() == 3
    reconciled = tasks.reconcile.apply().result
    assert isinstance(reconciled, int)
    assert tasks.process_pending_events.apply().result == 0


def test_void_task_runs_cancellation() -> None:
    a = _approved()
    envelope = _send(a)
    services.dispatch(a.org.id, envelope.id)
    with tenant_context(a.org.id):
        services.request_void(
            factories.principal_for_owner(a.org, a.manager), envelope.id, reason="Erro"
        )
    event = OutboxEvent.objects.get(topic="signatures.void_requested")
    tasks.void_envelope.apply(kwargs={"message": EventEnvelope.from_model(event).to_message()})
    assert _envelope(a, envelope.id).state == "voided"


def test_event_without_organization_is_rejected_by_consumer_reference() -> None:
    with transaction.atomic():
        event = OutboxEvent.objects.create(
            topic="signatures.envelope_requested", payload={"envelope_id": "x"}
        )
    with pytest.raises(ValueError, match="organização"):
        tasks._ref(EventEnvelope.from_model(event))


def test_provider_configuration() -> None:
    with pytest.raises(ConfigurationError, match="Credenciais Docusign ausentes"):
        providers.docusign_settings()
    with pytest.raises(ConfigurationError, match="desconhecido"):
        providers.get_provider("outro")
    assert providers.get_provider("simulator").name == "simulator"


def test_webhook_limits_and_unconfigured_provider() -> None:
    big = Client().post(
        "/webhooks/signatures/simulator", b"x" * (1024 * 1024 + 1), content_type="application/json"
    )
    assert big.status_code == 413
    unconfigured = Client().post(
        "/webhooks/signatures/docusign", b"{}", content_type="application/json"
    )
    assert unconfigured.status_code == 503


def test_simulator_actions_are_disabled_outside_simulator_mode() -> None:
    a = _approved()
    envelope = _send(a)
    client = factories.client_for(a.manager, a.org)
    with override_settings(PACTOR_SIGNATURE_PROVIDER="docusign"):
        response = client.post(
            f"/api/v1/envelopes/{envelope.id}/simulate",
            {"recipient_key": "1", "action": "sign"},
            content_type="application/json",
        )
    assert response.status_code == 403


def test_simulate_requires_registered_envelope_and_document_must_exist() -> None:
    a = _approved()
    envelope = _send(a)
    client = factories.client_for(a.manager, a.org)
    response = client.post(
        f"/api/v1/envelopes/{envelope.id}/simulate",
        {"recipient_key": "1", "action": "sign"},
        content_type="application/json",
    )
    assert response.status_code == 404
    missing = client.get(f"/api/v1/envelopes/{envelope.id}/documents/{envelope.id}")
    assert missing.status_code == 404


def test_reconcile_defers_when_provider_is_unavailable() -> None:
    a = _approved()
    envelope = _send(a)
    count = services.reconcile(
        now=timezone.now() + timedelta(hours=1), provider=SimulatorProvider(unavailable=True)
    )
    assert count == 0
    assert _envelope(a, envelope.id).state == "pending_dispatch"
