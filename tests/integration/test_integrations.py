"""Integrações OAuth (client credentials): tenant, escopos, visibilidade e revogação."""

from __future__ import annotations

import pytest
from django.test import Client

from pactor.access.policies import Role
from pactor.core.errors import DomainValidationError
from pactor.core.tenancy import tenant_context
from pactor.identity.models import Organization, User
from pactor.integrations import services
from pactor.integrations.models import IntegrationClient
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)
JSON = "application/json"


def _issue(
    org: Organization, owner: User, *, roles: list[str], scopes: list[str]
) -> dict[str, object]:
    response = factories.client_for(owner, org).post(
        "/api/v1/integrations",
        {"name": "ERP fictício", "roles": roles, "scopes": scopes},
        content_type=JSON,
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert isinstance(body, dict)
    return body


def _token(issued: dict[str, object], scope: str | None = None) -> str:
    data = {
        "grant_type": "client_credentials",
        "client_id": issued["client_id"],
        "client_secret": issued["client_secret"],
    }
    if scope is not None:
        data["scope"] = scope
    response = Client().post("/o/token/", data)
    assert response.status_code == 200, response.content
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


def _api(token: str) -> Client:
    return Client(HTTP_AUTHORIZATION=f"Bearer {token}")


def test_client_credentials_token_acts_within_its_organization_and_roles() -> None:
    org, owner = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    internal = factories.contract(org, manager)
    confidential = factories.contract(org, manager, confidentiality="confidential")
    issued = _issue(
        org, owner, roles=["reviewer"], scopes=["contracts.read", "counterparties.read"]
    )
    with tenant_context(org.id):
        stored = IntegrationClient.objects.get(id=str(issued["id"]))
    assert stored.oauth_client_id == issued["client_id"]
    api = _api(_token(issued))
    listing = api.get("/api/v1/contracts").json()["results"]
    assert {c["id"] for c in listing} == {str(internal.id)}
    assert api.get(f"/api/v1/contracts/{confidential.id}").status_code == 404
    assert api.get("/api/v1/counterparties").status_code == 200
    assert api.post("/api/v1/counterparties", {}, content_type=JSON).status_code == 403
    assert api.get("/api/v1/me").status_code in {401, 403}


def test_token_scopes_restrict_and_never_extend_permissions() -> None:
    org, owner = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    factories.contract(org, manager)
    issued = _issue(
        org, owner, roles=["contract_manager"], scopes=["contracts.read", "counterparties.read"]
    )
    narrow = _api(_token(issued, scope="counterparties.read"))
    assert narrow.get("/api/v1/contracts").json()["results"] == []
    assert narrow.get("/api/v1/counterparties").status_code == 200
    denied = Client().post(
        "/o/token/",
        {
            "grant_type": "client_credentials",
            "client_id": issued["client_id"],
            "client_secret": issued["client_secret"],
            "scope": "reports.read",
        },
    )
    assert denied.status_code == 400


def test_tokens_are_isolated_between_organizations() -> None:
    org_a, owner_a = factories.organization("A")
    org_b, _ = factories.organization("B")
    manager_b = factories.member(org_b, [Role.CONTRACT_MANAGER])
    contract_b = factories.contract(org_b, manager_b)
    issued = _issue(org_a, owner_a, roles=["contract_manager"], scopes=["contracts.read"])
    api = _api(_token(issued))
    assert api.get("/api/v1/contracts").json()["results"] == []
    assert api.get(f"/api/v1/contracts/{contract_b.id}").status_code == 404


def test_revocation_invalidates_existing_tokens() -> None:
    org, owner = factories.organization()
    issued = _issue(org, owner, roles=["reviewer"], scopes=["contracts.read"])
    token = _token(issued)
    assert _api(token).get("/api/v1/contracts").status_code == 200
    revoked = factories.client_for(owner, org).post(f"/api/v1/integrations/{issued['id']}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert _api(token).get("/api/v1/contracts").status_code in {401, 403}
    assert (
        factories.client_for(owner, org)
        .post(f"/api/v1/integrations/{issued['id']}/revoke")
        .status_code
        == 409
    )


def test_wrong_secret_and_invalid_tokens_are_rejected() -> None:
    org, owner = factories.organization()
    issued = _issue(org, owner, roles=["reviewer"], scopes=["contracts.read"])
    wrong = Client().post(
        "/o/token/",
        {
            "grant_type": "client_credentials",
            "client_id": issued["client_id"],
            "client_secret": "errado",
        },
    )
    assert wrong.status_code == 401
    invalid = _api("token-inexistente").get("/api/v1/contracts")
    assert invalid.status_code == 401
    assert invalid["WWW-Authenticate"].startswith("Bearer")


def test_client_management_rules() -> None:
    org, owner = factories.organization()
    principal = factories.principal(org, owner)
    with tenant_context(org.id):
        with pytest.raises(DomainValidationError, match="gestão"):
            services.create_client(principal, name="X", roles=["admin"], scopes=["contracts.read"])
        with pytest.raises(DomainValidationError, match="Escopos"):
            services.create_client(
                principal, name="X", roles=["reviewer"], scopes=["members.manage"]
            )
        with pytest.raises(DomainValidationError, match="nome"):
            services.create_client(
                principal, name=" ", roles=["reviewer"], scopes=["contracts.read"]
            )
        with pytest.raises(DomainValidationError, match="Papel"):
            services.create_client(principal, name="X", roles=["rei"], scopes=["contracts.read"])
    listing = factories.client_for(owner, org).get("/api/v1/integrations").json()
    assert listing == []
    without_mfa = factories.client_for(owner, org, mfa=False).get("/api/v1/integrations")
    assert without_mfa.status_code == 403
    assert without_mfa.json()["error"]["code"] == "mfa_required"
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    assert factories.client_for(manager, org).get("/api/v1/integrations").status_code == 403


def test_token_is_accepted_only_in_authorization_header() -> None:
    org, owner = factories.organization()
    issued = _issue(org, owner, roles=["reviewer"], scopes=["contracts.read"])
    token = _token(issued)
    assert Client().get(f"/api/v1/contracts?access_token={token}").status_code == 401
    assert Client(HTTP_AUTHORIZATION=f"Token {token}").get("/api/v1/contracts").status_code == 401


def test_integration_creates_requests_as_its_service_user() -> None:
    org, owner = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    counterparty = factories.counterparty(org)
    issued = _issue(org, owner, roles=["requester"], scopes=["requests.create"])
    api = _api(_token(issued))
    created = api.post(
        "/api/v1/requests",
        {
            "title": "Pedido vindo do ERP (fictício)",
            "contract_type": "services",
            "objective": "Contratar suporte.",
            "priority": "normal",
            "counterparty_id": str(counterparty.id),
        },
        content_type=JSON,
    )
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["requester_name"] == "ERP fictício (integração)"
    with tenant_context(org.id):
        client = IntegrationClient.objects.select_related("service_user").get(id=str(issued["id"]))
    service_user = client.service_user
    assert body["requester_id"] == str(service_user.id)
    assert body["events"][0]["actor_id"] == str(service_user.id)
    assert not service_user.is_active
    assert not service_user.has_usable_password()
    assert [r["id"] for r in api.get("/api/v1/requests").json()["results"]] == [body["id"]]
    assert not Client().login(email=service_user.email, password="")

    staff = factories.client_for(manager, org)
    assert (
        staff.post(f"/api/v1/requests/{body['id']}/review", {}, content_type=JSON).status_code
        == 200
    )
    converted = staff.post(
        f"/api/v1/requests/{body['id']}/convert",
        {
            "owner_id": str(manager.id),
            "organization_role": "contractor",
            "counterparty_role": "contracted",
        },
        content_type=JSON,
    )
    assert converted.status_code == 201, converted.json()
    assert converted.json()["requester_id"] is None
    directory = staff.get("/api/v1/members/directory").json()
    assert str(service_user.id) not in {entry["user_id"] for entry in directory}
