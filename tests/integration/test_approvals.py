"""Aprovações vinculadas à revisão: workflows, quórum, delegação e bloqueios."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from pactor.access.policies import Principal, Role
from pactor.approvals import services
from pactor.approvals.domain import Decision
from pactor.approvals.models import ApprovalRound, Vote
from pactor.contracts import services as contracts
from pactor.contracts.models import Contract, ContractGrant, Revision
from pactor.core.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    InvalidTransitionError,
    NotFoundError,
)
from pactor.core.tenancy import tenant_context
from pactor.identity import services as identity
from pactor.identity.models import Membership, Organization, User
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)
JSON = "application/json"


@dataclass(frozen=True)
class Setup:
    org: Organization
    admin: User
    manager: User
    legal_a: User
    legal_b: User
    finance: User
    contract: Contract
    revision: Revision


def _step(key: str, approvers: list[User], *, order: int = 1, quorum: int = 1) -> dict[str, object]:
    return {
        "key": key,
        "name": key.capitalize(),
        "order": order,
        "quorum": quorum,
        "approver_ids": [str(a.id) for a in approvers],
    }


def _setup(
    *,
    value: str = "80000.00",
    confidentiality: str = "internal",
    self_approval_blocked: bool = True,
) -> Setup:
    org, admin = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER, Role.APPROVER])
    legal_a = factories.member(org, [Role.APPROVER])
    legal_b = factories.member(org, [Role.APPROVER])
    finance = factories.member(org, [Role.APPROVER, Role.FINANCE])
    rules = [
        {
            "name": "Alto valor",
            "conditions": [{"field": "total_value", "operator": "gte", "value": "50000.00"}],
            "steps": [
                _step("juridico", [legal_a, legal_b], quorum=2),
                _step("financeiro", [finance], order=2),
            ],
        },
        {"name": "Padrão", "conditions": [], "steps": [_step("gestao", [legal_a, manager])]},
    ]
    with tenant_context(org.id):
        services.publish_workflow(
            factories.principal(org, admin),
            name="Aprovação padrão",
            rules=rules,
            prohibit_self_approval=self_approval_blocked,
        )
    contract = factories.contract(org, manager, confidentiality=confidentiality, total_value=None)
    with tenant_context(org.id):
        principal = factories.principal_for_owner(org, manager)
        contracts.update_metadata(
            principal, contract.id, expected_version=1, changes={"total_value": Decimal(value)}
        )
        revision = contracts.publish_revision(principal, contract.id, expected_draft_version=1)
    return Setup(org, admin, manager, legal_a, legal_b, finance, contract, revision)


def _principal(s: Setup, user: User) -> Principal:
    return factories.principal(s.org, user)


def _submit(s: Setup) -> ApprovalRound:
    with tenant_context(s.org.id):
        return services.submit_for_approval(
            _principal(s, s.manager), s.contract.id, expected_fingerprint=s.revision.fingerprint
        )


def _vote(
    s: Setup,
    round_: ApprovalRound,
    user: User,
    step: str,
    decision: Decision = Decision.APPROVE,
    *,
    fingerprint: str | None = None,
    on_behalf_of: uuid.UUID | None = None,
) -> Vote:
    with tenant_context(s.org.id):
        return services.cast_vote(
            _principal(s, user),
            round_.id,
            step_key=step,
            decision=decision,
            expected_fingerprint=fingerprint or round_.fingerprint,
            on_behalf_of=on_behalf_of,
        )


def _contract(s: Setup) -> Contract:
    with tenant_context(s.org.id):
        return Contract.objects.get(id=s.contract.id)


def test_full_approval_binds_exact_revision_and_fingerprint() -> None:
    s = _setup()
    round_ = _submit(s)
    assert _contract(s).drafting_status == "in_approval"
    assert round_.rule_index == 0
    assert round_.workflow_version == 1
    _vote(s, round_, s.legal_a, "juridico")
    with pytest.raises(ConflictError, match="não está aguardando"):
        _vote(s, round_, s.finance, "financeiro")
    _vote(s, round_, s.legal_b, "juridico")
    assert _contract(s).drafting_status == "in_approval"
    _vote(s, round_, s.finance, "financeiro")
    contract = _contract(s)
    assert contract.drafting_status == "approved"
    assert contract.approved_revision_id == s.revision.id
    assert contract.approved_fingerprint == s.revision.fingerprint
    with tenant_context(s.org.id):
        services.require_approved_for_signature(contract)
        assert ApprovalRound.objects.get(id=round_.id).status == "approved"
        votes = Vote.objects.filter(round=round_)
        assert {v.revision_id for v in votes} == {s.revision.id}
        assert {v.fingerprint for v in votes} == {s.revision.fingerprint}


def test_submission_requires_current_fingerprint_and_published_revision() -> None:
    s = _setup()
    with tenant_context(s.org.id), pytest.raises(ConflictError, match="mudou"):
        services.submit_for_approval(
            _principal(s, s.manager), s.contract.id, expected_fingerprint="0" * 64
        )
    contract = factories.contract(s.org, s.manager)
    with tenant_context(s.org.id), pytest.raises(InvalidTransitionError, match="Publique"):
        services.submit_for_approval(
            _principal(s, s.manager), contract.id, expected_fingerprint="0" * 64
        )
    _submit(s)
    with tenant_context(s.org.id), pytest.raises(InvalidTransitionError):
        services.submit_for_approval(
            _principal(s, s.manager), s.contract.id, expected_fingerprint=s.revision.fingerprint
        )


def test_vote_with_fingerprint_of_another_content_is_rejected() -> None:
    s = _setup()
    round_ = _submit(s)
    with pytest.raises(ConflictError, match="não corresponde"):
        _vote(s, round_, s.legal_a, "juridico", fingerprint="f" * 64)


def test_new_revision_during_approval_supersedes_round_and_rejects_old_votes() -> None:
    s = _setup()
    round_ = _submit(s)
    _vote(s, round_, s.legal_a, "juridico")
    with tenant_context(s.org.id):
        principal = factories.principal_for_owner(s.org, s.manager)
        draft = contracts.save_draft(
            principal, s.contract.id, content="Cláusula 1. Novo objeto.", expected_version=1
        )
        contracts.publish_revision(principal, s.contract.id, expected_draft_version=draft.version)
        assert ApprovalRound.objects.get(id=round_.id).status == "superseded"
        assert not ContractGrant.objects.filter(source="approval", source_id=round_.id).exists()
    assert _contract(s).drafting_status == "draft"
    with pytest.raises(ConflictError, match="encerrada"):
        _vote(s, round_, s.legal_b, "juridico")


def test_approval_is_invalidated_when_new_revision_is_published_after_it() -> None:
    s = _setup(value="100.00")
    round_ = _submit(s)
    _vote(s, round_, s.legal_a, "gestao")
    assert _contract(s).drafting_status == "approved"
    with tenant_context(s.org.id):
        principal = factories.principal_for_owner(s.org, s.manager)
        contracts.save_draft(
            principal, s.contract.id, content="Texto diferente do aprovado", expected_version=1
        )
        contracts.publish_revision(principal, s.contract.id, expected_draft_version=2)
    contract = _contract(s)
    assert contract.drafting_status == "draft"
    assert contract.approved_revision_id is None
    with tenant_context(s.org.id), pytest.raises(InvalidTransitionError):
        services.require_approved_for_signature(contract)


def test_self_approval_is_prohibited_for_author() -> None:
    s = _setup(value="100.00")
    round_ = _submit(s)
    # O gestor é autor da revisão e ocupa vaga na etapa "gestao".
    with pytest.raises(AccessDeniedError, match="Autoaprovação"):
        _vote(s, round_, s.manager, "gestao")
    _vote(s, round_, s.legal_a, "gestao")
    assert _contract(s).drafting_status == "approved"


def test_self_approval_allowed_when_workflow_permits() -> None:
    s = _setup(value="100.00", self_approval_blocked=False)
    round_ = _submit(s)
    _vote(s, round_, s.manager, "gestao")
    assert _contract(s).drafting_status == "approved"


def test_rejection_and_change_request_return_to_draft() -> None:
    s = _setup(value="100.00")
    round_ = _submit(s)
    _vote(s, round_, s.legal_a, "gestao", Decision.REJECT)
    assert _contract(s).drafting_status == "draft"
    with tenant_context(s.org.id):
        assert ApprovalRound.objects.get(id=round_.id).status == "rejected"
    second = _submit(s)
    _vote(s, second, s.legal_a, "gestao", Decision.REQUEST_CHANGES)
    with tenant_context(s.org.id):
        assert ApprovalRound.objects.get(id=second.id).status == "changes_requested"


def test_delegation_votes_once_and_never_double_counts() -> None:
    s = _setup()
    round_ = _submit(s)
    now = timezone.now()
    with tenant_context(s.org.id):
        services.create_delegation(
            _principal(s, s.legal_b),
            delegate_id=s.legal_a.id,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(days=3),
            reason="Férias",
        )
    _vote(s, round_, s.legal_a, "juridico")
    # legal_a já votou pela própria vaga: votar também por legal_b contaria duas vezes.
    with pytest.raises(ConflictError, match="Voto já registrado"):
        _vote(s, round_, s.legal_a, "juridico", on_behalf_of=s.legal_b.id)
    with tenant_context(s.org.id):
        assert Vote.objects.filter(round=round_).count() == 1
    assert _contract(s).drafting_status == "in_approval"


def test_delegate_votes_on_behalf_of_absent_approver() -> None:
    s = _setup()
    round_ = _submit(s)
    now = timezone.now()
    with tenant_context(s.org.id):
        services.create_delegation(
            _principal(s, s.finance),
            delegate_id=s.legal_b.id,
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(days=1),
            reason="Afastamento",
        )
    _vote(s, round_, s.legal_a, "juridico")
    _vote(s, round_, s.legal_b, "juridico")
    vote = _vote(s, round_, s.legal_b, "financeiro", on_behalf_of=s.finance.id)
    assert vote.approver_id == s.finance.id
    assert vote.cast_by_id == s.legal_b.id
    assert vote.delegation_id is not None
    assert _contract(s).drafting_status == "approved"


def test_expired_or_revoked_delegation_is_not_accepted() -> None:
    s = _setup()
    round_ = _submit(s)
    now = timezone.now()
    with tenant_context(s.org.id):
        expired = services.create_delegation(
            _principal(s, s.finance),
            delegate_id=s.legal_b.id,
            starts_at=now - timedelta(days=3),
            ends_at=now - timedelta(days=1),
            reason="Passado",
        )
        del expired
    _vote(s, round_, s.legal_a, "juridico")
    _vote(s, round_, s.legal_b, "juridico")
    with pytest.raises(AccessDeniedError, match="Delegação"):
        _vote(s, round_, s.legal_b, "financeiro", on_behalf_of=s.finance.id)
    with tenant_context(s.org.id):
        active = services.create_delegation(
            _principal(s, s.finance),
            delegate_id=s.legal_b.id,
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(days=1),
            reason="Atual",
        )
        services.revoke_delegation(_principal(s, s.finance), active.id)
        with pytest.raises(ConflictError):
            services.revoke_delegation(_principal(s, s.finance), active.id)
    with pytest.raises(AccessDeniedError, match="Delegação"):
        _vote(s, round_, s.legal_b, "financeiro", on_behalf_of=s.finance.id)


def test_delegation_validations() -> None:
    s = _setup()
    now = timezone.now()
    principal = _principal(s, s.legal_a)
    with tenant_context(s.org.id):
        with pytest.raises(DomainValidationError, match="si mesmo"):
            services.create_delegation(
                principal,
                delegate_id=s.legal_a.id,
                starts_at=now,
                ends_at=now + timedelta(days=1),
                reason="x",
            )
        with pytest.raises(DomainValidationError, match="Período"):
            services.create_delegation(
                principal, delegate_id=s.legal_b.id, starts_at=now, ends_at=now, reason="x"
            )
        with pytest.raises(DomainValidationError, match="motivo"):
            services.create_delegation(
                principal,
                delegate_id=s.legal_b.id,
                starts_at=now,
                ends_at=now + timedelta(days=1),
                reason=" ",
            )
        with pytest.raises(DomainValidationError, match="delegado"):
            services.create_delegation(
                principal,
                delegate_id=s.admin.id,
                starts_at=now,
                ends_at=now + timedelta(days=1),
                reason="x",
            )
        services.create_delegation(
            principal,
            delegate_id=s.legal_b.id,
            starts_at=now,
            ends_at=now + timedelta(days=2),
            reason="x",
        )
        with pytest.raises(ConflictError, match="período"):
            services.create_delegation(
                principal,
                delegate_id=s.finance.id,
                starts_at=now + timedelta(days=1),
                ends_at=now + timedelta(days=3),
                reason="x",
            )


def test_lost_access_blocks_step_until_approver_is_replaced() -> None:
    s = _setup()
    round_ = _submit(s)
    _vote(s, round_, s.legal_a, "juridico")
    with tenant_context(s.org.id):
        membership = Membership.objects.get(user=s.legal_b)
        identity.revoke_membership(_principal(s, s.admin), membership.id, reason="Desligamento")
        evaluation = services._evaluate(ApprovalRound.objects.get(id=round_.id))
        assert evaluation.steps["juridico"].value == "blocked"
    replacement = factories.member(s.org, [Role.APPROVER])
    with tenant_context(s.org.id):
        with pytest.raises(DomainValidationError, match="justificativa"):
            services.replace_approver(
                _principal(s, s.admin),
                round_.id,
                step_key="juridico",
                old_user_id=s.legal_b.id,
                new_user_id=replacement.id,
                reason=" ",
            )
        with pytest.raises(ConflictError, match="já votou"):
            services.replace_approver(
                _principal(s, s.admin),
                round_.id,
                step_key="juridico",
                old_user_id=s.legal_a.id,
                new_user_id=replacement.id,
                reason="x",
            )
        services.replace_approver(
            _principal(s, s.admin),
            round_.id,
            step_key="juridico",
            old_user_id=s.legal_b.id,
            new_user_id=replacement.id,
            reason="Substituição por desligamento",
        )
        assert ContractGrant.objects.filter(source_id=round_.id, user=replacement).exists()
    _vote(s, round_, replacement, "juridico")
    with tenant_context(s.org.id):
        assert services._evaluate(ApprovalRound.objects.get(id=round_.id)).active_steps() == {
            "financeiro"
        }


def test_non_slot_holder_and_non_approver_cannot_vote() -> None:
    s = _setup()
    round_ = _submit(s)
    with pytest.raises(AccessDeniedError, match="não é aprovador"):
        _vote(s, round_, s.finance, "juridico")
    with tenant_context(s.org.id), pytest.raises(AccessDeniedError):
        services.cast_vote(
            factories.principal(s.org, s.admin),
            round_.id,
            step_key="juridico",
            decision=Decision.APPROVE,
            expected_fingerprint=round_.fingerprint,
        )


def test_votes_are_append_only() -> None:
    s = _setup(value="100.00")
    round_ = _submit(s)
    vote = _vote(s, round_, s.legal_a, "gestao", Decision.REJECT)
    with (
        pytest.raises(Exception, match="somente-inserção"),
        tenant_context(s.org.id),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE approvals_vote SET decision = 'approve' WHERE id = %s", [vote.id])


def test_workflow_versions_are_recorded_and_rounds_keep_their_rule() -> None:
    s = _setup(value="100.00")
    first = _submit(s)
    rules = [{"name": "Nova", "conditions": [], "steps": [_step("diretoria", [s.finance])]}]
    with tenant_context(s.org.id):
        v2 = services.publish_workflow(_principal(s, s.admin), name="Aprovação padrão", rules=rules)
        assert v2.version == 2
        stored = ApprovalRound.objects.get(id=first.id)
        assert stored.workflow_version == 1
        assert stored.rule_snapshot["name"] == "Padrão"


def test_workflow_publication_validations_and_permissions() -> None:
    s = _setup()
    with tenant_context(s.org.id):
        with pytest.raises(DomainValidationError, match="padrão"):
            services.publish_workflow(
                _principal(s, s.admin),
                name="X",
                rules=[
                    {
                        "name": "a",
                        "conditions": [{"field": "total_value", "operator": "gt", "value": "1"}],
                        "steps": [_step("a", [s.finance])],
                    }
                ],
            )
        with pytest.raises(DomainValidationError, match="Aprovadores"):
            services.publish_workflow(
                _principal(s, s.admin),
                name="X",
                rules=[{"name": "a", "conditions": [], "steps": [_step("a", [s.admin])]}],
            )
        with pytest.raises(DomainValidationError, match="nome"):
            services.publish_workflow(
                _principal(s, s.admin),
                name=" ",
                rules=[{"name": "a", "conditions": [], "steps": [_step("a", [s.finance])]}],
            )
        with pytest.raises(AccessDeniedError):
            services.publish_workflow(_principal(s, s.manager), name="X", rules=[])


def test_approvers_see_confidential_contract_only_while_round_is_open() -> None:
    s = _setup(value="100.00", confidentiality="confidential")
    legal_client = factories.client_for(s.legal_a, s.org)
    url = f"/api/v1/contracts/{s.contract.id}"
    assert legal_client.get(url).status_code == 404
    round_ = _submit(s)
    assert legal_client.get(url).status_code == 200
    inbox = legal_client.get("/api/v1/approval-rounds/inbox").json()
    assert [r["id"] for r in inbox] == [str(round_.id)]
    assert inbox[0]["revision_content"] == s.revision.content
    _vote(s, round_, s.legal_a, "gestao")
    assert legal_client.get(url).status_code == 404
    assert legal_client.get("/api/v1/approval-rounds/inbox").json() == []


def test_api_flow_submit_vote_and_listing() -> None:
    s = _setup(value="100.00")
    manager_client = factories.client_for(s.manager, s.org)
    submitted = manager_client.post(
        "/api/v1/approval-rounds/submit",
        {"contract_id": str(s.contract.id), "expected_fingerprint": s.revision.fingerprint},
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="enviar-aprovacao-1",
    )
    assert submitted.status_code == 201, submitted.json()
    round_id = submitted.json()["id"]
    assert submitted.json()["steps"][0]["state"] == "active"
    legal_client = factories.client_for(s.legal_a, s.org)
    payload = {
        "step_key": "gestao",
        "decision": "approve",
        "expected_fingerprint": s.revision.fingerprint,
    }
    voted = legal_client.post(
        f"/api/v1/approval-rounds/{round_id}/vote",
        payload,
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="voto-unico-1",
    )
    assert voted.status_code == 201, voted.json()
    replay = legal_client.post(
        f"/api/v1/approval-rounds/{round_id}/vote",
        payload,
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="voto-unico-1",
    )
    assert replay.json()["id"] == voted.json()["id"]
    listing = manager_client.get(f"/api/v1/approval-rounds?contract_id={s.contract.id}").json()
    assert listing[0]["status"] == "approved"
    detail = manager_client.get(f"/api/v1/approval-rounds/{round_id}").json()
    assert detail["votes"][0]["fingerprint"] == s.revision.fingerprint
    assert manager_client.get(f"/api/v1/approval-rounds/{uuid.uuid4()}").status_code == 404


def test_rounds_are_isolated_between_organizations() -> None:
    s = _setup(value="100.00")
    round_ = _submit(s)
    org_b, _ = factories.organization()
    outsider = factories.member(org_b, [Role.APPROVER, Role.CONTRACT_MANAGER])
    client = factories.client_for(outsider, org_b)
    assert client.get(f"/api/v1/approval-rounds/{round_.id}").status_code == 404
    assert client.get("/api/v1/approval-rounds/inbox").json() == []
    vote = client.post(
        f"/api/v1/approval-rounds/{round_.id}/vote",
        {"step_key": "gestao", "decision": "approve", "expected_fingerprint": round_.fingerprint},
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="voto-estrangeiro",
    )
    assert vote.status_code == 404


def test_workflow_and_delegation_api() -> None:
    s = _setup()
    admin_client = factories.client_for(s.admin, s.org)
    listing = admin_client.get("/api/v1/workflows").json()["results"]
    assert listing[0]["version"] == 1
    created = admin_client.post(
        "/api/v1/workflows",
        {
            "name": "Aprovação padrão",
            "rules": [{"name": "Padrão", "conditions": [], "steps": [_step("x", [s.finance])]}],
        },
        content_type=JSON,
    )
    assert created.status_code == 201
    assert created.json()["version"] == 2
    legal = factories.client_for(s.legal_a, s.org)
    now = timezone.now()
    delegation = legal.post(
        "/api/v1/delegations",
        {
            "delegate_id": str(s.legal_b.id),
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(days=2)).isoformat(),
            "reason": "Viagem",
        },
        content_type=JSON,
    )
    assert delegation.status_code == 201, delegation.json()
    assert len(legal.get("/api/v1/delegations").json()) == 1
    assert len(factories.client_for(s.legal_b, s.org).get("/api/v1/delegations").json()) == 1
    assert legal.post(f"/api/v1/delegations/{delegation.json()['id']}/revoke").json()["revoked_at"]


def test_approval_rejection_paths() -> None:
    s = _setup()
    principal = _principal(s, s.admin)
    with tenant_context(s.org.id):
        with pytest.raises(NotFoundError):
            services.cast_vote(
                _principal(s, s.legal_a),
                uuid.uuid4(),
                step_key="x",
                decision=Decision.APPROVE,
                expected_fingerprint="0" * 64,
            )
        with pytest.raises(NotFoundError):
            services.replace_approver(
                principal,
                uuid.uuid4(),
                step_key="x",
                old_user_id=s.legal_a.id,
                new_user_id=s.legal_b.id,
                reason="x",
            )
        with pytest.raises(NotFoundError):
            services.revoke_delegation(_principal(s, s.legal_a), uuid.uuid4())
    round_ = _submit(s)
    with pytest.raises(NotFoundError, match="Etapa"):
        _vote(s, round_, s.legal_a, "inexistente")
    with tenant_context(s.org.id):
        contract = Contract.objects.get(id=s.contract.id)
        with pytest.raises(InvalidTransitionError):
            services.require_approved_for_signature(contract)
        with pytest.raises(NotFoundError, match="Etapa"):
            services.replace_approver(
                principal,
                round_.id,
                step_key="x",
                old_user_id=s.legal_a.id,
                new_user_id=s.legal_b.id,
                reason="x",
            )
        with pytest.raises(NotFoundError, match="Aprovador"):
            services.replace_approver(
                principal,
                round_.id,
                step_key="juridico",
                old_user_id=s.finance.id,
                new_user_id=s.manager.id,
                reason="x",
            )
        with pytest.raises(DomainValidationError, match="Substituto"):
            services.replace_approver(
                principal,
                round_.id,
                step_key="juridico",
                old_user_id=s.legal_b.id,
                new_user_id=s.admin.id,
                reason="x",
            )
        with pytest.raises(ConflictError, match="já ocupa"):
            services.replace_approver(
                principal,
                round_.id,
                step_key="juridico",
                old_user_id=s.legal_b.id,
                new_user_id=s.legal_a.id,
                reason="x",
            )


def test_submission_without_workflow_and_closed_round_operations() -> None:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, manager)
    with tenant_context(org.id):
        principal = factories.principal_for_owner(org, manager)
        revision = contracts.publish_revision(principal, contract.id, expected_draft_version=1)
        with pytest.raises(ConflictError, match="Nenhum workflow"):
            services.submit_for_approval(
                principal, contract.id, expected_fingerprint=revision.fingerprint
            )
    s = _setup(value="100.00")
    round_ = _submit(s)
    _vote(s, round_, s.legal_a, "gestao")
    with tenant_context(s.org.id), pytest.raises(ConflictError, match="encerrada"):
        services.replace_approver(
            _principal(s, s.admin),
            round_.id,
            step_key="gestao",
            old_user_id=s.manager.id,
            new_user_id=s.legal_b.id,
            reason="x",
        )
