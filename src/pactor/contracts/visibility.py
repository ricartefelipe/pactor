"""Tradução das políticas de visibilidade para consultas ao banco.

``visibility_q`` deve aceitar exatamente os contratos para os quais
``policies.can_view_contract`` devolve verdadeiro; um teste de propriedade
compara as duas implementações sobre dados gerados. Listagens, contagens,
pesquisas, facetas e exportações usam sempre esta condição, aplicada antes de
qualquer agregação.
"""

from __future__ import annotations

from django.db.models import Exists, OuterRef, Q

from pactor.access.policies import (
    RESTRICTED_WIDE_ROLES,
    Confidentiality,
    ContractAccessFacts,
    Permission,
    Principal,
)
from pactor.contracts.models import Contract, ContractGrant, GrantSource


def _grant_exists(**filters: object) -> Exists:
    return Exists(ContractGrant.objects.filter(contract=OuterRef("pk"), **filters))


def visibility_q(principal: Principal) -> Q:
    org = Q(organization_id=principal.organization_id)
    actor = principal.actor_id
    if principal.is_external:
        return org & Q(_grant_exists(user_id=actor, source=GrantSource.MANUAL))
    linked = Q(_grant_exists(user_id=actor)) | Q(owner_id=actor) | Q(requester_id=actor)
    if principal.team_ids:
        linked |= Q(_grant_exists(team_id__in=list(principal.team_ids)))
    if Permission.CONTRACTS_READ not in principal.permissions:
        return org & linked
    restricted = Q(confidentiality=Confidentiality.RESTRICTED.value)
    if not principal.roles & RESTRICTED_WIDE_ROLES:
        restricted &= Q(department_id__in=list(principal.department_ids))
    by_classification = Q(confidentiality=Confidentiality.INTERNAL.value) | restricted
    return org & (linked | by_classification)


def facts_for(contract: Contract) -> ContractAccessFacts:
    """Fatos de acesso a partir do contrato com ``grants`` pré-carregados."""
    grants = list(contract.grants.all())
    return ContractAccessFacts(
        organization_id=contract.organization_id,
        confidentiality=Confidentiality(contract.confidentiality),
        department_id=contract.department_id,
        owner_id=contract.owner_id,
        requester_id=contract.requester_id,
        grantee_user_ids=frozenset(
            g.user_id for g in grants if g.user_id and g.source == GrantSource.MANUAL
        ),
        editor_user_ids=frozenset(
            g.user_id for g in grants if g.user_id and g.can_edit and g.source == GrantSource.MANUAL
        ),
        grantee_team_ids=frozenset(g.team_id for g in grants if g.team_id),
        active_approver_ids=frozenset(
            g.user_id for g in grants if g.user_id and g.source == GrantSource.APPROVAL
        ),
    )
