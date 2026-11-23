"""Consultas de contratos sempre filtradas pela visibilidade do principal."""

from __future__ import annotations

import uuid

from django.db.models import Prefetch, QuerySet

from pactor.access.policies import Principal, can_edit_contract
from pactor.contracts.models import Comment, Contract, ContractGrant, Revision
from pactor.contracts.visibility import facts_for, visibility_q
from pactor.core.errors import AccessDeniedError, NotFoundError

_NOT_FOUND = "Contrato não encontrado."


def visible_contracts(principal: Principal) -> QuerySet[Contract]:
    return (
        Contract.objects.filter(visibility_q(principal))
        .select_related(
            "counterparty", "department", "owner", "current_revision", "approved_revision"
        )
        .prefetch_related(Prefetch("grants", queryset=ContractGrant.objects.order_by("id")))
    )


def get_visible_contract(
    principal: Principal, contract_id: uuid.UUID, *, for_update: bool = False
) -> Contract:
    """Contrato visível ao principal; inexistente e invisível são indistinguíveis.

    Com ``for_update``, a linha é travada sem joins. Ao aguardar o lock, o
    PostgreSQL reavalia apenas a linha travada; relações lidas no mesmo comando
    (revisão vigente, aprovada) poderiam vir desatualizadas. Por isso elas são
    carregadas depois, em consultas novas que já enxergam o commit concorrente.
    """
    if for_update:
        locked = (
            Contract.objects.select_for_update()
            .filter(visibility_q(principal), id=contract_id)
            .first()
        )
        if locked is None:
            raise NotFoundError(_NOT_FOUND)
        return locked
    contract = visible_contracts(principal).filter(id=contract_id).first()
    if contract is None:
        raise NotFoundError(_NOT_FOUND)
    return contract


def require_editable(principal: Principal, contract: Contract) -> None:
    if not can_edit_contract(principal, facts_for(contract)):
        raise AccessDeniedError("Você não pode editar este contrato.")


def revisions_of(contract: Contract) -> QuerySet[Revision]:
    return Revision.objects.filter(
        organization_id=contract.organization_id, contract=contract
    ).select_related("author")


def get_revision(contract: Contract, revision_id: uuid.UUID) -> Revision:
    revision = revisions_of(contract).filter(id=revision_id).first()
    if revision is None:
        raise NotFoundError("Revisão não encontrada.")
    return revision


def comments_of(revision: Revision) -> QuerySet[Comment]:
    return (
        Comment.objects.filter(organization_id=revision.organization_id, revision=revision)
        .select_related("author")
        .order_by("created_at", "id")
    )
