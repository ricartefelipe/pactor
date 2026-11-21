"""Caminhos de rejeição de serviços de identidade, planos, tenancy e idempotência."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from pactor.access.policies import Role
from pactor.core import idempotency
from pactor.core.errors import (
    ConflictError,
    DomainValidationError,
    IdempotencyConflictError,
    NotFoundError,
    PlanLimitExceededError,
)
from pactor.core.tenancy import TenantContextConflictError, bind_user, tenant_context
from pactor.identity import services
from pactor.identity.invitations import make_token
from pactor.identity.models import Membership, MembershipKind
from pactor.plans import services as plans
from pactor.plans.models import Module, Subscription
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


def test_unknown_departments_teams_and_memberships_are_rejected() -> None:
    org, owner = factories.organization()
    principal = factories.principal(org, owner)
    person = factories.member(org)
    with tenant_context(org.id):
        membership = Membership.objects.get(user=person)
        with pytest.raises(DomainValidationError, match="Departamento"):
            services.update_membership(
                principal,
                membership.id,
                roles=["approver"],
                department_ids=[uuid.uuid4()],
                team_ids=[],
            )
        with pytest.raises(DomainValidationError, match="Equipe"):
            services.update_membership(
                principal,
                membership.id,
                roles=["approver"],
                department_ids=[],
                team_ids=[uuid.uuid4()],
            )
        with pytest.raises(NotFoundError):
            services.update_membership(
                principal, uuid.uuid4(), roles=["approver"], department_ids=[], team_ids=[]
            )
        with pytest.raises(NotFoundError):
            services.revoke_invitation(principal, uuid.uuid4())


def test_departments_from_other_organization_are_not_accepted() -> None:
    org_a, owner_a = factories.organization()
    org_b, _ = factories.organization()
    foreign = factories.department(org_b)
    with tenant_context(org_a.id), pytest.raises(DomainValidationError):
        services.invite(
            factories.principal(org_a, owner_a),
            email="h@exemplo.test",
            kind=MembershipKind.INTERNAL,
            roles=["approver"],
            department_ids=[foreign.id],
        )


def test_blank_and_duplicate_structure_names() -> None:
    org, owner = factories.organization()
    principal = factories.principal(org, owner)
    with tenant_context(org.id):
        with pytest.raises(DomainValidationError):
            services.create_department(principal, name=" ")
        with pytest.raises(DomainValidationError):
            services.create_team(principal, name="")
        services.create_team(principal, name="Time")
        with pytest.raises(ConflictError):
            services.create_team(principal, name="time")


def test_accepting_invitation_when_already_member_conflicts() -> None:
    org, owner = factories.organization()
    principal = factories.principal(org, owner)
    person = factories.user()
    with tenant_context(org.id):
        invitation = services.invite(
            principal, email=person.email, kind=MembershipKind.INTERNAL, roles=["approver"]
        )
    factories.member(org, [Role.REVIEWER], person=person)
    with pytest.raises(ConflictError):
        services.accept_invitation(person, make_token(org.id, invitation.id))


def test_owner_can_transfer_ownership_when_another_owner_exists() -> None:
    org, owner = factories.organization()
    second = factories.member(org, [Role.OWNER])
    principal = factories.principal(org, owner)
    with tenant_context(org.id):
        own = Membership.objects.get(user=owner)
        services.update_membership(
            principal, own.id, roles=["admin"], department_ids=[], team_ids=[]
        )
        assert Membership.objects.get(user=second).roles == ["owner"]


def test_admin_cannot_revoke_owner() -> None:
    org, owner = factories.organization()
    admin = factories.member(org, [Role.ADMIN])
    factories.member(org, [Role.OWNER])
    with tenant_context(org.id):
        target = Membership.objects.get(user=owner)
        with pytest.raises(Exception, match="proprietário"):
            services.revoke_membership(factories.principal(org, admin), target.id, reason="x")


def test_plan_guards() -> None:
    org, _ = factories.organization(plan_obj=factories.plan("restrito", modules=["approvals"]))
    with tenant_context(org.id):
        plans.ensure_module(Module.APPROVALS)
        with pytest.raises(PlanLimitExceededError, match="Módulo"):
            plans.ensure_module(Module.SIGNATURES)
        plans.ensure_capacity(active_contracts=50, storage_bytes=10 * 1024**2)
        with pytest.raises(PlanLimitExceededError, match="contratos"):
            plans.ensure_capacity(active_contracts=51)
        with pytest.raises(PlanLimitExceededError, match="armazenamento"):
            plans.ensure_capacity(storage_bytes=10 * 1024**2 + 1)
        Subscription.objects.update(status="active", trial_ends_at=None)
        plans.ensure_writable(now=timezone.now() + timedelta(days=365))
        Subscription.objects.update(status="canceled")
        with pytest.raises(PlanLimitExceededError, match="cancelada"):
            plans.ensure_writable()
        Subscription.objects.all().delete()
        with pytest.raises(PlanLimitExceededError, match="sem assinatura"):
            plans.current_subscription()


def test_binding_another_user_in_same_transaction_is_rejected() -> None:
    org, owner = factories.organization()
    with tenant_context(org.id, user_id=owner.id), pytest.raises(TenantContextConflictError):
        bind_user(uuid.uuid4())


def test_idempotency_in_progress_and_missing_record() -> None:
    org, owner = factories.organization()
    with tenant_context(org.id):
        assert (
            idempotency.begin(actor_id=owner.id, scope="s", key="chave-dupla", fingerprint="f")
            is None
        )
        with pytest.raises(IdempotencyConflictError, match="andamento"):
            idempotency.begin(actor_id=owner.id, scope="s", key="chave-dupla", fingerprint="f")
        with pytest.raises(RuntimeError):
            idempotency.complete(
                actor_id=owner.id, scope="s", key="inexistente", status=200, body={}
            )
