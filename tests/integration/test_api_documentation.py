"""Respostas documentadas por serializers de esquema correspondem às reais."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from pactor.access.policies import Role
from pactor.contracts import services as contracts
from pactor.contracts.api import ComparisonSerializer, RevisionSummarySerializer
from pactor.core.api.operations import RequeuedSerializer
from pactor.core.tenancy import tenant_context
from pactor.deadlines import services as deadlines
from pactor.deadlines.api import CalendarSerializer
from pactor.deadlines.models import DeadlineAnchor, DeadlineKind
from pactor.identity.api import MeSerializer
from pactor.search.api import DashboardSerializer, SearchPageSerializer, SuggestionSerializer
from tests import factories
from tests.schema_match import assert_matches

pytestmark = pytest.mark.django_db(transaction=True)
JSON = "application/json"


def test_identity_responses_match_their_documentation() -> None:
    org, owner = factories.organization()
    other, _ = factories.organization("Outra")
    factories.member(other, [Role.REVIEWER], person=owner)
    without_active = factories.client_for(owner)
    me = without_active.get("/api/v1/me").json()
    assert me["active_organization"] is None
    assert_matches(MeSerializer(), me)
    switched = without_active.put(
        "/api/v1/session/organization", {"organization_id": str(org.id)}, content_type=JSON
    )
    assert switched.status_code == 200
    assert_matches(MeSerializer(), switched.json())
    assert switched.json()["active_organization"]["permissions"]
    created = without_active.post("/api/v1/organizations", {"name": "Nova"}, content_type=JSON)
    assert created.status_code == 201
    assert_matches(MeSerializer(), created.json())


def test_search_report_and_calendar_responses_match_their_documentation() -> None:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, manager, content="Texto inicial.")
    with tenant_context(org.id):
        principal = factories.principal_for_owner(org, manager)
        first = contracts.publish_revision(principal, contract.id, expected_draft_version=1)
        contracts.save_draft(principal, contract.id, content="Texto novo.", expected_version=1)
        second = contracts.publish_revision(principal, contract.id, expected_draft_version=2)
        deadlines.create_deadline(
            principal,
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.REMINDER,
                "Lembrete fictício",
                DeadlineAnchor.FIXED,
                0,
                fixed_date=timezone.localdate() + timedelta(days=3),
            ),
        )
    client = factories.client_for(manager, org)
    assert_matches(SearchPageSerializer(), client.get("/api/v1/search?q=texto").json())
    assert_matches(
        SuggestionSerializer(many=True),
        client.get(f"/api/v1/search/suggest?q={contract.reference}").json(),
    )
    assert_matches(DashboardSerializer(), client.get("/api/v1/reports/dashboard").json())
    calendar = client.get("/api/v1/calendar").json()
    assert calendar["events"]
    assert_matches(CalendarSerializer(), calendar)
    comparison = client.get(
        f"/api/v1/contracts/{contract.id}/compare?old={first.id}&new={second.id}"
    ).json()
    assert comparison["hunks"]
    assert_matches(ComparisonSerializer(), comparison)
    revisions = client.get(f"/api/v1/contracts/{contract.id}/revisions").json()
    assert [r["number"] for r in revisions] == [2, 1]
    assert_matches(RevisionSummarySerializer(many=True), revisions)


def test_operations_response_matches_its_documentation() -> None:
    org, owner = factories.organization()
    response = factories.client_for(owner, org).post("/api/v1/operations/dead-events/requeue")
    assert_matches(RequeuedSerializer(), response.json())


def test_contract_without_revision_is_documented_as_nullable() -> None:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, manager)
    client = factories.client_for(manager, org)
    body = client.get(f"/api/v1/contracts/{contract.id}").json()
    assert body["current_revision"] is None
    schema = client.get("/api/v1/schema", HTTP_ACCEPT="application/vnd.oai.openapi+json").json()
    assert schema["components"]["schemas"]["Contract"]["properties"]["current_revision"]["nullable"]
