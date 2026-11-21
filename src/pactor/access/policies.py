"""Políticas de autorização puras (sem Django, sem banco).

As consultas que filtram listagens, pesquisas, contagens e exportações são
derivadas destas mesmas regras em ``pactor.access.querysets``; testes de
propriedade comparam as duas implementações para garantir equivalência.

Princípios:

* Papel concede capacidades; ele não concede, sozinho, acesso a contratos
  confidenciais. Confidenciais exigem vínculo explícito com o contrato.
* Gerenciar usuários (proprietário/administrador) não implica ler contratos.
* Participantes externos só enxergam o que foi compartilhado explicitamente.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    CONTRACT_MANAGER = "contract_manager"
    REQUESTER = "requester"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    FINANCE = "finance"
    AUDITOR = "auditor"


ROLE_LABELS: Mapping[Role, str] = MappingProxyType(
    {
        Role.OWNER: "Proprietário",
        Role.ADMIN: "Administrador",
        Role.CONTRACT_MANAGER: "Gestor de contratos",
        Role.REQUESTER: "Solicitante",
        Role.REVIEWER: "Revisor",
        Role.APPROVER: "Aprovador",
        Role.FINANCE: "Financeiro",
        Role.AUDITOR: "Auditor",
    }
)


class Permission(StrEnum):
    MEMBERS_MANAGE = "members.manage"
    ORGANIZATION_MANAGE = "organization.manage"
    BILLING_MANAGE = "billing.manage"
    INTEGRATIONS_MANAGE = "integrations.manage"
    WORKFLOWS_MANAGE = "workflows.manage"
    TEMPLATES_MANAGE = "templates.manage"
    COUNTERPARTIES_READ = "counterparties.read"
    COUNTERPARTIES_WRITE = "counterparties.write"
    REQUESTS_CREATE = "requests.create"
    REQUESTS_MANAGE = "requests.manage"
    CONTRACTS_READ = "contracts.read"
    CONTRACTS_CREATE = "contracts.create"
    CONTRACTS_EDIT = "contracts.edit"
    CONTRACTS_COMMENT = "contracts.comment"
    CONTRACTS_SUBMIT = "contracts.submit"
    CONTRACTS_LIFECYCLE = "contracts.lifecycle"
    CONTRACTS_SHARE = "contracts.share"
    APPROVALS_VOTE = "approvals.vote"
    SIGNATURES_SEND = "signatures.send"
    OBLIGATIONS_MANAGE = "obligations.manage"
    OBLIGATIONS_COMPLETE = "obligations.complete"
    FINANCE_READ = "finance.read"
    FINANCE_WRITE = "finance.write"
    AUDIT_READ = "audit.read"
    REPORTS_READ = "reports.read"
    OPERATIONS_REPROCESS = "operations.reprocess"


# Permissões cuja concessão exige MFA concluído na sessão.
PRIVILEGED_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.MEMBERS_MANAGE,
        Permission.ORGANIZATION_MANAGE,
        Permission.BILLING_MANAGE,
        Permission.INTEGRATIONS_MANAGE,
        Permission.WORKFLOWS_MANAGE,
        Permission.OPERATIONS_REPROCESS,
    }
)

_P = Permission
_CONTRACT_WORK = frozenset(
    {
        _P.COUNTERPARTIES_READ,
        _P.COUNTERPARTIES_WRITE,
        _P.REQUESTS_CREATE,
        _P.REQUESTS_MANAGE,
        _P.CONTRACTS_READ,
        _P.CONTRACTS_CREATE,
        _P.CONTRACTS_EDIT,
        _P.CONTRACTS_COMMENT,
        _P.CONTRACTS_SUBMIT,
        _P.CONTRACTS_LIFECYCLE,
        _P.CONTRACTS_SHARE,
        _P.SIGNATURES_SEND,
        _P.OBLIGATIONS_MANAGE,
        _P.OBLIGATIONS_COMPLETE,
        _P.TEMPLATES_MANAGE,
        _P.FINANCE_READ,
        _P.REPORTS_READ,
    }
)

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        # Proprietário e administrador gerenciam a organização. Nenhuma
        # permissão de leitura de contratos é concedida por estes papéis.
        Role.OWNER: frozenset(
            {
                _P.MEMBERS_MANAGE,
                _P.ORGANIZATION_MANAGE,
                _P.BILLING_MANAGE,
                _P.INTEGRATIONS_MANAGE,
                _P.WORKFLOWS_MANAGE,
                _P.OPERATIONS_REPROCESS,
                _P.AUDIT_READ,
            }
        ),
        Role.ADMIN: frozenset(
            {
                _P.MEMBERS_MANAGE,
                _P.INTEGRATIONS_MANAGE,
                _P.WORKFLOWS_MANAGE,
                _P.OPERATIONS_REPROCESS,
                _P.AUDIT_READ,
            }
        ),
        Role.CONTRACT_MANAGER: _CONTRACT_WORK,
        Role.REQUESTER: frozenset(
            {
                _P.COUNTERPARTIES_READ,
                _P.REQUESTS_CREATE,
                _P.CONTRACTS_COMMENT,
                _P.OBLIGATIONS_COMPLETE,
            }
        ),
        Role.REVIEWER: frozenset(
            {
                _P.COUNTERPARTIES_READ,
                _P.CONTRACTS_READ,
                _P.CONTRACTS_EDIT,
                _P.CONTRACTS_COMMENT,
            }
        ),
        Role.APPROVER: frozenset(
            {_P.COUNTERPARTIES_READ, _P.CONTRACTS_READ, _P.CONTRACTS_COMMENT, _P.APPROVALS_VOTE}
        ),
        Role.FINANCE: frozenset(
            {
                _P.COUNTERPARTIES_READ,
                _P.CONTRACTS_READ,
                _P.FINANCE_READ,
                _P.FINANCE_WRITE,
                _P.REPORTS_READ,
                _P.OBLIGATIONS_COMPLETE,
            }
        ),
        Role.AUDITOR: frozenset(
            {_P.COUNTERPARTIES_READ, _P.CONTRACTS_READ, _P.AUDIT_READ, _P.REPORTS_READ}
        ),
    }
)

# Capacidades possíveis para um participante externo, sempre restritas aos
# contratos compartilhados com ele.
EXTERNAL_PERMISSIONS: frozenset[Permission] = frozenset(
    {_P.CONTRACTS_READ, _P.CONTRACTS_COMMENT, _P.OBLIGATIONS_COMPLETE}
)


class Confidentiality(StrEnum):
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    CONFIDENTIAL = "confidential"


class PrincipalKind(StrEnum):
    USER = "user"
    EXTERNAL = "external"
    INTEGRATION = "integration"


@dataclass(frozen=True, slots=True)
class Principal:
    """Quem age, em qual organização, com quais vínculos.

    Construído a cada requisição ou task a partir do banco; nunca reutilizado
    entre organizações.
    """

    organization_id: uuid.UUID
    actor_id: uuid.UUID
    kind: PrincipalKind
    roles: frozenset[Role] = frozenset()
    department_ids: frozenset[uuid.UUID] = frozenset()
    team_ids: frozenset[uuid.UUID] = frozenset()
    mfa_satisfied: bool = False
    # Escopos OAuth de integrações; limitam ainda mais o que os papéis concedem.
    scopes: frozenset[str] | None = None
    permissions: frozenset[Permission] = field(init=False)

    def __post_init__(self) -> None:
        if self.kind is PrincipalKind.EXTERNAL:
            granted = EXTERNAL_PERMISSIONS
        else:
            granted = frozenset().union(*(ROLE_PERMISSIONS[role] for role in self.roles))
        if self.scopes is not None:
            granted = frozenset(p for p in granted if p.value in self.scopes)
        object.__setattr__(self, "permissions", granted)

    @property
    def is_external(self) -> bool:
        return self.kind is PrincipalKind.EXTERNAL

    @property
    def is_privileged(self) -> bool:
        return bool(self.roles & {Role.OWNER, Role.ADMIN})


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    MFA_REQUIRED = "mfa_required"


def decide(principal: Principal, permission: Permission) -> Decision:
    if permission not in principal.permissions:
        return Decision.DENY
    if (
        permission in PRIVILEGED_PERMISSIONS
        and principal.kind is PrincipalKind.USER
        and not principal.mfa_satisfied
    ):
        return Decision.MFA_REQUIRED
    return Decision.ALLOW


def has_permission(principal: Principal, permission: Permission) -> bool:
    return decide(principal, permission) is Decision.ALLOW


# Papéis que enxergam contratos restritos de qualquer departamento.
RESTRICTED_WIDE_ROLES: frozenset[Role] = frozenset({Role.CONTRACT_MANAGER, Role.AUDITOR})


@dataclass(frozen=True, slots=True)
class ContractAccessFacts:
    """Fatos do contrato relevantes para decidir visibilidade."""

    organization_id: uuid.UUID
    confidentiality: Confidentiality
    department_id: uuid.UUID | None
    owner_id: uuid.UUID | None
    requester_id: uuid.UUID | None
    grantee_user_ids: frozenset[uuid.UUID] = frozenset()
    editor_user_ids: frozenset[uuid.UUID] = frozenset()
    grantee_team_ids: frozenset[uuid.UUID] = frozenset()
    active_approver_ids: frozenset[uuid.UUID] = frozenset()


def _is_linked(principal: Principal, facts: ContractAccessFacts) -> bool:
    """Vínculo direto com o contrato: concessão, responsabilidade ou rodada ativa."""
    actor = principal.actor_id
    return (
        actor in facts.grantee_user_ids
        or bool(principal.team_ids & facts.grantee_team_ids)
        or actor in {facts.owner_id, facts.requester_id}
        or actor in facts.active_approver_ids
    )


def _visible_by_classification(principal: Principal, facts: ContractAccessFacts) -> bool:
    match facts.confidentiality:
        case Confidentiality.INTERNAL:
            return True
        case Confidentiality.RESTRICTED:
            return bool(principal.roles & RESTRICTED_WIDE_ROLES) or (
                facts.department_id is not None and facts.department_id in principal.department_ids
            )
        case Confidentiality.CONFIDENTIAL:
            return False


def can_view_contract(principal: Principal, facts: ContractAccessFacts) -> bool:
    if principal.organization_id != facts.organization_id:
        return False
    if principal.is_external:
        return principal.actor_id in facts.grantee_user_ids
    return _is_linked(principal, facts) or (
        Permission.CONTRACTS_READ in principal.permissions
        and _visible_by_classification(principal, facts)
    )


def can_edit_contract(principal: Principal, facts: ContractAccessFacts) -> bool:
    if principal.is_external or Permission.CONTRACTS_EDIT not in principal.permissions:
        return False
    if not can_view_contract(principal, facts):
        return False
    if facts.confidentiality is Confidentiality.CONFIDENTIAL:
        return principal.actor_id in facts.editor_user_ids or principal.actor_id == facts.owner_id
    return True
