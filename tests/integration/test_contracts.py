"""Contrapartes, contratos, rascunhos, revisões, comentários e compartilhamento."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from django.db import connection
from django.test import Client
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pactor.access.policies import (
    Confidentiality,
    Principal,
    PrincipalKind,
    Role,
    can_view_contract,
)
from pactor.contracts import services
from pactor.contracts.domain import ContractType, PartyRole
from pactor.contracts.models import Contract, ContractGrant, GrantSource, Revision
from pactor.contracts.visibility import facts_for, visibility_q
from pactor.core.errors import DomainValidationError
from pactor.core.tenancy import tenant_context
from pactor.identity.models import MembershipKind, Organization, User
from tests import factories

if TYPE_CHECKING:
    # Tipo de resposta do cliente de testes declarado apenas nos stubs do Django.
    from django.test.client import _MonkeyPatchedWSGIResponse as TestResponse

pytestmark = pytest.mark.django_db(transaction=True)

CNPJ = "11.222.333/0001-81"
JSON = "application/json"


def _counterparty_payload(**extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "company",
        "name": "Fornecedora Exemplo Ltda (fictícia)",
        "document": CNPJ,
        "contacts": [{"name": "Contato Fictício", "email": "contato@exemplo.test"}],
        "addresses": [
            {
                "street": "Rua Exemplo",
                "number": "100",
                "city": "São Paulo",
                "state": "sp",
                "postal_code": "01001-000",
            }
        ],
        "representatives": [{"name": "Representante Fictício", "document": "529.982.247-25"}],
    }
    payload.update(extra)
    return payload


def _manager() -> tuple[Organization, User, Client]:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    return org, manager, factories.client_for(manager, org)


def _create_payload(
    counterparty_id: uuid.UUID, owner_id: uuid.UUID, **extra: object
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Prestação de serviços de suporte (exemplo fictício)",
        "contract_type": "services",
        "counterparty_id": str(counterparty_id),
        "organization_role": "contractor",
        "counterparty_role": "contracted",
        "owner_id": str(owner_id),
        "total_value": "12000.00",
        "tags": ["Suporte", "ti"],
        "initial_content": "Cláusula 1. Objeto.\nCláusula 2. Prazo.",
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Contrapartes
# ---------------------------------------------------------------------------
def test_counterparty_create_normalizes_and_rejects_duplicates_and_invalid() -> None:
    _, _, client = _manager()
    created = client.post("/api/v1/counterparties", _counterparty_payload(), content_type=JSON)
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["document"] == "11222333000181"
    assert body["addresses"][0]["state"] == "SP"
    assert body["addresses"][0]["postal_code"] == "01001000"
    assert body["representatives"][0]["document"] == "52998224725"
    duplicate = client.post("/api/v1/counterparties", _counterparty_payload(), content_type=JSON)
    assert duplicate.status_code == 409
    invalid = client.post(
        "/api/v1/counterparties",
        _counterparty_payload(document="11.222.333/0001-80"),
        content_type=JSON,
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["details"]["document"]


def test_counterparty_address_validation() -> None:
    _, _, client = _manager()
    bad_state = _counterparty_payload(
        addresses=[{"street": "R", "city": "C", "state": "XX", "postal_code": "01001000"}]
    )
    assert client.post("/api/v1/counterparties", bad_state, content_type=JSON).status_code == 400
    bad_cep = _counterparty_payload(
        addresses=[{"street": "R", "city": "C", "state": "SP", "postal_code": "123"}]
    )
    assert client.post("/api/v1/counterparties", bad_cep, content_type=JSON).status_code == 400


def test_counterparty_update_uses_optimistic_concurrency() -> None:
    _, _, client = _manager()
    created = client.post(
        "/api/v1/counterparties", _counterparty_payload(), content_type=JSON
    ).json()
    url = f"/api/v1/counterparties/{created['id']}"
    first = client.patch(url, {"expected_version": 1, "phone": "11 4000-0000"}, content_type=JSON)
    assert first.status_code == 200
    assert first.json()["version"] == 2
    stale = client.patch(url, {"expected_version": 1, "phone": "11 5000-0000"}, content_type=JSON)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"
    assert stale.json()["error"]["details"]["current_version"] == 2
    assert client.get(url).json()["phone"] == "11 4000-0000"


def test_counterparty_listing_filters_and_permissions() -> None:
    org, _, client = _manager()
    client.post("/api/v1/counterparties", _counterparty_payload(), content_type=JSON)
    assert (
        client.get("/api/v1/counterparties?q=exemplo")
        .json()["results"][0]["name"]
        .startswith("Fornecedora")
    )
    assert client.get("/api/v1/counterparties?q=inexistente").json()["results"] == []
    unknown = client.get("/api/v1/counterparties?campo=1")
    assert unknown.status_code == 400
    assert unknown.json()["error"]["details"]["campo"] == ["Filtro não permitido."]
    approver = factories.client_for(factories.member(org, [Role.APPROVER]), org)
    assert approver.get("/api/v1/counterparties").status_code == 200
    assert (
        approver.post(
            "/api/v1/counterparties", _counterparty_payload(), content_type=JSON
        ).status_code
        == 403
    )


def test_counterparties_are_isolated_between_organizations() -> None:
    _, _, client_a = _manager()
    client_a.post("/api/v1/counterparties", _counterparty_payload(), content_type=JSON)
    _, _, client_b = _manager()
    assert client_b.get("/api/v1/counterparties").json()["results"] == []
    # O mesmo documento pode existir em outra organização.
    assert (
        client_b.post(
            "/api/v1/counterparties", _counterparty_payload(), content_type=JSON
        ).status_code
        == 201
    )


# ---------------------------------------------------------------------------
# Contratos e rascunho
# ---------------------------------------------------------------------------
def test_create_contract_assigns_sequential_reference_and_is_idempotent() -> None:
    org, manager, client = _manager()
    cp = factories.counterparty(org)
    payload = _create_payload(cp.id, manager.id)
    first = client.post(
        "/api/v1/contracts", payload, content_type=JSON, HTTP_IDEMPOTENCY_KEY="criar-contrato-1"
    )
    assert first.status_code == 201, first.json()
    replay = client.post(
        "/api/v1/contracts", payload, content_type=JSON, HTTP_IDEMPOTENCY_KEY="criar-contrato-1"
    )
    assert replay.status_code == 201
    assert replay["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == first.json()["id"]
    changed = dict(payload, title="Outro título")
    conflict = client.post(
        "/api/v1/contracts", changed, content_type=JSON, HTTP_IDEMPOTENCY_KEY="criar-contrato-1"
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    second = client.post(
        "/api/v1/contracts", changed, content_type=JSON, HTTP_IDEMPOTENCY_KEY="criar-contrato-2"
    )
    year = first.json()["reference"].split("-")[1]
    assert first.json()["reference"] == f"CT-{year}-00001"
    assert second.json()["reference"] == f"CT-{year}-00002"
    assert first.json()["tags"] == ["suporte", "ti"]
    with tenant_context(org.id):
        assert Contract.objects.count() == 2


def test_domain_error_rolls_back_request_including_idempotency_reservation() -> None:
    """A chave é reservada antes da validação; o erro desfaz a reserva junto com o resto."""
    org, manager, client = _manager()
    valid = _create_payload(factories.counterparty(org).id, manager.id)
    invalid = dict(valid, owner_id=str(uuid.uuid4()))
    failed = client.post(
        "/api/v1/contracts", invalid, content_type=JSON, HTTP_IDEMPOTENCY_KEY="reserva-desfeita"
    )
    assert failed.status_code == 400
    retried = client.post(
        "/api/v1/contracts", valid, content_type=JSON, HTTP_IDEMPOTENCY_KEY="reserva-desfeita"
    )
    assert retried.status_code == 201, retried.json()
    with tenant_context(org.id):
        assert Contract.objects.count() == 1


def test_create_contract_requires_idempotency_key() -> None:
    org, manager, client = _manager()
    response = client.post(
        "/api/v1/contracts",
        _create_payload(factories.counterparty(org).id, manager.id),
        content_type=JSON,
    )
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["error"]["message"]


def test_create_contract_validations() -> None:
    org, manager, _ = _manager()
    cp = factories.counterparty(org)
    principal = factories.principal(org, manager)
    other_org, _ = factories.organization()
    foreign_cp = factories.counterparty(other_org)
    base = services.ContractInput(
        title="T",
        contract_type=ContractType.SERVICES,
        counterparty_id=cp.id,
        organization_role=PartyRole.CONTRACTOR,
        counterparty_role=PartyRole.CONTRACTED,
        owner_id=manager.id,
    )
    cases = [
        (replace(base, counterparty_id=foreign_cp.id), "Contraparte"),
        (replace(base, owner_id=uuid.uuid4()), "membro"),
        (replace(base, contract_type=ContractType.AMENDMENT), "Aditivo"),
        (replace(base, parent_id=uuid.uuid4()), "aditivos"),
        (replace(base, total_value=Decimal("NaN")), "finito"),
        (replace(base, total_value=Decimal("1.001")), "duas casas"),
        (replace(base, total_value=Decimal("-1")), "limites"),
        (replace(base, planned_start=date(2027, 1, 2), planned_end=date(2027, 1, 1)), "Término"),
        (replace(base, title="  "), "título"),
        (replace(base, department_id=uuid.uuid4()), "Departamento"),
        (replace(base, tags=("x" * 41,)), "Etiquetas"),
    ]
    for data, message in cases:
        with pytest.raises(DomainValidationError, match=message), tenant_context(org.id):
            services.create_contract(principal, data)


def _create(client: Client, org: Organization, owner: User, **extra: object) -> dict[str, object]:
    response = client.post(
        "/api/v1/contracts",
        _create_payload(factories.counterparty(org).id, owner.id, **extra),
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY=f"criar-{uuid.uuid4().hex}",
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert isinstance(body, dict)
    return body


def _publish(
    client: Client, contract_id: object, draft_version: int, *, key: str | None = None
) -> TestResponse:
    return client.post(
        f"/api/v1/contracts/{contract_id}/revisions",
        {"expected_draft_version": draft_version, "summary": "Versão para revisão"},
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY=key or f"publicar-{uuid.uuid4().hex}",
    )


def test_draft_save_detects_concurrent_edit() -> None:
    org, manager, client = _manager()
    contract = _create(client, org, manager)
    url = f"/api/v1/contracts/{contract['id']}/draft"
    draft = client.get(url).json()
    assert draft["version"] == 1
    ok = client.put(url, {"content": "Texto A", "expected_version": 1}, content_type=JSON)
    assert ok.status_code == 200
    assert ok.json()["version"] == 2
    stale = client.put(url, {"content": "Texto B", "expected_version": 1}, content_type=JSON)
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_version"] == 2
    assert client.get(url).json()["content"] == "Texto A"


def test_publish_revision_snapshot_and_immutability() -> None:
    org, manager, client = _manager()
    contract = _create(client, org, manager)
    first = _publish(client, contract["id"], 1)
    assert first.status_code == 201, first.json()
    revision = first.json()
    assert revision["number"] == 1
    assert revision["metadata"]["total_value"] == "12000.00"
    assert len(revision["fingerprint"]) == 64
    nothing_changed = _publish(client, contract["id"], 1)
    assert nothing_changed.status_code == 409
    with pytest.raises(Exception, match="somente-inserção"), tenant_context(org.id):
        Revision.objects.filter(id=revision["id"]).update(content="adulterado")
    # A exclusão é barrada pelo banco mesmo ignorando as proteções do ORM.
    with (
        pytest.raises(Exception, match="somente-inserção"),
        tenant_context(org.id),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM contracts_revision WHERE id = %s", [revision["id"]])


def test_revision_publication_requires_expected_draft_version_and_content() -> None:
    org, manager, client = _manager()
    contract = _create(client, org, manager)
    client.put(
        f"/api/v1/contracts/{contract['id']}/draft",
        {"content": "Novo", "expected_version": 1},
        content_type=JSON,
    )
    stale = _publish(client, contract["id"], 1)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"
    client.put(
        f"/api/v1/contracts/{contract['id']}/draft",
        {"content": "   ", "expected_version": 2},
        content_type=JSON,
    )
    assert _publish(client, contract["id"], 3).status_code == 400


def test_publish_is_idempotent_by_key() -> None:
    org, manager, client = _manager()
    contract = _create(client, org, manager)
    first = _publish(client, contract["id"], 1, key="publicacao-unica")
    replay = _publish(client, contract["id"], 1, key="publicacao-unica")
    assert first.json()["id"] == replay.json()["id"]
    assert replay["Idempotent-Replayed"] == "true"
    with tenant_context(org.id):
        assert Revision.objects.count() == 1


def test_revisions_listing_detail_and_textual_compare() -> None:
    org, manager, client = _manager()
    contract = _create(client, org, manager)
    base = f"/api/v1/contracts/{contract['id']}"
    r1 = _publish(client, contract["id"], 1).json()
    client.put(
        f"{base}/draft",
        {
            "content": "Cláusula 1. Objeto revisado.\nCláusula 2. Prazo.\nCláusula 3. Foro.",
            "expected_version": 1,
        },
        content_type=JSON,
    )
    r2 = _publish(client, contract["id"], 2).json()
    assert r2["number"] == 2
    assert r2["previous_id"] == r1["id"]
    listing = client.get(f"{base}/revisions").json()
    assert [r["number"] for r in listing] == [2, 1]
    detail = client.get(f"{base}/revisions/{r1['id']}").json()
    assert detail["content"].startswith("Cláusula 1. Objeto.")
    diff = client.get(f"{base}/compare?old={r1['id']}&new={r2['id']}").json()
    assert diff["kind"] == "textual"
    assert (diff["inserted_lines"], diff["deleted_lines"]) == (2, 1)
    assert client.get(f"{base}/revisions/{uuid.uuid4()}").status_code == 404


def test_comments_and_resolution() -> None:
    org, manager, client = _manager()
    contract = _create(client, org, manager)
    revision = _publish(client, contract["id"], 1).json()
    url = f"/api/v1/contracts/{contract['id']}/revisions/{revision['id']}/comments"
    created = client.post(url, {"body": "Rever prazo da cláusula 2.", "line": 2}, content_type=JSON)
    assert created.status_code == 201, created.json()
    assert (
        client.post(url, {"body": "Linha inexistente", "line": 99}, content_type=JSON).status_code
        == 400
    )
    assert client.post(url, {"body": "   "}, content_type=JSON).status_code == 400
    comment_id = created.json()["id"]
    resolve = f"/api/v1/contracts/{contract['id']}/comments/{comment_id}/resolve"
    assert client.post(resolve).json()["resolved_at"] is not None
    assert client.post(resolve).status_code == 409
    assert (
        client.post(
            f"/api/v1/contracts/{contract['id']}/comments/{uuid.uuid4()}/resolve"
        ).status_code
        == 404
    )
    assert len(client.get(url).json()) == 1


def test_metadata_update_with_optimistic_concurrency() -> None:
    org, manager, client = _manager()
    contract = _create(client, org, manager)
    url = f"/api/v1/contracts/{contract['id']}"
    ok = client.patch(
        url, {"expected_version": 1, "total_value": "15000.00", "tags": ["Novo"]}, content_type=JSON
    )
    assert ok.status_code == 200, ok.json()
    assert ok.json()["total_value"] == "15000.00"
    assert ok.json()["tags"] == ["novo"]
    stale = client.patch(url, {"expected_version": 1, "title": "x"}, content_type=JSON)
    assert stale.status_code == 409


def test_metadata_change_after_approval_invalidates_it() -> None:
    org, manager, client = _manager()
    contract = _create(client, org, manager)
    revision = _publish(client, contract["id"], 1).json()
    with tenant_context(org.id):
        Contract.objects.filter(id=str(contract["id"])).update(
            drafting_status="approved",
            approved_revision_id=revision["id"],
            approved_fingerprint=revision["fingerprint"],
        )
    current = client.get(f"/api/v1/contracts/{contract['id']}").json()
    changed = client.patch(
        f"/api/v1/contracts/{contract['id']}",
        {"expected_version": current["version"], "total_value": "99.00"},
        content_type=JSON,
    ).json()
    assert changed["drafting_status"] == "draft"
    assert changed["approved_revision_id"] is None


def test_signed_contract_rejects_new_revisions_and_edits() -> None:
    org, manager, client = _manager()
    contract = _create(client, org, manager)
    with tenant_context(org.id):
        Contract.objects.filter(id=str(contract["id"])).update(drafting_status="signed")
    draft = client.put(
        f"/api/v1/contracts/{contract['id']}/draft",
        {"content": "x", "expected_version": 1},
        content_type=JSON,
    )
    assert draft.status_code == 409
    assert draft.json()["error"]["code"] == "invalid_transition"
    assert _publish(client, contract["id"], 1).status_code == 409


# ---------------------------------------------------------------------------
# Visibilidade e compartilhamento
# ---------------------------------------------------------------------------
def test_visibility_by_role_department_and_confidentiality() -> None:
    org, owner = factories.organization()
    legal = factories.department(org, "Jurídico")
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    reviewer_in = factories.member(org, [Role.REVIEWER], departments=[legal])
    reviewer_out = factories.member(org, [Role.REVIEWER])
    admin = factories.member(org, [Role.ADMIN])
    internal = factories.contract(org, manager)
    restricted = factories.contract(org, manager, confidentiality="restricted", department=legal)
    confidential = factories.contract(org, manager, confidentiality="confidential")

    def visible(person: User) -> set[str]:
        response = factories.client_for(person, org).get("/api/v1/contracts")
        assert response.status_code == 200
        return {c["id"] for c in response.json()["results"]}

    ids = {str(internal.id), str(restricted.id), str(confidential.id)}
    assert visible(manager) == ids
    assert visible(reviewer_in) == {str(internal.id), str(restricted.id)}
    assert visible(reviewer_out) == {str(internal.id)}
    assert visible(owner) == set()
    assert visible(admin) == set()
    hidden = factories.client_for(reviewer_out, org).get(f"/api/v1/contracts/{confidential.id}")
    missing = factories.client_for(reviewer_out, org).get(f"/api/v1/contracts/{uuid.uuid4()}")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"] == {
        **missing.json()["error"],
        "request_id": hidden.json()["error"]["request_id"],
    }


def test_confidential_owner_sees_and_explicit_share_grants_access() -> None:
    org, _ = factories.organization()
    owner = factories.member(org, [Role.CONTRACT_MANAGER])
    reviewer = factories.member(org, [Role.REVIEWER])
    external = factories.member(org, kind=MembershipKind.EXTERNAL)
    team = factories.team(org)
    teammate = factories.member(org, [Role.APPROVER], teams=[team])
    confidential = factories.contract(org, owner, confidentiality="confidential")
    owner_client = factories.client_for(owner, org)
    url = f"/api/v1/contracts/{confidential.id}"
    assert factories.client_for(reviewer, org).get(url).status_code == 404
    shared = owner_client.post(f"{url}/grants", {"user_id": str(reviewer.id)}, content_type=JSON)
    assert shared.status_code == 201
    reviewer_client = factories.client_for(reviewer, org)
    reviewer_view = reviewer_client.get(url).json()
    assert reviewer_view["can_edit"] is False
    draft = reviewer_client.put(
        f"{url}/draft", {"content": "x", "expected_version": 1}, content_type=JSON
    )
    assert draft.status_code == 403
    assert (
        owner_client.post(f"{url}/grants", {"team_id": str(team.id)}, content_type=JSON).status_code
        == 201
    )
    assert factories.client_for(teammate, org).get(url).status_code == 200
    assert (
        owner_client.post(
            f"{url}/grants", {"user_id": str(external.id), "can_edit": True}, content_type=JSON
        ).status_code
        == 400
    )
    assert (
        owner_client.post(
            f"{url}/grants", {"user_id": str(external.id)}, content_type=JSON
        ).status_code
        == 201
    )
    external_client = factories.client_for(external, org)
    assert external_client.get(url).status_code == 200
    assert {c["id"] for c in external_client.get("/api/v1/contracts").json()["results"]} == {
        str(confidential.id)
    }
    grant_id = shared.json()["id"]
    assert owner_client.delete(f"{url}/grants/{grant_id}").status_code == 204
    assert factories.client_for(reviewer, org).get(url).status_code == 404
    assert owner_client.delete(f"{url}/grants/{grant_id}").status_code == 404


def test_share_validations() -> None:
    org, _ = factories.organization()
    owner = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, owner)
    client = factories.client_for(owner, org)
    url = f"/api/v1/contracts/{contract.id}/grants"
    assert client.post(url, {}, content_type=JSON).status_code == 400
    both = {"user_id": str(owner.id), "team_id": str(uuid.uuid4())}
    assert client.post(url, both, content_type=JSON).status_code == 400
    assert client.post(url, {"user_id": str(uuid.uuid4())}, content_type=JSON).status_code == 400
    assert client.post(url, {"team_id": str(uuid.uuid4())}, content_type=JSON).status_code == 400
    outsider_org, outsider = factories.organization()
    del outsider_org
    assert client.post(url, {"user_id": str(outsider.id)}, content_type=JSON).status_code == 400
    reviewer = factories.member(org, [Role.REVIEWER])
    first = client.post(url, {"user_id": str(reviewer.id)}, content_type=JSON).json()
    upgraded = client.post(
        url, {"user_id": str(reviewer.id), "can_edit": True}, content_type=JSON
    ).json()
    assert first["id"] == upgraded["id"]
    assert upgraded["can_edit"] is True


def test_contract_listing_filters() -> None:
    org, manager, client = _manager()
    a = _create(client, org, manager, tags=["Urgente"])
    _create(
        client,
        org,
        manager,
        title="Licença de software (exemplo fictício)",
        contract_type="licensing",
    )
    results = client.get("/api/v1/contracts?tag=urgente").json()["results"]
    assert [c["id"] for c in results] == [a["id"]]
    assert len(client.get("/api/v1/contracts?contract_type=licensing").json()["results"]) == 1
    assert len(client.get(f"/api/v1/contracts?q={a['reference']}").json()["results"]) == 1
    assert len(client.get("/api/v1/contracts?q=software").json()["results"]) == 1
    assert client.get("/api/v1/contracts?drafting_status=invalido").status_code == 400


def test_contracts_are_isolated_between_organizations() -> None:
    org_a, manager_a, client_a = _manager()
    contract = _create(client_a, org_a, manager_a)
    org_b, manager_b, client_b = _manager()
    assert client_b.get("/api/v1/contracts").json()["results"] == []
    assert client_b.get(f"/api/v1/contracts/{contract['id']}").status_code == 404
    assert client_b.get(f"/api/v1/contracts/{contract['id']}/draft").status_code == 404
    foreign_cp = factories.counterparty(org_a)
    response = client_b.post(
        "/api/v1/contracts",
        _create_payload(foreign_cp.id, manager_b.id),
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="criar-estrangeiro",
    )
    assert response.status_code == 400
    with tenant_context(org_b.id):
        assert Contract.objects.count() == 0


# ---------------------------------------------------------------------------
# Equivalência entre política e consulta
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VisibilityScenario:
    organization: Organization
    actor: User
    department_id: uuid.UUID
    team_id: uuid.UUID
    contract_ids: tuple[uuid.UUID, ...]


@pytest.fixture
def visibility_scenario(db: None) -> VisibilityScenario:
    """Contratos cobrindo todas as combinações de classificação e vínculo.

    Os dados são somente leitura durante o teste de propriedade, por isso podem
    ser compartilhados entre os exemplos gerados pelo Hypothesis.
    """
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    actor = factories.user()
    department = factories.department(org)
    team = factories.team(org)
    ids: list[uuid.UUID] = []
    for level in Confidentiality:
        ids.extend(
            factories.contract(org, manager, confidentiality=level.value, department=dept).id
            for dept in (department, None)
        )
        requested = factories.contract(org, manager, confidentiality=level.value)
        user_grant = factories.contract(org, manager, confidentiality=level.value)
        approval_grant = factories.contract(org, manager, confidentiality=level.value)
        team_grant = factories.contract(org, manager, confidentiality=level.value)
        ids += [requested.id, user_grant.id, approval_grant.id, team_grant.id]
        with tenant_context(org.id):
            Contract.objects.filter(id=requested.id).update(requester_id=actor.id)
            ContractGrant.objects.create(
                organization=org, contract=user_grant, user=actor, granted_by=manager
            )
            ContractGrant.objects.create(
                organization=org,
                contract=approval_grant,
                user=actor,
                source=GrantSource.APPROVAL,
                granted_by=manager,
            )
            ContractGrant.objects.create(
                organization=org, contract=team_grant, team=team, granted_by=manager
            )
    return VisibilityScenario(org, actor, department.id, team.id, tuple(ids))


@pytest.mark.django_db(transaction=False)
@settings(
    max_examples=60,
    deadline=None,
    # A fixture cria dados somente leitura reutilizados por todos os exemplos.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    roles=st.frozensets(st.sampled_from(list(Role)), max_size=3),
    kind=st.sampled_from([PrincipalKind.USER, PrincipalKind.EXTERNAL]),
    in_department=st.booleans(),
    in_team=st.booleans(),
)
def test_property_visibility_query_matches_policy(
    visibility_scenario: VisibilityScenario,
    roles: frozenset[Role],
    kind: PrincipalKind,
    in_department: bool,
    in_team: bool,
) -> None:
    """Para todo principal gerado, a consulta devolve exatamente o que a política permite."""
    scenario = visibility_scenario
    principal = Principal(
        organization_id=scenario.organization.id,
        actor_id=scenario.actor.id,
        kind=kind,
        roles=roles if kind is PrincipalKind.USER else frozenset(),
        department_ids=frozenset({scenario.department_id}) if in_department else frozenset(),
        team_ids=frozenset({scenario.team_id}) if in_team else frozenset(),
        mfa_satisfied=True,
    )
    with tenant_context(scenario.organization.id):
        by_query = set(
            Contract.objects.filter(visibility_q(principal)).values_list("id", flat=True)
        )
        by_policy = {
            c.id
            for c in Contract.objects.prefetch_related("grants").filter(
                id__in=scenario.contract_ids
            )
            if can_view_contract(principal, facts_for(c))
        }
    assert by_query == by_policy
