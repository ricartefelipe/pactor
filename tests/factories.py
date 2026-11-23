"""Construção de cenários de teste por meio dos próprios casos de uso."""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterable
from decimal import Decimal

from allauth.account.models import EmailAddress
from django.test import Client

from pactor.access.policies import Confidentiality, Principal, PrincipalKind, Role
from pactor.access.principal import ACTIVE_ORGANIZATION_SESSION_KEY, principal_for_membership
from pactor.contracts import services as contract_services
from pactor.contracts.domain import ContractType, PartyRole
from pactor.contracts.models import Contract
from pactor.core.tenancy import tenant_context
from pactor.counterparties import services as cp_services
from pactor.counterparties.domain import PartyKind, cnpj_check_digits
from pactor.counterparties.models import Counterparty
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


def counterparty(
    org: Organization,
    *,
    document: str | None = None,
    name: str = "Fornecedora Exemplo Ltda (fictícia)",
) -> Counterparty:
    base = f"{next(_sequence):08d}0001"
    doc = document or base + cnpj_check_digits(base)
    with tenant_context(org.id):
        manager = _system_manager(org)
        return cp_services.create_counterparty(
            manager, cp_services.CounterpartyData(kind=PartyKind.COMPANY, name=name, document=doc)
        )


def _system_manager(org: Organization) -> Principal:
    """Principal de gestor usado apenas para montar cenários de teste."""
    return Principal(
        organization_id=org.id,
        actor_id=uuid.uuid4(),
        kind=PrincipalKind.USER,
        roles=frozenset({Role.CONTRACT_MANAGER}),
        mfa_satisfied=True,
    )


def contract(
    org: Organization,
    owner: User,
    *,
    confidentiality: str = "internal",
    department: Department | None = None,
    content: str = "Cláusula 1. Objeto.\nCláusula 2. Prazo.",
    total_value: Decimal | None = Decimal("1000.00"),
    cp: Counterparty | None = None,
) -> Contract:
    target = cp or counterparty(org)
    with tenant_context(org.id):
        manager = principal_for_owner(org, owner)
        return contract_services.create_contract(
            manager,
            contract_services.ContractInput(
                title="Contrato de serviços (exemplo fictício)",
                contract_type=ContractType.SERVICES,
                counterparty_id=target.id,
                organization_role=PartyRole.CONTRACTOR,
                counterparty_role=PartyRole.CONTRACTED,
                owner_id=owner.id,
                department_id=department.id if department else None,
                confidentiality=Confidentiality(confidentiality),
                total_value=total_value,
                initial_content=content,
            ),
        )


def principal_for_owner(org: Organization, owner: User) -> Principal:
    """Principal do responsável, já dentro do contexto da organização."""
    membership = Membership.objects.prefetch_related("department_links", "team_links").get(
        organization=org, user=owner
    )
    return principal_for_membership(membership, mfa_satisfied=True)
