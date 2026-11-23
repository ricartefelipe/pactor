"""Assinatura eletrônica com o simulador: envio, webhooks, recuperação e integridade."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.storage import default_storage
from django.test import Client, override_settings
from django.utils import timezone

from pactor.access.policies import Role
from pactor.approvals import services as approvals
from pactor.approvals.domain import Decision
from pactor.contracts import services as contracts
from pactor.contracts.models import Contract, Revision
from pactor.core.errors import ConflictError, DomainValidationError, InvalidTransitionError
from pactor.core.models import OutboxEvent
from pactor.core.tenancy import tenant_context
from pactor.identity.models import Organization, User
from pactor.signatures import services
from pactor.signatures.api import SimulatedWebhookSerializer
from pactor.signatures.models import (
    DocumentKind,
    Envelope,
    EnvelopeDocument,
    SignatureEvent,
    SimulatedEnvelope,
)
from pactor.signatures.simulator import SIGNATURE_HEADER, SimulatorDisabledError, SimulatorProvider
from pactor.signatures.storage import DocumentIntegrityError, read_verified, sha256_bytes
from tests import factories
from tests.schema_match import assert_matches

pytestmark = pytest.mark.django_db(transaction=True)
JSON = "application/json"


@dataclass(frozen=True)
class Approved:
    org: Organization
    manager: User
    approver: User
    contract: Contract
    revision: Revision


SIGNERS = [
    services.SignerInput("Representante Contratante (fictício)", "contratante@exemplo.test", 1),
    services.SignerInput("Representante Contratada (fictícia)", "contratada@exemplo.test", 2),
]


def _approved(org: Organization | None = None) -> Approved:
    if org is None:
        org, admin = factories.organization()
    else:
        admin = factories.member(org, [Role.ADMIN])
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    approver = factories.member(org, [Role.APPROVER])
    rules = [
        {
            "name": "Padrão",
            "conditions": [],
            "steps": [
                {
                    "key": "gestao",
                    "name": "Gestão",
                    "order": 1,
                    "quorum": 1,
                    "approver_ids": [str(approver.id)],
                }
            ],
        }
    ]
    contract = factories.contract(org, manager, total_value=Decimal("5000.00"))
    with tenant_context(org.id):
        approvals.publish_workflow(factories.principal(org, admin), name="Padrão", rules=rules)
        principal = factories.principal_for_owner(org, manager)
        revision = contracts.publish_revision(principal, contract.id, expected_draft_version=1)
        round_ = approvals.submit_for_approval(
            principal, contract.id, expected_fingerprint=revision.fingerprint
        )
        approvals.cast_vote(
            factories.principal(org, approver),
            round_.id,
            step_key="gestao",
            decision=Decision.APPROVE,
            expected_fingerprint=revision.fingerprint,
        )
    return Approved(org, manager, approver, contract, revision)


def _send(a: Approved) -> Envelope:
    with tenant_context(a.org.id):
        return services.send_for_signature(
            factories.principal_for_owner(a.org, a.manager),
            a.contract.id,
            signers=SIGNERS,
            expected_fingerprint=a.revision.fingerprint,
        )


def _envelope(a: Approved, envelope_id: uuid.UUID | str) -> Envelope:
    with tenant_context(a.org.id):
        return Envelope.objects.get(id=envelope_id)


def _contract(a: Approved) -> Contract:
    with tenant_context(a.org.id):
        return Contract.objects.get(id=a.contract.id)


def _post_webhook(headers: dict[str, str], body: bytes) -> dict[str, object]:
    response = Client().post(
        "/webhooks/signatures/simulator", body, content_type=JSON, headers=headers
    )
    assert response.status_code == 200, response.content
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _dispatched(a: Approved) -> Envelope:
    envelope = _send(a)
    services.dispatch(a.org.id, envelope.id)
    return _envelope(a, envelope.id)


def test_send_requires_current_approval_and_matching_fingerprint() -> None:
    a = _approved()
    with tenant_context(a.org.id), pytest.raises(ConflictError, match="difere do aprovado"):
        services.send_for_signature(
            factories.principal_for_owner(a.org, a.manager),
            a.contract.id,
            signers=SIGNERS,
            expected_fingerprint="0" * 64,
        )
    unapproved = factories.contract(a.org, a.manager)
    with tenant_context(a.org.id), pytest.raises(InvalidTransitionError):
        services.send_for_signature(
            factories.principal_for_owner(a.org, a.manager),
            unapproved.id,
            signers=SIGNERS,
            expected_fingerprint="0" * 64,
        )


def test_sent_document_is_the_approved_revision_with_registered_hash() -> None:
    a = _approved()
    envelope = _send(a)
    assert _contract(a).drafting_status == "signing"
    with tenant_context(a.org.id):
        sent = EnvelopeDocument.objects.get(envelope=envelope, kind=DocumentKind.SENT)
        content = read_verified(sent)
        assert envelope.revision_id == a.revision.id
        assert envelope.fingerprint == a.revision.fingerprint
        assert sent.sha256 == sha256_bytes(content)
        assert content.startswith(b"%PDF")
        assert OutboxEvent.objects.filter(topic="signatures.envelope_requested").count() == 1
    with tenant_context(a.org.id), pytest.raises(InvalidTransitionError):
        services.send_for_signature(
            factories.principal_for_owner(a.org, a.manager),
            a.contract.id,
            signers=SIGNERS,
            expected_fingerprint=a.revision.fingerprint,
        )


def test_dispatch_uses_stored_approved_document_even_after_draft_changes() -> None:
    a = _approved()
    envelope = _send(a)
    with tenant_context(a.org.id):
        sent_hash = EnvelopeDocument.objects.get(envelope=envelope, kind=DocumentKind.SENT).sha256
    services.dispatch(a.org.id, envelope.id)
    simulated = SimulatedEnvelope.objects.get(reference=str(envelope.id))
    assert simulated.document_sha256 == sent_hash
    assert hashlib.sha256(bytes(simulated.document)).hexdigest() == sent_hash
    assert _envelope(a, envelope.id).state == "sent"


def test_dispatch_refuses_when_approval_changed_before_sending() -> None:
    a = _approved()
    envelope = _send(a)
    with tenant_context(a.org.id):
        Contract.objects.filter(id=a.contract.id).update(approved_fingerprint="f" * 64)
    services.dispatch(a.org.id, envelope.id)
    assert _envelope(a, envelope.id).state == "failed"
    assert not SimulatedEnvelope.objects.exists()


def test_dispatch_refuses_tampered_stored_document() -> None:
    a = _approved()
    envelope = _send(a)
    with tenant_context(a.org.id):
        sent = EnvelopeDocument.objects.get(envelope=envelope, kind=DocumentKind.SENT)
    with default_storage.open(sent.storage_path, "wb") as handle:
        handle.write(b"%PDF adulterado")
    with tenant_context(a.org.id), pytest.raises(DocumentIntegrityError):
        read_verified(sent)
    services.dispatch(a.org.id, envelope.id)
    stored = _envelope(a, envelope.id)
    assert stored.state == "failed"
    assert "hash" in stored.last_error
    assert _contract(a).drafting_status == "approved"


def test_timeout_after_creation_does_not_create_a_second_envelope() -> None:
    a = _approved()
    envelope = _send(a)
    with pytest.raises(services.DispatchRetryError):
        services.dispatch(
            a.org.id, envelope.id, provider=SimulatorProvider(timeout_after_create=True)
        )
    assert _envelope(a, envelope.id).state == "dispatch_unknown"
    assert SimulatedEnvelope.objects.count() == 1
    services.dispatch(a.org.id, envelope.id)
    recovered = _envelope(a, envelope.id)
    assert recovered.state == "sent"
    assert recovered.provider_envelope_id == SimulatedEnvelope.objects.get().provider_envelope_id
    assert SimulatedEnvelope.objects.count() == 1
    assert recovered.dispatch_attempts == 2


def test_unavailable_provider_keeps_envelope_pending_for_retry() -> None:
    a = _approved()
    envelope = _send(a)
    with pytest.raises(services.DispatchRetryError):
        services.dispatch(a.org.id, envelope.id, provider=SimulatorProvider(unavailable=True))
    assert _envelope(a, envelope.id).state == "pending_dispatch"
    assert not SimulatedEnvelope.objects.exists()


def test_participant_signature_does_not_complete_envelope_and_completion_via_webhook() -> None:
    a = _approved()
    envelope = _dispatched(a)
    simulator = SimulatorProvider()
    first = simulator.sign(envelope.provider_envelope_id, "1")
    result = _post_webhook(dict(first.headers), first.body)
    assert result["outcome"] == "applied"
    partial = _envelope(a, envelope.id)
    assert partial.state == "sent"
    assert _contract(a).drafting_status == "signing"
    with tenant_context(a.org.id):
        assert {r.recipient_key: r.status for r in partial.recipients.all()} == {
            "1": "signed",
            "2": "sent",
        }
    final = simulator.sign(envelope.provider_envelope_id, "2")
    assert _post_webhook(dict(final.headers), final.body)["outcome"] == "applied"
    assert _envelope(a, envelope.id).state == "completed"
    assert _contract(a).drafting_status == "signed"
    replay = _post_webhook(dict(final.headers), final.body)
    assert replay["duplicate"] is True
    assert SignatureEvent.objects.count() == 2
    with tenant_context(a.org.id):
        assert OutboxEvent.objects.filter(topic="signatures.envelope_completed").count() == 1


def test_out_of_order_events_never_regress_state() -> None:
    a = _approved()
    envelope = _dispatched(a)
    simulator = SimulatorProvider()
    first = simulator.sign(envelope.provider_envelope_id, "1")
    final = simulator.sign(envelope.provider_envelope_id, "2")
    assert _post_webhook(dict(final.headers), final.body)["outcome"] == "applied"
    late = _post_webhook(dict(first.headers), first.body)
    assert late["outcome"] == "stale"
    assert _envelope(a, envelope.id).state == "completed"
    assert _contract(a).drafting_status == "signed"


def test_event_of_old_envelope_never_changes_contract() -> None:
    a = _approved()
    old = _dispatched(a)
    simulator = SimulatorProvider()
    with tenant_context(a.org.id):
        services.request_void(
            factories.principal_for_owner(a.org, a.manager),
            old.id,
            reason="Dados de signatário errados",
        )
    services.perform_void(a.org.id, old.id)
    assert _contract(a).drafting_status == "approved"
    new = _dispatched(a)
    # Evento tardio do envelope antigo chega depois do novo envio.
    stale = SimulatedEnvelope.objects.get(provider_envelope_id=old.provider_envelope_id)
    stale.status = "completed"
    stale.sequence += 1
    stale.save()
    webhook = simulator.webhook_for(stale)
    assert _post_webhook(dict(webhook.headers), webhook.body)["outcome"] == "old_envelope"
    assert _contract(a).drafting_status == "signing"
    assert _envelope(a, new.id).state == "sent"


def test_decline_returns_contract_to_approved() -> None:
    a = _approved()
    envelope = _dispatched(a)
    webhook = SimulatorProvider().decline(envelope.provider_envelope_id, "1")
    assert _post_webhook(dict(webhook.headers), webhook.body)["outcome"] == "applied"
    assert _envelope(a, envelope.id).state == "declined"
    assert _contract(a).drafting_status == "approved"


def test_invalid_webhook_signature_is_rejected_and_not_stored() -> None:
    a = _approved()
    envelope = _dispatched(a)
    webhook = SimulatorProvider().sign(envelope.provider_envelope_id, "1")
    response = Client().post(
        "/webhooks/signatures/simulator",
        webhook.body,
        content_type=JSON,
        headers={SIGNATURE_HEADER: "0" * 64},
    )
    assert response.status_code == 401
    assert not SignatureEvent.objects.exists()
    assert Client().post("/webhooks/signatures/outro", b"{}", content_type=JSON).status_code == 404
    assert Client().get("/webhooks/signatures/simulator").status_code == 405


def test_event_for_unknown_envelope_is_recorded_and_ignored() -> None:
    simulator = SimulatorProvider()
    webhook = simulator.webhook_for(
        SimulatedEnvelope(
            provider_envelope_id="SIM-desconhecido",
            account_id="simulador",
            reference="x",
            subject="x",
            document=b"",
            document_sha256="",
            status="completed",
            recipients=[],
            sequence=1,
            changed_at=timezone.now(),
        )
    )
    assert _post_webhook(dict(webhook.headers), webhook.body)["outcome"] == "unknown_envelope"


def test_completed_documents_are_stored_separately_with_hashes() -> None:
    a = _approved()
    envelope = _dispatched(a)
    simulator = SimulatorProvider()
    for key in ("1", "2"):
        webhook = simulator.sign(envelope.provider_envelope_id, key)
        _post_webhook(dict(webhook.headers), webhook.body)
    assert services.fetch_completed_documents(a.org.id, envelope.id) == 2
    assert services.fetch_completed_documents(a.org.id, envelope.id) == 0
    with tenant_context(a.org.id):
        documents = {d.kind: d for d in EnvelopeDocument.objects.filter(envelope=envelope)}
    assert set(documents) == {"sent", "signed", "evidence"}
    assert documents["sent"].sha256 != documents["signed"].sha256
    with tenant_context(a.org.id):
        signed = read_verified(documents["signed"])
    assert sha256_bytes(signed) == documents["signed"].sha256
    client = factories.client_for(a.manager, a.org)
    download = client.get(f"/api/v1/envelopes/{envelope.id}/documents/{documents['signed'].id}")
    assert download.status_code == 200
    assert download["X-Pactor-SHA256"] == documents["signed"].sha256
    assert hashlib.sha256(download.getvalue()).hexdigest() == documents["signed"].sha256


def test_reconcile_recovers_missed_webhook() -> None:
    a = _approved()
    envelope = _dispatched(a)
    simulator = SimulatorProvider()
    simulator.sign(envelope.provider_envelope_id, "1")
    simulator.sign(envelope.provider_envelope_id, "2")
    later = timezone.now() + timedelta(hours=1)
    assert services.reconcile(now=later) >= 1
    assert _envelope(a, envelope.id).state == "completed"
    assert _contract(a).drafting_status == "signed"


def test_reconcile_finishes_dispatch_left_unknown() -> None:
    a = _approved()
    envelope = _send(a)
    with pytest.raises(services.DispatchRetryError):
        services.dispatch(
            a.org.id, envelope.id, provider=SimulatorProvider(timeout_after_create=True)
        )
    services.reconcile(now=timezone.now() + timedelta(hours=1))
    assert _envelope(a, envelope.id).state == "sent"
    assert SimulatedEnvelope.objects.count() == 1


def test_pending_inbox_events_are_processed_after_failure() -> None:
    a = _approved()
    envelope = _dispatched(a)
    webhook = SimulatorProvider().sign(envelope.provider_envelope_id, "1")
    parsed = SimulatorProvider().parse_webhook(dict(webhook.headers), webhook.body)
    stored, created = services.record_event("simulator", parsed)
    assert created
    assert stored.outcome == "pending"
    assert services.process_pending_events() == 1
    stored.refresh_from_db()
    assert stored.outcome == "applied"


def test_api_send_list_and_simulate_flow() -> None:
    a = _approved()
    client = factories.client_for(a.manager, a.org)
    sent = client.post(
        "/api/v1/envelopes/send",
        {
            "contract_id": str(a.contract.id),
            "expected_fingerprint": a.revision.fingerprint,
            "signers": [
                {"name": s.name, "email": s.email, "routing_order": s.routing_order}
                for s in SIGNERS
            ],
        },
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="envio-assinatura-1",
    )
    assert sent.status_code == 201, sent.json()
    envelope_id = sent.json()["id"]
    services.dispatch(a.org.id, envelope_id)
    listing = client.get(f"/api/v1/envelopes?contract_id={a.contract.id}").json()
    assert listing[0]["state"] == "sent"
    action = client.post(
        f"/api/v1/envelopes/{envelope_id}/simulate",
        {"recipient_key": "1", "action": "sign"},
        content_type=JSON,
    )
    assert action.status_code == 200, action.json()
    webhook = action.json()
    assert_matches(SimulatedWebhookSerializer(), webhook)
    assert _post_webhook(webhook["headers"], webhook["body"].encode())["outcome"] == "applied"
    replay = client.post(
        f"/api/v1/envelopes/{envelope_id}/simulate",
        {"recipient_key": "1", "action": "replay"},
        content_type=JSON,
    ).json()
    assert _post_webhook(replay["headers"], replay["body"].encode())["duplicate"] is True
    detail = client.get(f"/api/v1/envelopes/{envelope_id}").json()
    assert [r["status"] for r in detail["recipients"]] == ["signed", "sent"]


def test_envelopes_are_isolated_between_organizations() -> None:
    a = _approved()
    envelope = _send(a)
    other_org, _ = factories.organization()
    outsider = factories.member(other_org, [Role.CONTRACT_MANAGER])
    client = factories.client_for(outsider, other_org)
    assert client.get(f"/api/v1/envelopes/{envelope.id}").status_code == 404
    assert client.get(f"/api/v1/envelopes?contract_id={a.contract.id}").status_code == 404


def test_void_requires_reason_and_sent_state() -> None:
    a = _approved()
    envelope = _send(a)
    principal = factories.principal_for_owner
    with tenant_context(a.org.id), pytest.raises(DomainValidationError):
        services.request_void(principal(a.org, a.manager), envelope.id, reason="  ")
    with tenant_context(a.org.id), pytest.raises(InvalidTransitionError):
        services.request_void(principal(a.org, a.manager), envelope.id, reason="ainda não enviado")


@override_settings(PACTOR_ENVIRONMENT="production")
def test_simulator_is_disabled_in_production() -> None:
    with pytest.raises(SimulatorDisabledError):
        SimulatorProvider()
