"""Modelos versionados, geração de revisões e solicitações de contrato."""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test import Client

from pactor.access.policies import Role
from pactor.contracts.models import Revision
from pactor.core.tenancy import tenant_context
from pactor.drafting.models import TemplateVersion
from pactor.identity.models import Organization, User
from pactor.intake.models import RequestEvent
from tests import factories
from tests.unit.test_drafting_domain import DEFINITION

pytestmark = pytest.mark.django_db(transaction=True)
JSON = "application/json"
VALUES = {
    "contratante": "Empresa Fictícia Ltda",
    "valor": "1500.00",
    "inicio": "2027-03-01",
    "confidencial": True,
}


def _manager() -> tuple[Organization, User]:
    org, _ = factories.organization()
    return org, factories.member(org, [Role.CONTRACT_MANAGER])


def _template(
    client: Client, *, name: str = "Prestação de serviços (modelo demonstrativo)"
) -> dict[str, object]:
    created = client.post(
        "/api/v1/templates",
        {
            "name": name,
            "contract_type": "services",
            "description": "Exemplo fictício",
            "definition": DEFINITION,
        },
        content_type=JSON,
    )
    assert created.status_code == 201, created.json()
    body = created.json()
    assert isinstance(body, dict)
    return body


def _publish(client: Client, template_id: object, version: int) -> dict[str, object]:
    response = client.post(
        f"/api/v1/templates/{template_id}/publish", {"expected_version": version}, content_type=JSON
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert isinstance(body, dict)
    return body


def test_template_versioning_and_optimistic_draft() -> None:
    org, manager = _manager()
    client = factories.client_for(manager, org)
    template = _template(client)
    v1 = _publish(client, template["id"], 1)
    assert v1["number"] == 1
    assert (
        client.post(
            f"/api/v1/templates/{template['id']}/publish",
            {"expected_version": 1},
            content_type=JSON,
        ).status_code
        == 409
    )
    changed = {
        **DEFINITION,
        "sections": [
            *DEFINITION["sections"],
            {"key": "extra", "title": "Extra", "body": "Texto adicional."},
        ],
    }
    saved = client.put(
        f"/api/v1/templates/{template['id']}/draft",
        {"definition": changed, "expected_version": 1},
        content_type=JSON,
    )
    assert saved.status_code == 200
    stale = client.put(
        f"/api/v1/templates/{template['id']}/draft",
        {"definition": DEFINITION, "expected_version": 1},
        content_type=JSON,
    )
    assert stale.status_code == 409
    v2 = _publish(client, template["id"], 2)
    assert v2["number"] == 2
    detail = client.get(f"/api/v1/templates/{template['id']}").json()
    assert [v["number"] for v in detail["versions"]] == [1, 2]
    invalid = client.put(
        f"/api/v1/templates/{template['id']}/draft",
        {
            "definition": {"fields": [], "sections": [{"key": "s", "title": "S", "body": "{{x}}"}]},
            "expected_version": 2,
        },
        content_type=JSON,
    )
    assert invalid.status_code == 400
    with (
        pytest.raises(Exception, match="somente-inserção"),
        tenant_context(org.id),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE drafting_templateversion SET change_note = 'x' WHERE id = %s", [str(v1["id"])]
        )


def test_generation_records_template_version_and_data_and_is_immutable() -> None:
    org, manager = _manager()
    client = factories.client_for(manager, org)
    template = _template(client)
    v1 = _publish(client, template["id"], 1)
    contract = factories.contract(org, manager)
    generated = client.post(
        "/api/v1/templates/generate",
        {
            "contract_id": str(contract.id),
            "template_version_id": v1["id"],
            "values": VALUES,
            "expected_draft_version": 1,
        },
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="gerar-revisao-1",
    )
    assert generated.status_code == 201, generated.json()
    revision = generated.json()
    assert revision["source"] == "template"
    assert revision["template_version_id"] == v1["id"]
    assert revision["generation_data"] == {
        "confidencial": True,
        "contratante": "Empresa Fictícia Ltda",
        "inicio": "2027-03-01",
        "valor": "1500.00",
    }
    assert "R$ 1.500,00" in revision["content"]
    # Nova versão do modelo não altera a revisão já produzida.
    changed = {
        **DEFINITION,
        "sections": [
            {"key": "objeto", "title": "Objeto", "body": "Texto totalmente novo {{contratante}}."}
        ],
    }
    client.put(
        f"/api/v1/templates/{template['id']}/draft",
        {"definition": changed, "expected_version": 1},
        content_type=JSON,
    )
    _publish(client, template["id"], 2)
    with tenant_context(org.id):
        stored = Revision.objects.get(id=str(revision["id"]))
        assert stored.content == revision["content"]
        assert TemplateVersion.objects.get(id=str(v1["id"])).definition == DEFINITION


def test_generation_rejects_invalid_data_and_type_mismatch() -> None:
    org, manager = _manager()
    client = factories.client_for(manager, org)
    template = _template(client)
    v1 = _publish(client, template["id"], 1)
    contract = factories.contract(org, manager)
    invalid = client.post(
        "/api/v1/templates/generate",
        {
            "contract_id": str(contract.id),
            "template_version_id": v1["id"],
            "values": {"valor": "x"},
            "expected_draft_version": 1,
        },
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="gerar-invalido",
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["details"]["contratante"] == "obrigatório"
    nda = client.post(
        "/api/v1/templates",
        {"name": "NDA (modelo demonstrativo)", "contract_type": "nda", "definition": DEFINITION},
        content_type=JSON,
    ).json()
    nda_v1 = _publish(client, nda["id"], 1)
    mismatch = client.post(
        "/api/v1/templates/generate",
        {
            "contract_id": str(contract.id),
            "template_version_id": nda_v1["id"],
            "values": VALUES,
            "expected_draft_version": 1,
        },
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="gerar-tipo-errado",
    )
    assert mismatch.status_code == 400
    missing = client.post(
        "/api/v1/templates/generate",
        {
            "contract_id": str(contract.id),
            "template_version_id": str(uuid.uuid4()),
            "values": VALUES,
            "expected_draft_version": 1,
        },
        content_type=JSON,
        HTTP_IDEMPOTENCY_KEY="gerar-inexistente",
    )
    assert missing.status_code == 404


def test_templates_require_permission_and_are_isolated() -> None:
    org, manager = _manager()
    client = factories.client_for(manager, org)
    _template(client)
    assert (
        client.post(
            "/api/v1/templates",
            {
                "name": "Prestação de serviços (modelo demonstrativo)",
                "contract_type": "services",
                "definition": DEFINITION,
            },
            content_type=JSON,
        ).status_code
        == 409
    )
    approver = factories.client_for(factories.member(org, [Role.APPROVER]), org)
    assert approver.get("/api/v1/templates").status_code == 200
    assert (
        approver.post(
            "/api/v1/templates",
            {"name": "X", "contract_type": "nda", "definition": DEFINITION},
            content_type=JSON,
        ).status_code
        == 403
    )
    other_org, other_manager = _manager()
    assert factories.client_for(other_manager, other_org).get("/api/v1/templates").json() == []


def _request_payload(cp_id: object | None = None) -> dict[str, object]:
    return {
        "title": "Contratação de suporte (solicitação fictícia)",
        "contract_type": "services",
        "objective": "Suporte técnico mensal.",
        "priority": "high",
        "estimated_value": "3000.00",
        "desired_start": "2027-01-01",
        "desired_end": "2027-12-31",
        **({"counterparty_id": str(cp_id)} if cp_id else {}),
    }


def test_request_lifecycle_visibility_and_conversion() -> None:
    org, manager = _manager()
    requester = factories.member(org, [Role.REQUESTER])
    other_requester = factories.member(org, [Role.REQUESTER])
    cp = factories.counterparty(org)
    requester_client = factories.client_for(requester, org)
    created = requester_client.post("/api/v1/requests", _request_payload(cp.id), content_type=JSON)
    assert created.status_code == 201, created.json()
    request_id = created.json()["id"]
    assert created.json()["reference"].startswith("SOL-")
    assert (
        factories.client_for(other_requester, org).get("/api/v1/requests").json()["results"] == []
    )
    assert (
        factories.client_for(other_requester, org).get(f"/api/v1/requests/{request_id}").status_code
        == 404
    )
    manager_client = factories.client_for(manager, org)
    assert len(manager_client.get("/api/v1/requests").json()["results"]) == 1
    convert_payload = {
        "owner_id": str(manager.id),
        "organization_role": "contractor",
        "counterparty_role": "contracted",
    }
    assert (
        manager_client.post(
            f"/api/v1/requests/{request_id}/convert", convert_payload, content_type=JSON
        ).status_code
        == 409
    )
    assert (
        manager_client.post(f"/api/v1/requests/{request_id}/review", {}, content_type=JSON).json()[
            "status"
        ]
        == "in_review"
    )
    converted = manager_client.post(
        f"/api/v1/requests/{request_id}/convert", convert_payload, content_type=JSON
    )
    assert converted.status_code == 201, converted.json()
    contract = converted.json()
    assert contract["requester_id"] == str(requester.id)
    assert contract["total_value"] == "3000.00"
    detail = requester_client.get(f"/api/v1/requests/{request_id}").json()
    assert detail["status"] == "converted"
    assert detail["contract_id"] == contract["id"]
    assert [e["action"] for e in sorted(detail["events"], key=lambda e: e["created_at"])] == [
        "submitted",
        "review_started",
        "converted",
    ]
    # O solicitante enxerga o contrato gerado pelo vínculo de solicitante.
    assert requester_client.get(f"/api/v1/contracts/{contract['id']}").status_code == 200
    with pytest.raises(Exception, match="somente-inserção"), tenant_context(org.id):
        RequestEvent.objects.update(note="adulterado")


def test_request_rejection_cancellation_and_validation() -> None:
    org, manager = _manager()
    requester = factories.member(org, [Role.REQUESTER])
    requester_client = factories.client_for(requester, org)
    manager_client = factories.client_for(manager, org)
    first = requester_client.post("/api/v1/requests", _request_payload(), content_type=JSON).json()
    assert (
        manager_client.post(
            f"/api/v1/requests/{first['id']}/reject",
            {"reason": "Fora do orçamento"},
            content_type=JSON,
        ).json()["status"]
        == "rejected"
    )
    assert (
        requester_client.post(
            f"/api/v1/requests/{first['id']}/cancel", {"reason": "x"}, content_type=JSON
        ).status_code
        == 409
    )
    second = requester_client.post("/api/v1/requests", _request_payload(), content_type=JSON).json()
    assert (
        requester_client.post(
            f"/api/v1/requests/{second['id']}/cancel",
            {"reason": "Não é mais necessário"},
            content_type=JSON,
        ).json()["status"]
        == "cancelled"
    )
    third = requester_client.post("/api/v1/requests", _request_payload(), content_type=JSON).json()
    manager_client.post(f"/api/v1/requests/{third['id']}/review", {}, content_type=JSON)
    no_counterparty = manager_client.post(
        f"/api/v1/requests/{third['id']}/convert",
        {
            "owner_id": str(manager.id),
            "organization_role": "contractor",
            "counterparty_role": "contracted",
        },
        content_type=JSON,
    )
    assert no_counterparty.status_code == 400
    bad_dates = {**_request_payload(), "desired_start": "2027-12-31", "desired_end": "2027-01-01"}
    assert (
        requester_client.post("/api/v1/requests", bad_dates, content_type=JSON).status_code == 400
    )
    assert (
        requester_client.post(
            "/api/v1/requests",
            {**_request_payload(), "department_id": str(uuid.uuid4())},
            content_type=JSON,
        ).status_code
        == 400
    )
    assert (
        requester_client.post(
            f"/api/v1/requests/{third['id']}/review", {}, content_type=JSON
        ).status_code
        == 403
    )
    assert len(manager_client.get("/api/v1/requests?status=rejected").json()["results"]) == 1
    approver = factories.client_for(factories.member(org, [Role.APPROVER]), org)
    assert approver.get("/api/v1/requests").json()["results"] == []
