"""Seed de demonstração: idempotente, isolado por organização e recusado em produção."""

from __future__ import annotations

import io

import pytest
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import Client, override_settings

from pactor import demo
from pactor.approvals.models import WorkflowDefinition
from pactor.contracts.models import Contract
from pactor.core.tenancy import tenant_context
from pactor.counterparties.models import Counterparty
from pactor.deadlines.models import HolidayCalendar
from pactor.drafting.models import ContractTemplate
from pactor.identity.models import DepartmentMembership, Membership, Organization, User
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _trial_plan() -> None:
    # Testes transacionais limpam as tabelas, inclusive os planos da migration de dados.
    factories.plan(settings.PACTOR_TRIAL_PLAN)


def _counts(organization: Organization) -> dict[str, int]:
    with tenant_context(organization.id):
        return {
            "members": Membership.objects.count(),
            "department_links": DepartmentMembership.objects.count(),
            "calendars": HolidayCalendar.objects.count(),
            "workflows": WorkflowDefinition.objects.count(),
            "templates": ContractTemplate.objects.count(),
            "counterparties": Counterparty.objects.count(),
            "contracts": Contract.objects.count(),
        }


def test_seed_creates_two_isolated_organizations_and_is_idempotent() -> None:
    out = io.StringIO()
    call_command("seed_demo", stdout=out)
    assert "Organização A (fictícia) (demo-a) pronta." in out.getvalue()
    first = {o.slug: _counts(o) for o in Organization.objects.all()}
    assert set(first) == {"demo-a", "demo-b"}
    expected = {
        "members": len(demo.PEOPLE),
        "department_links": len(demo.LEGAL_MEMBERS),
        "calendars": 1,
        "workflows": 1,
        "templates": 1,
        "counterparties": 1,
        "contracts": 1,
    }
    assert first == {"demo-a": expected, "demo-b": expected}
    call_command("seed_demo", stdout=io.StringIO())
    assert {o.slug: _counts(o) for o in Organization.objects.all()} == first
    assert User.objects.count() == 2 * len(demo.PEOPLE)
    org_a = Organization.objects.get(slug="demo-a")
    with tenant_context(org_a.id):
        emails = set(Membership.objects.values_list("user__email", flat=True))
    assert all(e.endswith(".a@exemplo.test") for e in emails)


def test_privileged_demo_users_have_totp_and_can_sign_in() -> None:
    demo.seed()
    with_totp = set(
        Authenticator.objects.filter(type=Authenticator.Type.TOTP).values_list(
            "user__email", flat=True
        )
    )
    assert with_totp == {
        f"{p.key}.{s}@exemplo.test" for p in demo.PEOPLE if p.mfa for s in ("a", "b")
    }
    response = Client().post(
        "/_allauth/browser/v1/auth/login",
        {"email": "gestora.a@exemplo.test", "password": demo.DEMO_PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 200, response.json()


@override_settings(PACTOR_ENVIRONMENT="production")
def test_seed_is_refused_in_production() -> None:
    with pytest.raises(CommandError, match="produção"):
        call_command("seed_demo", stdout=io.StringIO())
    assert not Organization.objects.exists()
    assert not User.objects.exists()
