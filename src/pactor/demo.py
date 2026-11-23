"""Dados de demonstração: duas organizações fictícias, idempotente.

Todos os nomes, documentos e valores são fictícios e identificados como tal.
O seed usa os próprios casos de uso (convite e aceite, regras de plano,
auditoria e outbox valem) e é recusado em produção. Executar de novo não
duplica nada: cada etapa procura o que já existe pela chave natural antes de
criar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from pactor.access.policies import Confidentiality, Principal, PrincipalKind, Role
from pactor.approvals import services as approvals
from pactor.approvals.models import WorkflowDefinition
from pactor.contracts import services as contracts
from pactor.contracts.domain import ContractType, PartyRole
from pactor.contracts.models import Contract
from pactor.core.tenancy import tenant_context
from pactor.counterparties import services as counterparties
from pactor.counterparties.domain import PartyKind
from pactor.counterparties.models import Counterparty
from pactor.deadlines import services as deadlines
from pactor.deadlines.models import HolidayCalendar
from pactor.drafting import services as drafting
from pactor.drafting.models import ContractTemplate
from pactor.identity import invitations
from pactor.identity import services as identity
from pactor.identity.models import Department, Membership, MembershipKind, Organization, User
from pactor.plans.models import Plan

# Credenciais públicas e fictícias, exclusivas da demonstração (docs/DEMONSTRACAO.md).
# São seguras de publicar porque o seed é recusado em produção.
DEMO_PASSWORD = "Pactor-Demo-2026!"  # noqa: S105  # nosec B105 - senha pública de demonstração
DEMO_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"  # noqa: S105  # nosec B105 - TOTP público
DEMO_DOMAIN = "exemplo.test"
LEGAL_DEPARTMENT = "Jurídico (fictício)"
LEGAL_MEMBERS = frozenset({"aprovador", "revisora"})


class DemoNotAllowedError(RuntimeError):
    """Seed de demonstração solicitado em produção."""


@dataclass(frozen=True, slots=True)
class DemoPerson:
    key: str
    name: str
    roles: tuple[Role, ...]
    mfa: bool = False


PEOPLE = (
    DemoPerson("proprietaria", "Proprietária da organização (fictícia)", (Role.OWNER,), mfa=True),
    DemoPerson("admin", "Administração de acessos (fictícia)", (Role.ADMIN,), mfa=True),
    DemoPerson("gestora", "Gestora de contratos (fictícia)", (Role.CONTRACT_MANAGER,)),
    DemoPerson("aprovador", "Aprovador jurídico (fictício)", (Role.APPROVER,)),
    DemoPerson("revisora", "Revisora (fictícia)", (Role.REVIEWER,)),
    DemoPerson("financeiro", "Analista financeiro (fictício)", (Role.FINANCE,)),
    DemoPerson("solicitante", "Solicitante (fictício)", (Role.REQUESTER,)),
)

ORGANIZATIONS = (
    ("demo-a", "Organização A (fictícia)", "a"),
    ("demo-b", "Organização B (fictícia)", "b"),
)

TEMPLATE_DEFINITION: dict[str, list[dict[str, object]]] = {
    "fields": [
        {"key": "contratante", "label": "Contratante", "type": "text", "required": True},
        {"key": "contratada", "label": "Contratada", "type": "text", "required": True},
        {"key": "valor_mensal", "label": "Valor mensal (R$)", "type": "decimal", "required": True},
        {"key": "inicio", "label": "Início", "type": "date", "required": True},
        {"key": "sigilo", "label": "Incluir confidencialidade", "type": "boolean"},
    ],
    "sections": [
        {
            "key": "aviso",
            "title": "Aviso",
            "body": "Modelo demonstrativo e fictício: não representa texto jurídico recomendado.",
        },
        {
            "key": "objeto",
            "title": "Objeto",
            "body": "{{contratante}} contrata {{contratada}} para serviços de suporte.",
        },
        {
            "key": "valor",
            "title": "Valor",
            "body": "Valor mensal de R$ {{valor_mensal}}, a partir de {{inicio}}.",
        },
        {
            "key": "sigilo",
            "title": "Confidencialidade",
            "body": "As partes manterão sigilo.",
            "optional_when": "sigilo",
        },
    ],
}


def _user(email: str, name: str, *, mfa: bool) -> User:
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User.objects.create_user(email, DEMO_PASSWORD, full_name=name)
    EmailAddress.objects.get_or_create(
        user=user, email=email, defaults={"verified": True, "primary": True}
    )
    if mfa and not Authenticator.objects.filter(user=user, type=Authenticator.Type.TOTP).exists():
        TOTP.activate(user, DEMO_TOTP_SECRET)
    return user


def _principal(organization: Organization, user: User, roles: tuple[Role, ...]) -> Principal:
    return Principal(
        organization_id=organization.id,
        actor_id=user.id,
        kind=PrincipalKind.USER,
        roles=frozenset(roles),
        mfa_satisfied=True,
    )


def _organization(
    slug: str, name: str, suffix: str, plan: Plan
) -> tuple[Organization, dict[str, User]]:
    people = {
        p.key: _user(f"{p.key}.{suffix}@{DEMO_DOMAIN}", f"{p.name} — {name}", mfa=p.mfa)
        for p in PEOPLE
    }
    owner = people["proprietaria"]
    organization = Organization.objects.filter(slug=slug).first()
    if organization is None:
        organization = identity.create_organization(owner=owner, name=name, plan=plan, slug=slug)
    principal = _principal(organization, owner, (Role.OWNER,))
    with tenant_context(organization.id):
        legal = Department.objects.filter(organization=organization, name=LEGAL_DEPARTMENT).first()
        if legal is None:
            legal = identity.create_department(principal, name=LEGAL_DEPARTMENT)
        for person in PEOPLE[1:]:
            user = people[person.key]
            if Membership.objects.filter(organization=organization, user=user).exists():
                continue
            invitation = identity.invite(
                principal,
                email=user.email,
                kind=MembershipKind.INTERNAL,
                roles=[r.value for r in person.roles],
                department_ids=[legal.id] if person.key in LEGAL_MEMBERS else [],
            )
            identity.accept_invitation(user, invitations.make_token(organization.id, invitation.id))
    return organization, people


def _seed_contract_data(organization: Organization, people: dict[str, User], suffix: str) -> None:
    manager = _principal(organization, people["gestora"], (Role.CONTRACT_MANAGER,))
    owner = _principal(organization, people["proprietaria"], (Role.OWNER,))
    with tenant_context(organization.id):
        if not HolidayCalendar.objects.filter(organization=organization).exists():
            deadlines.create_holiday_calendar(
                manager,
                name="Feriados demonstrativos (revisar)",
                is_default=True,
                holidays=[
                    (date(2027, 1, 1), "Feriado demonstrativo"),
                    (date(2027, 12, 25), "Feriado demonstrativo"),
                ],
            )
        if not WorkflowDefinition.objects.filter(organization=organization).exists():
            approvals.publish_workflow(
                owner,
                name="Aprovação padrão (demonstração)",
                rules=[
                    {
                        "name": "Padrão",
                        "conditions": [],
                        "steps": [
                            {
                                "key": "juridico",
                                "name": "Jurídico",
                                "order": 1,
                                "quorum": 1,
                                "approver_ids": [str(people["aprovador"].id)],
                            },
                        ],
                    }
                ],
            )
        if not ContractTemplate.objects.filter(organization=organization).exists():
            template = drafting.create_template(
                manager,
                name="Prestação de serviços (modelo demonstrativo)",
                contract_type=ContractType.SERVICES,
                description="Exemplo fictício para demonstração.",
                definition=TEMPLATE_DEFINITION,
            )
            drafting.publish(
                manager, template.id, expected_version=1, change_note="Versão inicial demonstrativa"
            )
        document = "11222333000181" if suffix == "a" else "11444777000161"
        counterparty = Counterparty.objects.filter(
            organization=organization, document=document
        ).first()
        if counterparty is None:
            counterparty = counterparties.create_counterparty(
                manager,
                counterparties.CounterpartyData(
                    kind=PartyKind.COMPANY,
                    name=f"Fornecedora Exemplo {suffix.upper()} Ltda (fictícia)",
                    document=document,
                    email=f"contato.{suffix}@{DEMO_DOMAIN}",
                ),
            )
        if not Contract.objects.filter(organization=organization).exists():
            today = timezone.localdate()
            contracts.create_contract(
                manager,
                contracts.ContractInput(
                    title=f"Suporte técnico mensal — exemplo fictício {suffix.upper()}",
                    contract_type=ContractType.SERVICES,
                    counterparty_id=counterparty.id,
                    organization_role=PartyRole.CONTRACTOR,
                    counterparty_role=PartyRole.CONTRACTED,
                    owner_id=people["gestora"].id,
                    confidentiality=Confidentiality.INTERNAL,
                    total_value=Decimal("36000.00"),
                    planned_start=today + timedelta(days=15),
                    planned_end=today + timedelta(days=380),
                    tags=("demonstração",),
                    initial_content="Contrato fictício de demonstração.\nCláusula 1. Objeto.",
                ),
            )


def seed() -> list[Organization]:
    if settings.PACTOR_ENVIRONMENT == "production":
        raise DemoNotAllowedError("O seed de demonstração não pode ser executado em produção.")
    plan = Plan.objects.get(code=settings.PACTOR_TRIAL_PLAN)
    created: list[Organization] = []
    for slug, name, suffix in ORGANIZATIONS:
        with transaction.atomic():
            organization, people = _organization(slug, name, suffix, plan)
            _seed_contract_data(organization, people, suffix)
        created.append(organization)
    return created
