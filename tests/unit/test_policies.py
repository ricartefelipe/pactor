"""Políticas de autorização: matriz de papéis e visibilidade de contratos."""

from __future__ import annotations

import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pactor.access.policies import (
    EXTERNAL_PERMISSIONS,
    PRIVILEGED_PERMISSIONS,
    ROLE_PERMISSIONS,
    Confidentiality,
    ContractAccessFacts,
    Decision,
    Permission,
    Principal,
    PrincipalKind,
    Role,
    can_edit_contract,
    can_view_contract,
    decide,
    has_permission,
)

ORG = uuid.UUID(int=1)
OTHER_ORG = uuid.UUID(int=2)
ACTOR = uuid.UUID(int=10)
DEPT = uuid.UUID(int=20)
TEAM = uuid.UUID(int=30)


def _principal(
    *roles: Role,
    kind: PrincipalKind = PrincipalKind.USER,
    department_ids: frozenset[uuid.UUID] = frozenset(),
    team_ids: frozenset[uuid.UUID] = frozenset(),
    mfa_satisfied: bool = False,
    scopes: frozenset[str] | None = None,
) -> Principal:
    return Principal(
        organization_id=ORG,
        actor_id=ACTOR,
        kind=kind,
        roles=frozenset(roles),
        department_ids=department_ids,
        team_ids=team_ids,
        mfa_satisfied=mfa_satisfied,
        scopes=scopes,
    )


def _facts(
    confidentiality: Confidentiality,
    *,
    department_id: uuid.UUID | None = DEPT,
    owner_id: uuid.UUID | None = None,
    requester_id: uuid.UUID | None = None,
    grantee_user_ids: frozenset[uuid.UUID] = frozenset(),
    editor_user_ids: frozenset[uuid.UUID] = frozenset(),
    grantee_team_ids: frozenset[uuid.UUID] = frozenset(),
    active_approver_ids: frozenset[uuid.UUID] = frozenset(),
) -> ContractAccessFacts:
    return ContractAccessFacts(
        organization_id=ORG,
        confidentiality=confidentiality,
        department_id=department_id,
        owner_id=owner_id,
        requester_id=requester_id,
        grantee_user_ids=grantee_user_ids,
        editor_user_ids=editor_user_ids,
        grantee_team_ids=grantee_team_ids,
        active_approver_ids=active_approver_ids,
    )


def test_owner_and_admin_manage_people_but_do_not_read_contracts() -> None:
    for role in (Role.OWNER, Role.ADMIN):
        assert Permission.MEMBERS_MANAGE in ROLE_PERMISSIONS[role]
        assert Permission.CONTRACTS_READ not in ROLE_PERMISSIONS[role]


def test_admin_without_grant_cannot_view_any_contract() -> None:
    admin = _principal(Role.ADMIN, Role.OWNER, mfa_satisfied=True)
    for level in Confidentiality:
        assert not can_view_contract(admin, _facts(level))


def test_privileged_permission_requires_mfa() -> None:
    assert decide(_principal(Role.ADMIN), Permission.MEMBERS_MANAGE) is Decision.MFA_REQUIRED
    assert decide(_principal(Role.ADMIN, mfa_satisfied=True), Permission.MEMBERS_MANAGE) is (
        Decision.ALLOW
    )
    assert decide(_principal(Role.REQUESTER), Permission.MEMBERS_MANAGE) is Decision.DENY


def test_non_privileged_permission_does_not_require_mfa() -> None:
    assert has_permission(_principal(Role.CONTRACT_MANAGER), Permission.CONTRACTS_EDIT)
    assert Permission.CONTRACTS_EDIT not in PRIVILEGED_PERMISSIONS


def test_integration_scopes_restrict_role_permissions() -> None:
    client = _principal(
        Role.CONTRACT_MANAGER, kind=PrincipalKind.INTEGRATION, scopes=frozenset({"contracts.read"})
    )
    assert client.permissions == frozenset({Permission.CONTRACTS_READ})


def test_integration_does_not_need_session_mfa_for_privileged_scope() -> None:
    client = _principal(
        Role.ADMIN, kind=PrincipalKind.INTEGRATION, scopes=frozenset({"members.manage"})
    )
    assert decide(client, Permission.MEMBERS_MANAGE) is Decision.ALLOW


def test_external_permissions_ignore_roles() -> None:
    external = _principal(kind=PrincipalKind.EXTERNAL)
    assert external.permissions == EXTERNAL_PERMISSIONS
    assert external.is_external and not external.is_privileged


@pytest.mark.parametrize(
    ("roles", "level", "departments", "expected"),
    [
        ((Role.REVIEWER,), Confidentiality.INTERNAL, frozenset(), True),
        ((Role.REVIEWER,), Confidentiality.RESTRICTED, frozenset(), False),
        ((Role.REVIEWER,), Confidentiality.RESTRICTED, frozenset({DEPT}), True),
        ((Role.CONTRACT_MANAGER,), Confidentiality.RESTRICTED, frozenset(), True),
        ((Role.AUDITOR,), Confidentiality.RESTRICTED, frozenset(), True),
        ((Role.CONTRACT_MANAGER,), Confidentiality.CONFIDENTIAL, frozenset({DEPT}), False),
        ((Role.REQUESTER,), Confidentiality.INTERNAL, frozenset(), False),
    ],
)
def test_visibility_by_role_and_department(
    roles: tuple[Role, ...],
    level: Confidentiality,
    departments: frozenset[uuid.UUID],
    *,
    expected: bool,
) -> None:
    principal = _principal(*roles, department_ids=departments)
    assert can_view_contract(principal, _facts(level)) is expected


