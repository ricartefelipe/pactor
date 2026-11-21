"""Construção de cenários de teste por meio dos próprios casos de uso."""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterable
from decimal import Decimal

from allauth.account.models import EmailAddress
from django.test import Client

from pactor.access.policies import Principal, Role
from pactor.access.principal import ACTIVE_ORGANIZATION_SESSION_KEY, principal_for_membership
from pactor.core.tenancy import tenant_context
from pactor.identity import services
from pactor.identity.models import (
    Department,
    DepartmentMembership,
    Membership,
    MembershipKind,
    Organization,
    Team,
    TeamMembership,
    User,
)
from pactor.plans.models import Plan

_sequence = itertools.count(1)
PASSWORD = "senha-de-teste-Forte-123"


def plan(code: str = "teste", *, max_users: int = 10, modules: Iterable[str] | None = None) -> Plan:
    obj, _ = Plan.objects.update_or_create(
        code=code,
        defaults={
            "name": f"Plano {code}",
            "monthly_price": Decimal("100.00"),
            "max_users": max_users,
            "max_active_contracts": 50,
            "max_storage_bytes": 10 * 1024**2,
            "modules": list(
                modules
                or ["approvals", "signatures", "obligations", "finance", "search", "integrations"]
            ),
        },
    )
    return obj


def user(email: str | None = None, *, verified: bool = True, full_name: str = "") -> User:
    address = email or f"pessoa{next(_sequence)}@exemplo.test"
    created = User.objects.create_user(address, PASSWORD, full_name=full_name or address)
    EmailAddress.objects.create(user=created, email=address, verified=verified, primary=True)
    return created


def organization(
    name: str | None = None, *, owner: User | None = None, plan_obj: Plan | None = None
) -> tuple[Organization, User]:
    owner = owner or user()
    org = services.create_organization(
        owner=owner,
        name=name or f"Organização {next(_sequence)}",
        plan=plan_obj or plan(),
        slug=f"org-{uuid.uuid4().hex[:10]}",
    )
    return org, owner


def member(
    org: Organization,
    roles: Iterable[Role] = (Role.CONTRACT_MANAGER,),
    *,
    person: User | None = None,
    kind: MembershipKind = MembershipKind.INTERNAL,
    departments: Iterable[Department] = (),
    teams: Iterable[Team] = (),
) -> User:
    person = person or user()
    with tenant_context(org.id):
        membership = Membership.objects.create(
            organization=org,
            user=person,
            kind=kind,
            roles=[] if kind == MembershipKind.EXTERNAL else sorted(r.value for r in roles),
        )
        for department in departments:
            DepartmentMembership.objects.create(
                organization=org, membership=membership, department=department
            )
        for team in teams:
            TeamMembership.objects.create(organization=org, membership=membership, team=team)
    return person


def department(org: Organization, name: str | None = None) -> Department:
    with tenant_context(org.id):
        return Department.objects.create(organization=org, name=name or f"Depto {next(_sequence)}")


def team(org: Organization, name: str | None = None) -> Team:
    with tenant_context(org.id):
        return Team.objects.create(organization=org, name=name or f"Equipe {next(_sequence)}")


def principal(org: Organization, person: User, *, mfa: bool = True) -> Principal:
    with tenant_context(org.id, user_id=person.id):
        membership = Membership.objects.prefetch_related("department_links", "team_links").get(
            organization=org, user=person
        )
        return principal_for_membership(membership, mfa_satisfied=mfa)


def client_for(person: User, org: Organization | None = None, *, mfa: bool = True) -> Client:
    client = Client(enforce_csrf_checks=False)
    client.force_login(person, backend="allauth.account.auth_backends.AuthenticationBackend")
    session = client.session
    if org is not None:
        session[ACTIVE_ORGANIZATION_SESSION_KEY] = str(org.id)
    if mfa:
        session["account_authentication_methods_user"] = str(person.id)
        session["account_authentication_methods"] = [{"method": "mfa", "at": 0, "type": "totp"}]
    session.save()
    return client
