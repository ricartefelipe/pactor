"""Valores, parcelas, confirmações imutáveis, índices registrados e reajustes."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from pactor.access.policies import Role
from pactor.contracts.models import Contract
from pactor.core.tenancy import tenant_context
from pactor.finance.models import Installment, PaymentConfirmation
from pactor.identity.models import Organization, User
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)
JSON = "application/json"


def _setup() -> tuple[Organization, User, User, Contract]:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    finance = factories.member(org, [Role.FINANCE])
    contract = factories.contract(org, manager, total_value=Decimal("12000.00"))
    return org, manager, finance, contract


def test_schedule_confirm_adjust_and_summary() -> None:
    org, _, finance, contract = _setup()
    client = factories.client_for(finance, org)
    base = f"/api/v1/contracts/{contract.id}/finance"
    first_due = timezone.localdate() - timedelta(days=40)
    scheduled = client.post(
        f"{base}/installments",
        {
            "total": "1000.00",
            "parts": 3,
            "first_due": first_due.isoformat(),
            "frequency": "monthly",
            "description": "Mensalidade",
        },
        content_type=JSON,
    )
    assert scheduled.status_code == 201, scheduled.json()
    installments = scheduled.json()
    assert [i["amount"] for i in installments] == ["333.34", "333.33", "333.33"]
    milestone = client.post(
        f"{base}/milestones",
        {"description": "Entrega do marco 1", "due_date": "2099-01-31", "amount": "500.00"},
        content_type=JSON,
    )
    assert milestone.status_code == 201
    confirmed = client.post(
        f"/api/v1/installments/{installments[0]['id']}/confirm",
        {
            "amount": "333.34",
            "confirmed_on": first_due.isoformat(),
            "reference": "Comprovante fictício 001",
        },
        content_type=JSON,
    )
    assert confirmed.status_code == 201, confirmed.json()
    again = client.post(
        f"/api/v1/installments/{installments[0]['id']}/confirm",
        {"amount": "1.00", "confirmed_on": first_due.isoformat(), "reference": "x"},
        content_type=JSON,
    )
    assert again.status_code == 409
    index = client.post(
        "/api/v1/indexes",
        {
            "series_name": "Índice demonstrativo",
            "index_source": "Valor fictício para testes",
            "period": "2027-01-15",
            "rate_percent": "10",
            "source_note": "Inserido manualmente (fictício)",
            "is_demonstrative": True,
        },
        content_type=JSON,
    )
    assert index.status_code == 201, index.json()
    assert index.json()["period"] == "2027-01-01"
    assert index.json()["is_demonstrative"] is True
    adjusted = client.post(
        f"{base}/adjustments",
        {"index_value_id": index.json()["id"], "applies_from": first_due.isoformat()},
        content_type=JSON,
    )
    assert adjusted.status_code == 201, adjusted.json()
    affected_ids = {a["installment_id"] for a in adjusted.json()["affected"]}
    assert installments[0]["id"] not in affected_ids  # confirmada não é reajustada
    summary = client.get(base).json()
    amounts = {i["sequence"]: i["amount"] for i in summary["installments"]}
    assert amounts == {1: "333.34", 2: "366.66", 3: "366.66", 4: "550.00"}
    assert summary["contracted"] == "12000.00"
    assert summary["confirmed"] == "333.34"
    assert summary["projected"] == "1616.66"
    assert summary["overdue"] == "366.66"  # parcela 2 vencida e não confirmada
    with (
        pytest.raises(Exception, match="somente-inserção"),
        tenant_context(org.id),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE finance_paymentconfirmation SET amount = 0")
    with tenant_context(org.id):
        assert PaymentConfirmation.objects.get().amount == Decimal("333.34")
        assert Installment.objects.get(sequence=1).amount == Decimal("333.34")


@pytest.mark.parametrize("value", [1000.5, "1000.555", "NaN", "-5", "Infinity"])
def test_float_and_invalid_amounts_are_rejected(value: object) -> None:
    org, _, finance, contract = _setup()
    response = factories.client_for(finance, org).post(
        f"/api/v1/contracts/{contract.id}/finance/installments",
        {"total": value, "parts": 2, "first_due": "2027-01-01", "frequency": "monthly"},
        content_type=JSON,
    )
    assert response.status_code == 400


def test_index_and_adjustment_rules() -> None:
    org, _, finance, contract = _setup()
    client = factories.client_for(finance, org)
    payload = {
        "series_name": "Índice X (fictício)",
        "index_source": "Fonte fictícia",
        "period": "2027-02-01",
        "rate_percent": "3.5",
        "source_note": "Nota",
        "is_demonstrative": True,
    }
    assert client.post("/api/v1/indexes", payload, content_type=JSON).status_code == 201
    assert client.post("/api/v1/indexes", payload, content_type=JSON).status_code == 409
    assert (
        client.post(
            "/api/v1/indexes",
            {**payload, "period": "2027-03-01", "source_note": " "},
            content_type=JSON,
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/indexes",
            {**payload, "period": "2027-03-01", "rate_percent": "1.0000001"},
            content_type=JSON,
        ).status_code
        == 400
    )
    value_id = client.get("/api/v1/indexes").json()[0]["id"]
    no_targets = client.post(
        f"/api/v1/contracts/{contract.id}/finance/adjustments",
        {"index_value_id": value_id, "applies_from": "2027-01-01"},
        content_type=JSON,
    )
    assert no_targets.status_code == 409


def test_index_value_adjusts_a_contract_once_and_never_overflows() -> None:
    org, _, finance, contract = _setup()
    client = factories.client_for(finance, org)
    base = f"/api/v1/contracts/{contract.id}/finance"
    due = (timezone.localdate() + timedelta(days=60)).isoformat()
    assert (
        client.post(
            f"{base}/milestones",
            {"description": "Marco", "due_date": due, "amount": "200000000000.00"},
            content_type=JSON,
        ).status_code
        == 201
    )
    index = {
        "series_name": "Índice Y (fictício)",
        "index_source": "Fonte fictícia",
        "source_note": "Nota",
        "is_demonstrative": True,
    }
    small = client.post(
        "/api/v1/indexes", {**index, "period": "2027-01-01", "rate_percent": "1"}, content_type=JSON
    ).json()
    huge = client.post(
        "/api/v1/indexes",
        {**index, "period": "2027-02-01", "rate_percent": "500"},
        content_type=JSON,
    ).json()
    body = {"index_value_id": small["id"], "applies_from": due}
    assert client.post(f"{base}/adjustments", body, content_type=JSON).status_code == 201
    repeated = client.post(f"{base}/adjustments", body, content_type=JSON)
    assert repeated.status_code == 409
    assert "já reajustou" in repeated.json()["error"]["message"]
    overflow = client.post(
        f"{base}/adjustments", {**body, "index_value_id": huge["id"]}, content_type=JSON
    )
    assert overflow.status_code == 400
    assert "máximo" in overflow.json()["error"]["message"]
    assert client.get(f"{base}").json()["installments"][0]["amount"] == "202000000000.00"
    too_late = client.post(
        f"{base}/installments",
        {
            "total": "100.00",
            "parts": 12,
            "first_due": "9999-06-01",
            "frequency": "monthly",
            "description": "Parcela",
        },
        content_type=JSON,
    )
    assert too_late.status_code == 400
    assert "última data" in too_late.json()["error"]["message"]
    assert (
        client.post(
            f"{base}/milestones",
            {"description": "X", "due_date": due, "amount": "1e30"},
            content_type=JSON,
        ).status_code
        == 400
    )


def test_finance_permissions_and_isolation() -> None:
    org, manager, finance, contract = _setup()
    manager_client = factories.client_for(manager, org)
    assert manager_client.get(f"/api/v1/contracts/{contract.id}/finance").status_code == 200
    write = manager_client.post(
        f"/api/v1/contracts/{contract.id}/finance/installments",
        {"total": "10.00", "parts": 1, "first_due": "2027-01-01", "frequency": "once"},
        content_type=JSON,
    )
    assert write.status_code == 403
    reviewer = factories.client_for(factories.member(org, [Role.REVIEWER]), org)
    assert reviewer.get(f"/api/v1/contracts/{contract.id}/finance").status_code == 403
    other_org, _ = factories.organization()
    outsider = factories.client_for(factories.member(other_org, [Role.FINANCE]), other_org)
    assert outsider.get(f"/api/v1/contracts/{contract.id}/finance").status_code == 404
    finance_client = factories.client_for(finance, org)
    too_many = finance_client.post(
        f"/api/v1/contracts/{contract.id}/finance/installments",
        {"total": "10.00", "parts": 3, "first_due": "2027-01-01", "frequency": "once"},
        content_type=JSON,
    )
    assert too_many.status_code == 400