def test_restricted_contract_without_department_is_not_visible_by_department() -> None:
    principal = _principal(Role.REVIEWER, department_ids=frozenset({DEPT}))
    assert not can_view_contract(principal, _facts(Confidentiality.RESTRICTED, department_id=None))


_C = Confidentiality.CONFIDENTIAL


@pytest.mark.parametrize(
    "linked",
    [
        _facts(_C, grantee_user_ids=frozenset({ACTOR})),
        _facts(_C, grantee_team_ids=frozenset({TEAM})),
        _facts(_C, owner_id=ACTOR),
        _facts(_C, requester_id=ACTOR),
        _facts(_C, active_approver_ids=frozenset({ACTOR})),
    ],
)
def test_confidential_requires_direct_link(linked: ContractAccessFacts) -> None:
    principal = _principal(Role.APPROVER, team_ids=frozenset({TEAM}))
    assert not can_view_contract(principal, _facts(_C))
    assert can_view_contract(principal, linked)


def test_external_sees_only_explicit_user_grants() -> None:
    external = _principal(kind=PrincipalKind.EXTERNAL, team_ids=frozenset({TEAM}))
    assert not can_view_contract(external, _facts(Confidentiality.INTERNAL, owner_id=ACTOR))
    assert not can_view_contract(
        external, _facts(Confidentiality.INTERNAL, grantee_team_ids=frozenset({TEAM}))
    )
    assert can_view_contract(
        external, _facts(Confidentiality.CONFIDENTIAL, grantee_user_ids=frozenset({ACTOR}))
    )
    assert not can_edit_contract(
        external, _facts(Confidentiality.INTERNAL, grantee_user_ids=frozenset({ACTOR}))
    )


def test_edit_rules() -> None:
    manager = _principal(Role.CONTRACT_MANAGER)
    approver = _principal(Role.APPROVER)
    assert can_edit_contract(manager, _facts(Confidentiality.INTERNAL))
    assert not can_edit_contract(approver, _facts(Confidentiality.INTERNAL))
    assert not can_edit_contract(manager, _facts(Confidentiality.CONFIDENTIAL))
    viewer_only = _facts(Confidentiality.CONFIDENTIAL, grantee_user_ids=frozenset({ACTOR}))
    assert not can_edit_contract(manager, viewer_only)
    editor = _facts(
        Confidentiality.CONFIDENTIAL,
        grantee_user_ids=frozenset({ACTOR}),
        editor_user_ids=frozenset({ACTOR}),
    )
    assert can_edit_contract(manager, editor)
    assert can_edit_contract(manager, _facts(Confidentiality.CONFIDENTIAL, owner_id=ACTOR))
    assert not can_edit_contract(_principal(Role.REVIEWER), _facts(Confidentiality.RESTRICTED))


uuids = st.sampled_from([uuid.UUID(int=i) for i in range(1, 6)])
uuid_sets = st.frozensets(uuids, max_size=3)
principals = st.builds(
    Principal,
    organization_id=st.sampled_from([ORG, OTHER_ORG]),
    actor_id=uuids,
    kind=st.sampled_from(list(PrincipalKind)),
    roles=st.frozensets(st.sampled_from(list(Role))),
    department_ids=uuid_sets,
    team_ids=uuid_sets,
    mfa_satisfied=st.booleans(),
)
facts = st.builds(
    ContractAccessFacts,
    organization_id=st.just(ORG),
    confidentiality=st.sampled_from(list(Confidentiality)),
    department_id=st.none() | uuids,
    owner_id=st.none() | uuids,
    requester_id=st.none() | uuids,
    grantee_user_ids=uuid_sets,
    editor_user_ids=uuid_sets,
    grantee_team_ids=uuid_sets,
    active_approver_ids=uuid_sets,
)


@given(principals, facts)
def test_property_other_organization_never_sees(
    principal: Principal, contract: ContractAccessFacts
) -> None:
    if principal.organization_id != contract.organization_id:
        assert not can_view_contract(principal, contract)
        assert not can_edit_contract(principal, contract)


@given(principals, facts)
def test_property_edit_implies_view(principal: Principal, contract: ContractAccessFacts) -> None:
    if can_edit_contract(principal, contract):
        assert can_view_contract(principal, contract)


@given(principals, facts)
def test_property_confidential_requires_link(
    principal: Principal, contract: ContractAccessFacts
) -> None:
    if contract.confidentiality is Confidentiality.CONFIDENTIAL and can_view_contract(
        principal, contract
    ):
        actor = principal.actor_id
        assert (
            actor in contract.grantee_user_ids
            or (not principal.is_external and bool(principal.team_ids & contract.grantee_team_ids))
            or (not principal.is_external and actor in {contract.owner_id, contract.requester_id})
            or (not principal.is_external and actor in contract.active_approver_ids)
        )


@given(principals, facts)
def test_property_management_roles_add_no_visibility(
    principal: Principal, contract: ContractAccessFacts
) -> None:
    """Acrescentar proprietário/administrador nunca amplia o que se enxerga."""
    if principal.kind is PrincipalKind.INTEGRATION:
        return
    elevated = Principal(
        organization_id=principal.organization_id,
        actor_id=principal.actor_id,
        kind=principal.kind,
        roles=principal.roles | {Role.OWNER, Role.ADMIN},
        department_ids=principal.department_ids,
        team_ids=principal.team_ids,
        mfa_satisfied=True,
    )
    assert can_view_contract(elevated, contract) == can_view_contract(principal, contract)
