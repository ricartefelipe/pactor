"""Vigência após assinatura, agendador, ações manuais, aditivos e limites do plano."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from pactor.approvals import services as approvals
from pactor.approvals.domain import Decision
from pactor.contracts import lifecycle_services as lifecycle
from pactor.contracts import services as contracts
from pactor.contracts.domain import ContractType, PartyRole
from pactor.contracts.lifecycle import ConditionField
from pactor.contracts.models import Contract, LifecycleEvent, LifecyclePolicy
from pactor.contracts.tasks import advance_lifecycles
from pactor.core.errors import DomainValidationError, InvalidTransitionError, PlanLimitExceededError
from pactor.core.tenancy import tenant_context
from pactor.identity.models import Organization
from pactor.plans.models import Plan
from pactor.signatures import services as signatures
from pactor.signatures.simulator import SimulatorProvider
from tests import factories
from tests.integration.test_signatures import SIGNERS, Approved, _approved

pytestmark = pytest.mark.django_db(transaction=True)
JSON = "application/json"


def _contract(org: Organization, contract_id: uuid.UUID) -> Contract:
    with tenant_context(org.id):
        return Contract.objects.get(id=contract_id)


def _sign(a: Approved, contract: Contract | None = None) -> None:
    target = contract or a.contract
    with tenant_context(a.org.id):
        fresh = Contract.objects.get(id=target.id)
        envelope = signatures.send_for_signature(
            factories.principal_for_owner(a.org, a.manager),
            target.id,
            signers=SIGNERS,
            expected_fingerprint=fresh.approved_fingerprint,
        )
    signatures.dispatch(a.org.id, envelope.id)
    with tenant_context(a.org.id):
        provider_id = envelope.__class__.objects.get(id=envelope.id).provider_envelope_id
    simulator = SimulatorProvider()
    for key in ("1", "2"):
        webhook = simulator.sign(provider_id, key)
        signatures.ingest_webhook("simulator", dict(webhook.headers), webhook.body)


def _approved_with_dates(start: date | None, end: date | None) -> Approved:
    a = _approved()
    with tenant_context(a.org.id):
        Contract.objects.filter(id=a.contract.id).update(planned_start=start, planned_end=end)
    return a


def _approve(a: Approved, contract: Contract) -> None:
    with tenant_context(a.org.id):
        principal = factories.principal_for_owner(a.org, a.manager)
        fresh = Contract.objects.get(id=contract.id)
        revision = fresh.current_revision
        assert revision is not None
        round_ = approvals.submit_for_approval(
            principal, contract.id, expected_fingerprint=revision.fingerprint
        )
        approvals.cast_vote(
            factories.principal(a.org, a.approver),
            round_.id,
            step_key="gestao",
            decision=Decision.APPROVE,
            expected_fingerprint=revision.fingerprint,
        )


def test_signed_with_future_start_awaits_and_scheduler_starts_and_ends() -> None:
    today = timezone.localdate()
    a = _approved_with_dates(today + timedelta(days=5), today + timedelta(days=30))
    _sign(a)
    contract = _contract(a.org, a.contract.id)
    assert contract.drafting_status == "signed"
    assert contract.lifecycle_status == "awaiting_start"
    assert lifecycle.advance_due(a.org.id, today=today + timedelta(days=4)) == 0
    assert lifecycle.advance_due(a.org.id, today=today + timedelta(days=5)) == 1
    assert _contract(a.org, a.contract.id).lifecycle_status == "active"
    assert lifecycle.advance_due(a.org.id, today=today + timedelta(days=30)) == 0
    assert lifecycle.advance_due(a.org.id, today=today + timedelta(days=31)) == 1
    assert _contract(a.org, a.contract.id).lifecycle_status == "ended"
    assert lifecycle.advance_due(a.org.id, today=today + timedelta(days=31)) == 0
    with tenant_context(a.org.id):
        actions = list(
            LifecycleEvent.objects.filter(contract=a.contract)
            .order_by("created_at")
            .values_list("action", flat=True)
        )
    assert actions == ["register_signature", "start", "expire"]


def test_signed_without_planned_start_is_active_and_indefinite() -> None:
    a = _approved_with_dates(None, None)
    _sign(a)
    contract = _contract(a.org, a.contract.id)
    assert contract.lifecycle_status == "active"
    assert contract.effective_start == contract.signed_on
    assert contract.effective_end is None


def test_scheduler_outage_records_both_transitions() -> None:
    today = timezone.localdate()
    a = _approved_with_dates(today + timedelta(days=1), today + timedelta(days=2))
    _sign(a)
    assert lifecycle.advance_due(a.org.id, today=today + timedelta(days=90)) == 2
    assert _contract(a.org, a.contract.id).lifecycle_status == "ended"


def test_lifecycle_requires_signature_in_database() -> None:
    a = _approved()
    with pytest.raises(IntegrityError), tenant_context(a.org.id), transaction.atomic():
        Contract.objects.filter(id=a.contract.id).update(lifecycle_status="active")


def test_manual_actions_require_reason_and_compatible_state() -> None:
    a = _approved_with_dates(None, None)
    _sign(a)
    principal = factories.principal_for_owner
    today = timezone.localdate()
    with tenant_context(a.org.id):
        with pytest.raises(DomainValidationError, match="justificativa"):
            lifecycle.suspend(
                principal(a.org, a.manager), a.contract.id, reason=" ", effective_date=today
            )
        with pytest.raises(InvalidTransitionError):
            lifecycle.archive(
                principal(a.org, a.manager), a.contract.id, reason="cedo", effective_date=today
            )
        lifecycle.suspend(
            principal(a.org, a.manager),
            a.contract.id,
            reason="Obra interrompida",
            effective_date=today,
        )
        lifecycle.resume(
            principal(a.org, a.manager), a.contract.id, reason="Retomada", effective_date=today
        )
        with pytest.raises(DomainValidationError, match="anterior"):
            lifecycle.terminate(
                principal(a.org, a.manager),
                a.contract.id,
                reason="x",
                effective_date=today - timedelta(days=400),
            )
        lifecycle.terminate(
            principal(a.org, a.manager),
            a.contract.id,
            reason="Rescisão amigável registrada",
            effective_date=today,
        )
        lifecycle.archive(
            principal(a.org, a.manager),
            a.contract.id,
            reason="Encerramento administrativo",
            effective_date=today,
        )
        with pytest.raises(InvalidTransitionError):
            lifecycle.resume(
                principal(a.org, a.manager), a.contract.id, reason="x", effective_date=today
            )
    contract = _contract(a.org, a.contract.id)
    assert contract.lifecycle_status == "archived"
    assert contract.effective_end == today


def test_suspension_follows_organization_policy() -> None:
    a = _approved_with_dates(None, None)
    _sign(a)
    with tenant_context(a.org.id):
        LifecyclePolicy.objects.update_or_create(
            organization_id=a.org.id, defaults={"allow_suspension": False}
        )
        with pytest.raises(InvalidTransitionError, match="política"):
            lifecycle.suspend(
                factories.principal_for_owner(a.org, a.manager),
                a.contract.id,
                reason="x",
                effective_date=timezone.localdate(),
            )


def test_amendment_changes_resolve_conditions_by_date_without_rewriting_original() -> None:
    a = _approved_with_dates(None, date(2027, 12, 31))
    _sign(a)
    with tenant_context(a.org.id):
        principal = factories.principal_for_owner(a.org, a.manager)
        amendment = contracts.create_contract(
            principal,
            contracts.ContractInput(
                title="Primeiro aditivo (exemplo fictício)",
                contract_type=ContractType.AMENDMENT,
                counterparty_id=a.contract.counterparty_id,
                organization_role=PartyRole.CONTRACTOR,
                counterparty_role=PartyRole.CONTRACTED,
                owner_id=a.manager.id,
                parent_id=a.contract.id,
                initial_content="Altera o valor e prorroga o prazo.",
            ),
        )
        lifecycle.add_amendment_change(
            principal,
            amendment.id,
            field=ConditionField.TOTAL_VALUE,
            value="7500.00",
            effective_from=date(2027, 6, 1),
        )
        lifecycle.add_amendment_change(
            principal,
            amendment.id,
            field=ConditionField.EFFECTIVE_END,
            value="2028-12-31",
            effective_from=date(2027, 6, 1),
        )
        with pytest.raises(DomainValidationError):
            lifecycle.add_amendment_change(
                principal,
                amendment.id,
                field=ConditionField.TOTAL_VALUE,
                value="1.001",
                effective_from=date(2027, 6, 1),
            )
        with pytest.raises(DomainValidationError, match="aditivos"):
            lifecycle.add_amendment_change(
                principal,
                a.contract.id,
                field=ConditionField.TOTAL_VALUE,
                value="1",
                effective_from=date(2027, 6, 1),
            )
        revision = contracts.publish_revision(principal, amendment.id, expected_draft_version=1)
        assert len(revision.metadata["amendment_changes"]) == 2
        before = lifecycle.current_conditions(principal, a.contract.id, on=date(2027, 7, 1))
        assert before.total_value == Decimal("5000.00")  # aditivo ainda não assinado
    _approve(a, amendment)
    _sign(a, amendment)
    with tenant_context(a.org.id):
        principal = factories.principal_for_owner(a.org, a.manager)
        early = lifecycle.current_conditions(principal, a.contract.id, on=date(2027, 5, 31))
        late = lifecycle.current_conditions(principal, a.contract.id, on=date(2027, 6, 1))
        original = Contract.objects.get(id=a.contract.id)
    assert (early.total_value, early.effective_end) == (Decimal("5000.00"), date(2027, 12, 31))
    assert (late.total_value, late.effective_end) == (Decimal("7500.00"), date(2028, 12, 31))
    assert original.total_value == Decimal("5000.00")
    client = factories.client_for(a.manager, a.org)
    conditions = client.get(f"/api/v1/contracts/{a.contract.id}/conditions?on=2027-06-01").json()
    assert conditions == {
        "on": "2027-06-01",
        "total_value": "7500.00",
        "effective_end": "2028-12-31",
        "description": "",
    }
    listed = client.get(f"/api/v1/contracts/{amendment.id}/amendment-changes").json()
    assert [c["field"] for c in listed] == ["total_value", "effective_end"]


def test_amendment_change_after_approval_invalidates_it() -> None:
    a = _approved_with_dates(None, None)
    with tenant_context(a.org.id):
        principal = factories.principal_for_owner(a.org, a.manager)
        amendment = contracts.create_contract(
            principal,
            contracts.ContractInput(
                title="Aditivo (exemplo fictício)",
                contract_type=ContractType.AMENDMENT,
                counterparty_id=a.contract.counterparty_id,
                organization_role=PartyRole.CONTRACTOR,
                counterparty_role=PartyRole.CONTRACTED,
                owner_id=a.manager.id,
                parent_id=a.contract.id,
                initial_content="Texto do aditivo.",
            ),
        )
        contracts.publish_revision(principal, amendment.id, expected_draft_version=1)
    _approve(a, amendment)
    assert _contract(a.org, amendment.id).drafting_status == "approved"
    with tenant_context(a.org.id):
        lifecycle.add_amendment_change(
            factories.principal_for_owner(a.org, a.manager),
            amendment.id,
            field=ConditionField.DESCRIPTION,
            value="Nova cláusula",
            effective_from=date(2027, 1, 1),
        )
    stored = _contract(a.org, amendment.id)
    assert stored.drafting_status == "draft"
    assert stored.approved_revision_id is None


def test_active_contract_limit_is_enforced_when_sending_for_signature() -> None:
    org, _ = factories.organization(plan_obj=factories.plan("um-ativo", max_users=10))
    Plan.objects.filter(code="um-ativo").update(max_active_contracts=1)
    first = _approved(org)
    _sign(first)
    second = _approved(org)
    with tenant_context(org.id), pytest.raises(PlanLimitExceededError, match="contratos ativos"):
        signatures.send_for_signature(
            factories.principal_for_owner(org, second.manager),
            second.contract.id,
            signers=SIGNERS,
            expected_fingerprint=second.revision.fingerprint,
        )


def test_lifecycle_api() -> None:
    a = _approved_with_dates(None, None)
    _sign(a)
    client = factories.client_for(a.manager, a.org)
    base = f"/api/v1/contracts/{a.contract.id}/lifecycle"
    assert client.get(base).json()["lifecycle_status"] == "active"
    today = timezone.localdate().isoformat()
    suspended = client.post(
        f"{base}/suspend", {"reason": "Pausa acordada", "effective_date": today}, content_type=JSON
    )
    assert suspended.status_code == 200, suspended.json()
    assert suspended.json()["lifecycle_status"] == "suspended"
    assert [e["action"] for e in suspended.json()["events"]] == ["register_signature", "suspend"]
    assert (
        client.post(
            f"{base}/explode", {"reason": "x", "effective_date": today}, content_type=JSON
        ).status_code
        == 404
    )
    approver = factories.client_for(a.approver, a.org)
    assert (
        approver.post(
            f"{base}/resume", {"reason": "x", "effective_date": today}, content_type=JSON
        ).status_code
        == 403
    )
    assert client.get(f"/api/v1/contracts/{a.contract.id}/conditions").status_code == 200


def test_scheduler_task_iterates_organizations() -> None:
    a = _approved_with_dates(timezone.localdate() - timedelta(days=1), None)
    _sign(a)
    assert _contract(a.org, a.contract.id).lifecycle_status == "active"
    assert advance_lifecycles.apply().result == 0
