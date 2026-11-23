"""Organizações, associações, convites, revogação e MFA pela API e serviços."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from pactor.access.policies import Role
from pactor.audit.models import AuditEvent
from pactor.core.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    PlanLimitExceededError,
)
from pactor.core.models import OutboxEvent
from pactor.core.tenancy import tenant_context
from pactor.identity import services
from pactor.identity.api import AcceptedInvitationSerializer
from pactor.identity.invitations import make_token
from pactor.identity.models import Invitation, Membership, MembershipKind, MembershipStatus
from pactor.plans.models import Subscription
from tests import factories
from tests.schema_match import assert_matches

pytestmark = pytest.mark.django_db(transaction=True)


def test_create_organization_sets_owner_trial_and_audit() -> None:
    org, owner = factories.organization("Acme")
    with tenant_context(org.id):
        membership = Membership.objects.get()
        assert membership.user == owner and membership.roles == ["owner"]
        subscription = Subscription.objects.get()
        assert subscription.status == "trialing" and subscription.trial_ends_at is not None
        assert AuditEvent.objects.filter(action="organization.created").count() == 1


def test_me_lists_only_own_organizations_and_active_principal() -> None:
    org_a, owner = factories.organization("A")
    factories.organization("B")
    response = factories.client_for(owner, org_a).get("/api/v1/me")
    body = response.json()
    assert response.status_code == 200
    assert [o["name"] for o in body["organizations"]] == ["A"]
    assert body["active_organization"]["roles"] == ["owner"]
    assert "members.manage" in body["active_organization"]["permissions"]
    assert "contracts.read" not in body["active_organization"]["permissions"]


def test_switch_to_foreign_organization_is_indistinguishable_from_missing() -> None:
    org_a, owner = factories.organization("A")
    org_b, _ = factories.organization("B")
    client = factories.client_for(owner)
    foreign = client.put(
        "/api/v1/session/organization",
        {"organization_id": str(org_b.id)},
        content_type="application/json",
    )
    missing = client.put(
        "/api/v1/session/organization",
        {"organization_id": "00000000-0000-0000-0000-000000000000"},
        content_type="application/json",
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "not_found"
    ok = client.put(
        "/api/v1/session/organization",
        {"organization_id": str(org_a.id)},
        content_type="application/json",
    )
    assert ok.status_code == 200 and ok.json()["active_organization"]["name"] == "A"


def test_switching_organization_rotates_session_key() -> None:
    org, owner = factories.organization()
    client = factories.client_for(owner)
    before = client.session.session_key
    client.put(
        "/api/v1/session/organization",
        {"organization_id": str(org.id)},
        content_type="application/json",
    )
    assert client.session.session_key != before


def test_tenant_endpoint_without_active_organization_is_denied() -> None:
    _, owner = factories.organization()
    response = factories.client_for(owner).get("/api/v1/departments")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "organization_not_selected"


def test_anonymous_requests_are_rejected() -> None:
    assert Client().get("/api/v1/me").status_code in {401, 403}


def test_privileged_action_without_mfa_is_blocked() -> None:
    org, owner = factories.organization()
    response = factories.client_for(owner, org, mfa=False).get("/api/v1/members")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "mfa_required"
    assert factories.client_for(owner, org, mfa=True).get("/api/v1/members").status_code == 200


def test_members_listing_requires_management_permission() -> None:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    client = factories.client_for(manager, org)
    assert client.get("/api/v1/members").status_code == 403
    directory = client.get("/api/v1/members/directory")
    assert directory.status_code == 200
    assert {"user_id", "full_name", "email"} == set(directory.json()[0])


def test_external_participant_cannot_use_directory_or_departments() -> None:
    org, _ = factories.organization()
    factories.department(org)
    external = factories.member(org, kind=MembershipKind.EXTERNAL)
    client = factories.client_for(external, org)
    assert client.get("/api/v1/members/directory").status_code == 403
    assert client.get("/api/v1/departments").json() == []


def test_invitation_flow_sends_email_event_and_accepts() -> None:
    org, owner = factories.organization()
    dept = factories.department(org)
    client = factories.client_for(owner, org)
    created = client.post(
        "/api/v1/invitations",
        {"email": "Nova@Exemplo.test", "roles": ["reviewer"], "department_ids": [str(dept.id)]},
        content_type="application/json",
    )
    assert created.status_code == 201, created.json()
    assert "token" not in created.json()
    event = OutboxEvent.objects.get(topic="identity.invitation_created")
    assert set(event.payload) == {"invitation_id"}

    invitee = factories.user("nova@exemplo.test")
    token = make_token(org.id, created.json()["id"])
    accepted = factories.client_for(invitee).post(
        "/api/v1/invitations/accept", {"token": token}, content_type="application/json"
    )
    assert accepted.status_code == 200
    assert_matches(AcceptedInvitationSerializer(), accepted.json())
    principal = factories.principal(org, invitee)
    assert principal.roles == {Role.REVIEWER} and principal.department_ids == {dept.id}


def test_invitation_cannot_be_accepted_by_other_email_or_twice() -> None:
    org, owner = factories.organization()
    principal = factories.principal(org, owner)
    with tenant_context(org.id):
        invitation = services.invite(
            principal, email="alvo@exemplo.test", kind=MembershipKind.INTERNAL, roles=["approver"]
        )
    token = make_token(org.id, invitation.id)
    with pytest.raises(DomainValidationError):
        services.accept_invitation(factories.user("outro@exemplo.test"), token)
    target = factories.user("alvo@exemplo.test")
    services.accept_invitation(target, token)
    with pytest.raises(DomainValidationError):
        services.accept_invitation(target, token)


def test_unverified_email_cannot_accept() -> None:
    org, owner = factories.organization()
    with tenant_context(org.id):
        invitation = services.invite(
            factories.principal(org, owner),
            email="x@exemplo.test",
            kind=MembershipKind.INTERNAL,
            roles=["approver"],
        )
    with pytest.raises(DomainValidationError, match="Confirme"):
        services.accept_invitation(
            factories.user("x@exemplo.test", verified=False), make_token(org.id, invitation.id)
        )


def test_expired_invitation_is_rejected() -> None:
    org, owner = factories.organization()
    with tenant_context(org.id):
        invitation = services.invite(
            factories.principal(org, owner),
            email="y@exemplo.test",
            kind=MembershipKind.INTERNAL,
            roles=["approver"],
        )
    later = timezone.now() + timedelta(days=8)
    with pytest.raises(DomainValidationError):
        services.accept_invitation(
            factories.user("y@exemplo.test"), make_token(org.id, invitation.id), now=later
        )


def test_seat_limit_counts_members_and_pending_invitations() -> None:
    org, owner = factories.organization(plan_obj=factories.plan("pequeno", max_users=2))
    principal = factories.principal(org, owner)
    with tenant_context(org.id):
        services.invite(
            principal, email="a@exemplo.test", kind=MembershipKind.INTERNAL, roles=["approver"]
        )
        with pytest.raises(PlanLimitExceededError):
            services.invite(
                principal, email="b@exemplo.test", kind=MembershipKind.INTERNAL, roles=["approver"]
            )
        # Externos não consomem assento.
        services.invite(principal, email="c@exemplo.test", kind=MembershipKind.EXTERNAL, roles=[])


def test_expired_trial_blocks_invitations() -> None:
    org, owner = factories.organization()
    with tenant_context(org.id):
        Subscription.objects.update(trial_ends_at=timezone.now() - timedelta(seconds=1))
        with pytest.raises(PlanLimitExceededError, match="avaliação"):
            services.invite(
                factories.principal(org, owner),
                email="z@exemplo.test",
                kind=MembershipKind.INTERNAL,
                roles=["approver"],
            )


def test_duplicate_and_invalid_invitations() -> None:
    org, owner = factories.organization()
    principal = factories.principal(org, owner)
    with tenant_context(org.id):
        with pytest.raises(ConflictError):
            services.invite(
                principal, email=owner.email, kind=MembershipKind.INTERNAL, roles=["approver"]
            )
        services.invite(
            principal, email="d@exemplo.test", kind=MembershipKind.INTERNAL, roles=["approver"]
        )
        with pytest.raises(ConflictError):
            services.invite(
                principal, email="D@exemplo.test", kind=MembershipKind.INTERNAL, roles=["approver"]
            )
        with pytest.raises(DomainValidationError):
            services.invite(
                principal, email="e@exemplo.test", kind=MembershipKind.INTERNAL, roles=[]
            )
        with pytest.raises(DomainValidationError):
            services.invite(
                principal, email="e@exemplo.test", kind=MembershipKind.EXTERNAL, roles=["approver"]
            )
        with pytest.raises(DomainValidationError):
            services.invite(
                principal, email="e@exemplo.test", kind=MembershipKind.INTERNAL, roles=["rei"]
            )


def test_only_owner_grants_owner_role() -> None:
    org, _ = factories.organization()
    admin = factories.member(org, [Role.ADMIN])
    with tenant_context(org.id), pytest.raises(AccessDeniedError):
        services.invite(
            factories.principal(org, admin),
            email="f@exemplo.test",
            kind=MembershipKind.INTERNAL,
            roles=["owner"],
        )


def test_revocation_takes_effect_on_next_request_with_same_session() -> None:
    org, owner = factories.organization()
    reviewer = factories.member(org, [Role.REVIEWER])
    client = factories.client_for(reviewer, org)
    assert client.get("/api/v1/departments").status_code == 200
    with tenant_context(org.id):
        membership = Membership.objects.get(user=reviewer)
    revoke = factories.client_for(owner, org).post(
        f"/api/v1/members/{membership.id}/revoke",
        {"reason": "Desligamento"},
        content_type="application/json",
    )
    assert revoke.status_code == 200 and revoke.json()["status"] == MembershipStatus.REVOKED
    denied = client.get("/api/v1/departments")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "organization_not_selected"


def test_last_owner_cannot_be_revoked_or_demoted() -> None:
    org, owner = factories.organization()
    principal = factories.principal(org, owner)
    with tenant_context(org.id):
        membership = Membership.objects.get(user=owner)
        with pytest.raises(ConflictError):
            services.revoke_membership(principal, membership.id, reason="teste")
        with pytest.raises(ConflictError):
            services.update_membership(
                principal, membership.id, roles=["admin"], department_ids=[], team_ids=[]
            )


def test_update_membership_replaces_links_and_audits() -> None:
    org, owner = factories.organization()
    dept, team = factories.department(org), factories.team(org)
    person = factories.member(org, [Role.REVIEWER])
    client = factories.client_for(owner, org)
    with tenant_context(org.id):
        membership = Membership.objects.get(user=person)
    response = client.patch(
        f"/api/v1/members/{membership.id}",
        {
            "roles": ["approver", "reviewer"],
            "department_ids": [str(dept.id)],
            "team_ids": [str(team.id)],
        },
        content_type="application/json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["roles"] == ["approver", "reviewer"]
    assert response.json()["team_ids"] == [str(team.id)]
    audit = client.get("/api/v1/audit-events?action=membership.updated").json()["results"]
    assert audit[0]["changes"]["roles"]["after"] == ["approver", "reviewer"]


def test_revoke_requires_reason_and_active_membership() -> None:
    org, owner = factories.organization()
    person = factories.member(org)
    principal = factories.principal(org, owner)
    with tenant_context(org.id):
        membership = Membership.objects.get(user=person)
        with pytest.raises(DomainValidationError):
            services.revoke_membership(principal, membership.id, reason="  ")
        services.revoke_membership(principal, membership.id, reason="fim")
        with pytest.raises(ConflictError):
            services.revoke_membership(principal, membership.id, reason="de novo")
        with pytest.raises(ConflictError):
            services.update_membership(
                principal, membership.id, roles=["approver"], department_ids=[], team_ids=[]
            )


def test_revoked_member_can_be_invited_again() -> None:
    org, owner = factories.organization()
    person = factories.member(org)
    principal = factories.principal(org, owner)
    with tenant_context(org.id):
        membership = Membership.objects.get(user=person)
        services.revoke_membership(principal, membership.id, reason="fim")
        invitation = services.invite(
            principal, email=person.email, kind=MembershipKind.INTERNAL, roles=["finance"]
        )
    services.accept_invitation(person, make_token(org.id, invitation.id))
    assert factories.principal(org, person).roles == {Role.FINANCE}


def test_departments_and_teams_management() -> None:
    org, owner = factories.organization()
    client = factories.client_for(owner, org)
    assert (
        client.post(
            "/api/v1/departments", {"name": "Compras"}, content_type="application/json"
        ).status_code
        == 201
    )
    duplicate = client.post(
        "/api/v1/departments", {"name": "compras"}, content_type="application/json"
    )
    assert duplicate.status_code == 409
    assert (
        client.post(
            "/api/v1/teams", {"name": "Squad A"}, content_type="application/json"
        ).status_code
        == 201
    )
    assert [d["name"] for d in client.get("/api/v1/departments").json()] == ["Compras"]
    assert [t["name"] for t in client.get("/api/v1/teams").json()] == ["Squad A"]


def test_invitation_revocation() -> None:
    org, owner = factories.organization()
    client = factories.client_for(owner, org)
    created = client.post(
        "/api/v1/invitations",
        {"email": "g@exemplo.test", "roles": ["approver"]},
        content_type="application/json",
    ).json()
    assert client.post(f"/api/v1/invitations/{created['id']}/revoke").json()["status"] == "revoked"
    assert client.post(f"/api/v1/invitations/{created['id']}/revoke").status_code == 409
    with tenant_context(org.id):
        assert Invitation.objects.get().status == "revoked"


def test_audit_events_are_append_only() -> None:
    org, _ = factories.organization()
    with pytest.raises(Exception, match="somente-inserção"), tenant_context(org.id):
        AuditEvent.objects.update(action="adulterado")


def test_audit_is_isolated_between_organizations() -> None:
    org_a, owner_a = factories.organization("A")
    factories.organization("B")
    events = factories.client_for(owner_a, org_a).get("/api/v1/audit-events").json()["results"]
    assert {e["resource_id"] for e in events} == {str(org_a.id)}


def test_subscription_endpoint_reports_plan_and_usage() -> None:
    org, owner = factories.organization()
    body = factories.client_for(owner, org).get("/api/v1/subscription").json()
    assert body["status"] == "trialing" and body["usage"] == {"users": 1}


def test_revoke_all_sessions_invalidates_existing_session() -> None:
    org, owner = factories.organization()
    client = factories.client_for(owner, org)
    assert client.get("/api/v1/me").status_code == 200
    services.revoke_all_sessions(owner)
    assert client.get("/api/v1/me").status_code in {401, 403}


def test_create_organization_via_api_starts_trial() -> None:
    person = factories.user()
    factories.plan("profissional")
    client = factories.client_for(person)
    response = client.post(
        "/api/v1/organizations", {"name": "Nova Ltda"}, content_type="application/json"
    )
    assert response.status_code == 201
    assert response.json()["active_organization"]["roles"] == ["owner"]


def test_principal_includes_departments_and_teams_from_request() -> None:
    org, _ = factories.organization()
    dept, team = factories.department(org), factories.team(org)
    person = factories.member(org, [Role.REVIEWER], departments=[dept], teams=[team])
    response = factories.client_for(person, org).get("/api/v1/departments")
    assert response.status_code == 200
    with tenant_context(org.id):
        assert factories.principal(org, person).department_ids == {dept.id}
