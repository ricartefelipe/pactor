"""Resolução do principal autenticado para a organização ativa.

A organização ativa do navegador fica na sessão do servidor. A cada
requisição a associação é relida do banco: revogar uma associação encerra o
acesso imediatamente, mesmo com a sessão ainda válida.
"""

from __future__ import annotations

import uuid

from django.http import HttpRequest
from rest_framework.request import Request

from pactor.access.policies import Principal, PrincipalKind, Role
from pactor.core.errors import AccessDeniedError
from pactor.core.tenancy import bind_tenant, bind_user
from pactor.identity.mfa import session_used_mfa
from pactor.identity.models import (
    Membership,
    MembershipKind,
    MembershipStatus,
    OrganizationStatus,
    User,
)

ACTIVE_ORGANIZATION_SESSION_KEY = "pactor_active_organization"


class NoActiveOrganizationError(AccessDeniedError):
    code = "organization_not_selected"


def principal_for_membership(membership: Membership, *, mfa_satisfied: bool) -> Principal:
    return Principal(
        organization_id=membership.organization_id,
        actor_id=membership.user_id,
        kind=PrincipalKind.EXTERNAL
        if membership.kind == MembershipKind.EXTERNAL
        else PrincipalKind.USER,
        roles=frozenset(Role(r) for r in membership.roles),
        department_ids=frozenset(link.department_id for link in membership.department_links.all()),
        team_ids=frozenset(link.team_id for link in membership.team_links.all()),
        mfa_satisfied=mfa_satisfied,
    )


def load_active_membership(user: User, organization_id: uuid.UUID) -> Membership | None:
    """Busca a associação ativa. Exige transação aberta (vincula o usuário).

    Os vínculos com departamentos e equipes não são pré-carregados aqui: são
    tabelas de tenant e só ficam visíveis depois de ``bind_tenant``.
    """
    bind_user(user.id)
    return (
        Membership.objects.select_related("organization")
        .filter(
            user=user,
            organization_id=organization_id,
            status=MembershipStatus.ACTIVE,
            organization__status=OrganizationStatus.ACTIVE,
        )
        .first()
    )


def activate_request_principal(request: HttpRequest | Request, user: User) -> Principal:
    """Resolve o principal e vincula a transação da requisição à organização."""
    raw = request.session.get(ACTIVE_ORGANIZATION_SESSION_KEY)
    if not raw:
        raise NoActiveOrganizationError("Selecione uma organização.")
    organization_id = uuid.UUID(str(raw))
    membership = load_active_membership(user, organization_id)
    if membership is None:
        request.session.pop(ACTIVE_ORGANIZATION_SESSION_KEY, None)
        raise NoActiveOrganizationError("Acesso à organização não está mais disponível.")
    bind_tenant(organization_id)
    # Após o vínculo do tenant, os departamentos e equipes da associação são lidos.
    return principal_for_membership(membership, mfa_satisfied=session_used_mfa(request, user))
