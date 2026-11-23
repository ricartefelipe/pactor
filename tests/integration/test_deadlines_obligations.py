"""Prazos, alertas idempotentes, renovação automática e obrigações recorrentes."""

from __future__ import annotations

import calendar
import uuid
import zoneinfo
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest
from django.core import mail
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from pactor.access.policies import Role
from pactor.contracts.models import Contract
from pactor.core.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    InvalidTransitionError,
)
from pactor.core.models import OutboxEvent
from pactor.core.outbox import EventEnvelope
from pactor.core.tenancy import tenant_context
from pactor.deadlines import services as deadlines
from pactor.deadlines.calendar import DayCount, Frequency, Roll, add_months
from pactor.deadlines.models import (
    Alert,
    DeadlineAnchor,
    DeadlineKind,
    RenewalMode,
    RenewalRecord,
)
from pactor.deadlines.tasks import daily_cycle, dispatch_alerts
from pactor.files import services as files
from pactor.files.models import FilePurpose, FileStatus, StoredFile
from pactor.identity.models import Organization, User
from pactor.notifications import tasks as notifications
from pactor.obligations import services as obligations
from pactor.obligations.models import ObligationCategory, ObligationOccurrence, ResponsibleParty
from tests import factories, file_samples

pytestmark = pytest.mark.django_db(transaction=True)
JSON = "application/json"


def _active_contract(
    *, end: date | None, start: date | None = None, tz: str = "America/Sao_Paulo"
) -> tuple[Organization, User, Contract]:
    """Contrato assinado e vigente, montado diretamente para focar nos prazos."""
    org, _ = factories.organization()
    Organization.objects.filter(id=org.id).update(time_zone=tz)
    org.refresh_from_db()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, manager)
    with tenant_context(org.id):
        Contract.objects.filter(id=contract.id).update(
            drafting_status="signed",
            lifecycle_status="active",
            effective_start=start or (end - timedelta(days=365) if end else date(2027, 1, 1)),
            effective_end=end,
            signed_on=date(2027, 1, 1),
        )
        contract.refresh_from_db()
    return org, manager, contract


def test_deadline_uses_business_days_holidays_and_roll() -> None:
    org, manager, contract = _active_contract(end=date(2027, 11, 22))
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        calendar = deadlines.create_holiday_calendar(
            principal,
            name="Calendário demonstrativo (revisar)",
            is_default=True,
            holidays=[
                (date(2027, 11, 15), "Feriado demonstrativo"),
                (date(2027, 11, 20), "Feriado demonstrativo"),
            ],
        )
        # 5 dias úteis antes de 22/11 (segunda): 19, 18, 17, 16 e 12.
        # O dia 15 é feriado e 13-14 são fim de semana.
        notice = deadlines.create_deadline(
            principal,
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.TERMINATION_NOTICE,
                "Aviso de não renovação",
                DeadlineAnchor.EFFECTIVE_END,
                -5,
                DayCount.BUSINESS,
                Roll.NONE,
                notify_days_before=10,
            ),
        )
        assert notice.due_date == date(2027, 11, 12)
        rolled = deadlines.create_deadline(
            principal,
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.REMINDER,
                "Revisar no dia 20",
                DeadlineAnchor.FIXED,
                0,
                DayCount.CALENDAR,
                Roll.FOLLOWING,
                fixed_date=date(2027, 11, 20),
                holiday_calendar_id=calendar.id,
            ),
        )
        assert rolled.due_date == date(2027, 11, 22)
        alerts = Alert.objects.filter(target_id=notice.id).order_by("fire_on")
        assert [(a.kind, a.fire_on) for a in alerts] == [
            ("reminder", date(2027, 11, 2)),
            ("due", date(2027, 11, 12)),
        ]


def test_alert_fire_time_uses_organization_timezone() -> None:
    org, manager, contract = _active_contract(end=date(2028, 2, 29), tz="Asia/Tokyo")
    with tenant_context(org.id):
        deadline = deadlines.create_deadline(
            factories.principal(org, manager),
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.EXPIRY, "Vencimento em ano bissexto", DeadlineAnchor.EFFECTIVE_END, 0
            ),
        )
        alert = Alert.objects.get(target_id=deadline.id, kind="due")
    assert deadline.due_date == date(2028, 2, 29)
    assert alert.fire_at == datetime.combine(
        date(2028, 2, 29), time(8, 0), tzinfo=zoneinfo.ZoneInfo("Asia/Tokyo")
    )
    assert (
        alert.fire_at.astimezone(zoneinfo.ZoneInfo("UTC")).hour == 23
    )  # 08:00 em Tóquio = 23:00 UTC do dia anterior


def test_alerts_are_idempotent_and_recovered_after_downtime() -> None:
    org, manager, contract = _active_contract(end=timezone.localdate() - timedelta(days=1))
    with tenant_context(org.id):
        deadline = deadlines.create_deadline(
            factories.principal(org, manager),
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.EXPIRY,
                "Venceu durante indisponibilidade",
                DeadlineAnchor.EFFECTIVE_END,
                0,
                notify_days_before=3,
            ),
        )
        # Reagendar não duplica alertas (restrição única por alvo, tipo e data).
        deadlines.recompute_for_contract(contract)
        assert Alert.objects.filter(target_id=deadline.id).count() == 2
    # Agendador ficou fora do ar: os dois alertas atrasados disparam uma vez só.
    assert deadlines.dispatch_due_alerts() == 2
    assert deadlines.dispatch_due_alerts() == 0
    assert dispatch_alerts.apply().result == 0
    with tenant_context(org.id):
        assert set(
            Alert.objects.filter(target_id=deadline.id).values_list("status", flat=True)
        ) == {"sent"}
        assert OutboxEvent.objects.filter(topic="alerts.fired").count() == 2


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_fired_alert_is_emailed_once_to_active_recipients() -> None:
    org, manager, contract = _active_contract(end=timezone.localdate() - timedelta(days=1))
    with tenant_context(org.id):
        deadlines.create_deadline(
            factories.principal(org, manager),
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.EXPIRY, "Vencimento", DeadlineAnchor.EFFECTIVE_END, 0
            ),
        )
    deadlines.dispatch_due_alerts()
    event = OutboxEvent.objects.get(topic="alerts.fired")
    message = EventEnvelope.from_model(event).to_message()
    notifications.alert_email.apply(kwargs={"message": message})
    notifications.alert_email.apply(kwargs={"message": message})
    assert [m.to for m in mail.outbox] == [[manager.email]]
    assert "Vencimento" in mail.outbox[0].subject


def test_completed_deadline_cancels_pending_alerts() -> None:
    org, manager, contract = _active_contract(end=timezone.localdate() + timedelta(days=60))
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        deadline = deadlines.create_deadline(
            principal,
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.ADJUSTMENT,
                "Reajuste anual",
                DeadlineAnchor.EFFECTIVE_END,
                -30,
                notify_days_before=5,
            ),
        )
        deadlines.complete_deadline(principal, deadline.id)
        assert set(
            Alert.objects.filter(target_id=deadline.id).values_list("status", flat=True)
        ) == {"cancelled"}
        with pytest.raises(ConflictError):
            deadlines.complete_deadline(principal, deadline.id)


def test_automatic_renewal_requires_authorization_and_runs_once() -> None:
    today = timezone.localdate()
    org, manager, contract = _active_contract(end=today - timedelta(days=1))
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        notice = deadlines.create_deadline(
            principal,
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.RENEWAL_DECISION,
                "Decidir renovação",
                DeadlineAnchor.EFFECTIVE_END,
                -30,
            ),
        )
        policy = deadlines.configure_renewal(
            principal, contract.id, mode=RenewalMode.AUTOMATIC, term_months=12, max_renewals=2
        )
        assert policy.authorized_by_id == manager.id
        assert policy.authorized_at is not None
    assert _cycle()["renewals"] == 1
    assert _cycle()["renewals"] == 0
    with tenant_context(org.id):
        renewed = Contract.objects.get(id=contract.id)
        record = RenewalRecord.objects.get(contract=contract)
        notice.refresh_from_db()
    assert (
        renewed.lifecycle_status == "active"
    )  # não expirou: renovação ocorre antes do encerramento
    assert renewed.effective_end == add_year(today - timedelta(days=1))
    assert (record.previous_end, record.new_end) == (
        today - timedelta(days=1),
        renewed.effective_end,
    )
    assert record.rule["authorized_by"] == str(manager.id)
    assert notice.due_date == renewed.effective_end - timedelta(days=30)
    with (
        pytest.raises(Exception, match="somente-inserção"),
        tenant_context(org.id),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE deadlines_renewalrecord SET new_end = '2100-01-01' WHERE id = %s", [record.id]
        )


def test_monthly_renewals_keep_the_base_day_instead_of_drifting() -> None:
    """31/01 + 1 mês = 28/02, mas o termo seguinte volta a 31/03 (base 31/01 + 2 meses)."""
    today = timezone.localdate()
    org, manager, contract = _active_contract(end=date(today.year - 1, 1, 31))
    with tenant_context(org.id):
        deadlines.configure_renewal(
            factories.principal(org, manager),
            contract.id,
            mode=RenewalMode.AUTOMATIC,
            term_months=1,
            max_renewals=None,
        )
    assert _cycle()["renewals"] >= 12
    with tenant_context(org.id):
        records = list(RenewalRecord.objects.filter(contract=contract).order_by("sequence"))
    ends = [record.new_end for record in records]
    year = today.year - 1
    assert ends[:3] == [
        date(year, 2, calendar.monthrange(year, 2)[1]),
        date(year, 3, 31),
        date(year, 4, 30),
    ]
    assert all(end == add_months(date(today.year - 1, 1, 31), i + 1) for i, end in enumerate(ends))
    assert all(record.rule["base_end"] == f"{today.year - 1}-01-31" for record in records)
    assert ends[-1] >= today > ends[-2]


def add_year(day: date) -> date:
    return add_months(day, 12)


def test_registered_non_renewal_prevents_renewal_and_contract_ends() -> None:
    today = timezone.localdate()
    org, manager, contract = _active_contract(end=today - timedelta(days=1))
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        deadlines.configure_renewal(
            principal, contract.id, mode=RenewalMode.AUTOMATIC, term_months=6, max_renewals=None
        )
        with pytest.raises(DomainValidationError):
            deadlines.register_non_renewal(principal, contract.id, reason=" ")
        deadlines.register_non_renewal(
            principal, contract.id, reason="Contraparte notificada da não renovação"
        )
    assert _cycle()["renewals"] == 0
    with tenant_context(org.id):
        assert Contract.objects.get(id=contract.id).lifecycle_status == "ended"


def test_manual_mode_never_renews_automatically_and_validation() -> None:
    today = timezone.localdate()
    org, manager, contract = _active_contract(end=today - timedelta(days=1))
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        manual = deadlines.configure_renewal(
            principal, contract.id, mode=RenewalMode.MANUAL, term_months=12, max_renewals=None
        )
        assert manual.authorized_by_id is None
        with pytest.raises(DomainValidationError):
            deadlines.configure_renewal(
                principal, contract.id, mode=RenewalMode.MANUAL, term_months=0, max_renewals=None
            )
    assert deadlines.process_automatic_renewals(org.id, today=today) == 0
    other_org, other_manager, other = _active_contract(end=None)
    with tenant_context(other_org.id), pytest.raises(InvalidTransitionError):
        deadlines.register_non_renewal(
            factories.principal(other_org, other_manager), other.id, reason="x"
        )


def _obligation(
    manager: User,
    *,
    frequency: Frequency = Frequency.MONTHLY,
    first_due: date = date(2028, 1, 31),
    count: int | None = 4,
) -> obligations.ObligationInput:
    return obligations.ObligationInput(
        title="Relatório mensal de execução (exemplo fictício)",
        category=ObligationCategory.REPORT,
        responsible_id=manager.id,
        responsible_party=ResponsibleParty.ORGANIZATION,
        frequency=frequency,
        first_due=first_due,
        count=count,
    )


def _cycle() -> dict[str, int]:
    result = daily_cycle.apply().result
    assert isinstance(result, dict)
    return result


def test_recurring_occurrences_are_stable_idempotent_and_leap_year_aware() -> None:
    org, manager, contract = _active_contract(end=None)
    with tenant_context(org.id):
        obligation = obligations.create_obligation(
            factories.principal(org, manager), contract.id, _obligation(manager)
        )
        dates = list(
            ObligationOccurrence.objects.filter(obligation=obligation)
            .order_by("sequence")
            .values_list("due_date", flat=True)
        )
        assert dates == [date(2028, 1, 31), date(2028, 2, 29), date(2028, 3, 31), date(2028, 4, 30)]
        alerts_before = Alert.objects.filter(target_type="occurrence").count()
    assert obligations.extend_horizon(org.id) == 0
    assert obligations.extend_horizon(org.id) == 0
    with tenant_context(org.id):
        assert ObligationOccurrence.objects.filter(obligation=obligation).count() == 4
        assert Alert.objects.filter(target_type="occurrence").count() == alerts_before


def test_schedule_change_preserves_completed_and_cancels_future_pending() -> None:
    org, manager, contract = _active_contract(end=None)
    today = timezone.localdate()
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        obligation = obligations.create_obligation(
            principal,
            contract.id,
            _obligation(manager, first_due=today - timedelta(days=10), count=3),
        )
        first = ObligationOccurrence.objects.get(obligation=obligation, sequence=1)
        obligations.submit_completion(principal, first.id, evidence_id=None, note="Entregue")
        obligations.change_schedule(
            principal,
            obligation.id,
            expected_version=1,
            frequency=Frequency.QUARTERLY,
            first_due=today + timedelta(days=5),
            count=2,
        )
        with pytest.raises(ConflictError):
            obligations.change_schedule(
                principal,
                obligation.id,
                expected_version=1,
                frequency=Frequency.ONCE,
                first_due=today,
            )
        statuses = {
            (o.schedule_version, o.sequence): o.status
            for o in ObligationOccurrence.objects.filter(obligation=obligation)
        }
    assert statuses[(1, 1)] == "completed"
    assert statuses[(1, 2)] == "cancelled"
    assert statuses[(1, 3)] == "cancelled"
    assert statuses[(2, 1)] == "pending"
    assert statuses[(2, 2)] == "pending"


def _released_evidence(org: Organization, person: User) -> StoredFile:
    with tenant_context(org.id):
        stored = files.receive(
            factories.principal(org, person),
            name="evidencia.pdf",
            content=file_samples.pdf(),
            purpose=FilePurpose.OBLIGATION_EVIDENCE,
        )
        StoredFile.objects.filter(id=stored.id).update(
            status=FileStatus.RELEASED, released_at=timezone.now()
        )
        stored.refresh_from_db()
    return stored


def test_evidence_dependency_and_completion_approval() -> None:
    org, manager, contract = _active_contract(end=None)
    reviewer = factories.member(org, [Role.CONTRACT_MANAGER])
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        base = obligations.create_obligation(
            principal, contract.id, _obligation(manager, frequency=Frequency.ONCE, count=None)
        )
        dependent = obligations.create_obligation(
            principal,
            contract.id,
            replace(
                _obligation(manager, frequency=Frequency.ONCE, count=None),
                title="Certificado",
                category=ObligationCategory.CERTIFICATE,
                requires_evidence=True,
                requires_approval=True,
                depends_on_id=base.id,
            ),
        )
        occurrence = ObligationOccurrence.objects.get(obligation=dependent)
        with pytest.raises(DomainValidationError, match="evidência"):
            obligations.submit_completion(principal, occurrence.id, evidence_id=None)
        quarantined = files.receive(
            principal,
            name="q.pdf",
            content=file_samples.pdf(),
            purpose=FilePurpose.OBLIGATION_EVIDENCE,
        )
        with pytest.raises(ConflictError, match="liberada"):
            obligations.submit_completion(principal, occurrence.id, evidence_id=quarantined.id)
    evidence = _released_evidence(org, manager)
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        with pytest.raises(InvalidTransitionError, match="depende"):
            obligations.submit_completion(principal, occurrence.id, evidence_id=evidence.id)
        obligations.submit_completion(
            principal, ObligationOccurrence.objects.get(obligation=base).id, evidence_id=None
        )
        submitted = obligations.submit_completion(
            principal, occurrence.id, evidence_id=evidence.id, note="Certificado anexado"
        )
        assert submitted.status == "submitted"
        with pytest.raises(AccessDeniedError, match="submeteu"):
            obligations.review_completion(principal, occurrence.id, approve=True)
        reviewer_principal = factories.principal(org, reviewer)
        with pytest.raises(DomainValidationError):
            obligations.review_completion(reviewer_principal, occurrence.id, approve=False)
        completed = obligations.review_completion(reviewer_principal, occurrence.id, approve=True)
        assert completed.status == "completed"
        assert set(
            Alert.objects.filter(target_id=occurrence.id).values_list("status", flat=True)
        ) == {"cancelled"}
    # Evidência fica acessível a quem enxerga o contrato.
    viewer = factories.client_for(factories.member(org, [Role.REVIEWER]), org)
    assert viewer.get(f"/api/v1/files/{evidence.id}/content").status_code == 200
    outsider_org, outsider = factories.organization()
    assert (
        factories.client_for(outsider, outsider_org).get(f"/api/v1/files/{evidence.id}").status_code
        == 404
    )


def test_only_responsible_or_manager_completes() -> None:
    org, manager, contract = _active_contract(end=None)
    finance = factories.member(org, [Role.FINANCE])
    with tenant_context(org.id):
        obligation = obligations.create_obligation(
            factories.principal(org, manager),
            contract.id,
            _obligation(manager, frequency=Frequency.ONCE, count=None),
        )
        occurrence = ObligationOccurrence.objects.get(obligation=obligation)
        with pytest.raises(AccessDeniedError, match="responsável"):
            obligations.submit_completion(
                factories.principal(org, finance), occurrence.id, evidence_id=None
            )


def test_obligation_and_calendar_api() -> None:
    org, manager, contract = _active_contract(end=timezone.localdate() + timedelta(days=20))
    client = factories.client_for(manager, org)
    created = client.post(
        f"/api/v1/contracts/{contract.id}/obligations",
        {
            "title": "Revisão trimestral (exemplo fictício)",
            "category": "review",
            "responsible_id": str(manager.id),
            "responsible_party": "organization",
            "frequency": "quarterly",
            "first_due": (timezone.localdate() - timedelta(days=2)).isoformat(),
            "count": 2,
        },
        content_type=JSON,
    )
    assert created.status_code == 201, created.json()
    assert len(created.json()["occurrences"]) == 2
    deadline = client.post(
        f"/api/v1/contracts/{contract.id}/deadlines",
        {"kind": "expiry", "title": "Vencimento", "anchor": "effective_end", "offset_days": 0},
        content_type=JSON,
    )
    assert deadline.status_code == 201, deadline.json()
    calendar = client.get("/api/v1/calendar").json()
    kinds = [(e["type"], e["overdue"]) for e in calendar["events"]]
    assert ("obligation", True) in kinds
    assert ("deadline", False) in kinds
    assert calendar["events"] == sorted(
        calendar["events"], key=lambda e: (e["date"], e["type"], e["id"])
    )
    too_long = client.get("/api/v1/calendar?start=2027-01-01&end=2029-01-01")
    assert too_long.status_code == 400
    occurrence_id = created.json()["occurrences"][0]["id"]
    completed = client.post(
        f"/api/v1/occurrences/{occurrence_id}/complete", {"note": "Feito"}, content_type=JSON
    )
    assert completed.json()["status"] == "completed"
    outsider_org, outsider_manager, _ = _active_contract(end=None)
    outsider = factories.client_for(outsider_manager, outsider_org)
    assert outsider.get("/api/v1/calendar").json()["events"] == []
    assert (
        outsider.post(
            f"/api/v1/occurrences/{occurrence_id}/complete", {}, content_type=JSON
        ).status_code
        == 404
    )
    assert client.get("/api/v1/alerts").status_code == 200
    holidays = client.post(
        "/api/v1/holiday-calendars",
        {
            "name": "Feriados demonstrativos",
            "is_default": True,
            "holidays": [{"day": "2027-12-25", "name": "Natal"}],
        },
        content_type=JSON,
    )
    assert holidays.status_code == 201
    assert client.get("/api/v1/holiday-calendars").json()[0]["holidays"] == [
        {"day": "2027-12-25", "name": "Natal"}
    ]
    renewal = client.put(
        f"/api/v1/contracts/{contract.id}/renewal",
        {"mode": "automatic", "term_months": 12},
        content_type=JSON,
    )
    assert renewal.status_code == 200, renewal.json()
    assert (
        client.get(f"/api/v1/contracts/{contract.id}/renewal").json()["policy"]["mode"]
        == "automatic"
    )
    assert uuid.UUID(renewal.json()["authorized_by_id"]) == manager.id


def test_deadline_validations_and_unscheduled_anchor() -> None:
    org, manager, contract = _active_contract(end=None)
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        cases = [
            (
                deadlines.DeadlineInput(DeadlineKind.EXPIRY, " ", DeadlineAnchor.EFFECTIVE_END, 0),
                "título",
            ),
            (
                deadlines.DeadlineInput(
                    DeadlineKind.EXPIRY, "x", DeadlineAnchor.EFFECTIVE_END, 4000
                ),
                "limite",
            ),
            (
                deadlines.DeadlineInput(DeadlineKind.EXPIRY, "x", DeadlineAnchor.FIXED, 0),
                "data fixa",
            ),
            (
                deadlines.DeadlineInput(
                    DeadlineKind.EXPIRY,
                    "x",
                    DeadlineAnchor.FIXED,
                    0,
                    fixed_date=date(2027, 1, 1),
                    holiday_calendar_id=uuid.uuid4(),
                ),
                "Calendário",
            ),
        ]
        for data, message in cases:
            with pytest.raises(DomainValidationError, match=message):
                deadlines.create_deadline(principal, contract.id, data)
        unscheduled = deadlines.create_deadline(
            principal,
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.EXPIRY,
                "Vencimento (prazo indeterminado)",
                DeadlineAnchor.EFFECTIVE_END,
                0,
            ),
        )
        assert unscheduled.status == "unscheduled"
        assert unscheduled.due_date is None
        assert not Alert.objects.filter(target_id=unscheduled.id).exists()
        started = deadlines.create_deadline(
            principal,
            contract.id,
            deadlines.DeadlineInput(
                DeadlineKind.REMINDER, "Um mês de vigência", DeadlineAnchor.EFFECTIVE_START, 30
            ),
        )
        assert contract.effective_start is not None
        assert started.due_date == contract.effective_start + timedelta(days=30)
        with pytest.raises(DomainValidationError):
            deadlines.create_holiday_calendar(principal, name=" ", holidays=[], is_default=False)
        with pytest.raises(Exception, match="Prazo não encontrado"):
            deadlines.complete_deadline(principal, uuid.uuid4())


def test_obligation_validations_and_review_rules() -> None:
    org, manager, contract = _active_contract(end=None)
    other_org, other_manager, other_contract = _active_contract(end=None)
    with tenant_context(other_org.id):
        foreign = obligations.create_obligation(
            factories.principal(other_org, other_manager),
            other_contract.id,
            _obligation(other_manager, frequency=Frequency.ONCE, count=None),
        )
    with tenant_context(org.id):
        principal = factories.principal(org, manager)
        with pytest.raises(DomainValidationError, match="título"):
            obligations.create_obligation(
                principal, contract.id, replace(_obligation(manager), title=" ")
            )
        with pytest.raises(DomainValidationError, match="Responsável"):
            obligations.create_obligation(
                principal, contract.id, replace(_obligation(manager), responsible_id=uuid.uuid4())
            )
        with pytest.raises(DomainValidationError, match="dependência"):
            obligations.create_obligation(
                principal, contract.id, replace(_obligation(manager), depends_on_id=foreign.id)
            )
        with pytest.raises(DomainValidationError):
            obligations.create_obligation(
                principal, contract.id, replace(_obligation(manager), interval=0)
            )
        simple = obligations.create_obligation(
            principal, contract.id, _obligation(manager, frequency=Frequency.ONCE, count=None)
        )
        occurrence = ObligationOccurrence.objects.get(obligation=simple)
        with pytest.raises(InvalidTransitionError):
            obligations.review_completion(principal, occurrence.id, approve=True)
        obligations.submit_completion(principal, occurrence.id, evidence_id=None)
        with pytest.raises(InvalidTransitionError, match="pendente"):
            obligations.submit_completion(principal, occurrence.id, evidence_id=None)
        with pytest.raises(Exception, match="Ocorrência não encontrada"):
            obligations.submit_completion(principal, uuid.uuid4(), evidence_id=None)
        with pytest.raises(Exception, match="Obrigação não encontrada"):
            obligations.change_schedule(
                principal,
                uuid.uuid4(),
                expected_version=1,
                frequency=Frequency.ONCE,
                first_due=date(2027, 1, 1),
            )
        foreign_file = _released_evidence(org, factories.member(org, [Role.CONTRACT_MANAGER]))
        second = obligations.create_obligation(
            principal, contract.id, _obligation(manager, frequency=Frequency.ONCE, count=None)
        )
        with pytest.raises(DomainValidationError, match="outra pessoa"):
            obligations.submit_completion(
                principal,
                ObligationOccurrence.objects.get(obligation=second).id,
                evidence_id=foreign_file.id,
            )


def test_obligation_review_and_reschedule_api() -> None:
    org, manager, contract = _active_contract(end=None)
    reviewer = factories.member(org, [Role.CONTRACT_MANAGER])
    client = factories.client_for(manager, org)
    created = client.post(
        f"/api/v1/contracts/{contract.id}/obligations",
        {
            "title": "Marco de implantação (exemplo fictício)",
            "category": "milestone",
            "responsible_id": str(manager.id),
            "responsible_party": "counterparty",
            "frequency": "once",
            "first_due": "2027-06-30",
            "requires_approval": True,
        },
        content_type=JSON,
    ).json()
    occurrence_id = created["occurrences"][0]["id"]
    assert (
        client.post(
            f"/api/v1/occurrences/{occurrence_id}/complete",
            {"note": "Marco atingido"},
            content_type=JSON,
        ).json()["status"]
        == "submitted"
    )
    reviewer_client = factories.client_for(reviewer, org)
    returned = reviewer_client.post(
        f"/api/v1/occurrences/{occurrence_id}/review",
        {"approve": False, "note": "Falta ata"},
        content_type=JSON,
    )
    assert returned.json()["status"] == "pending"
    rescheduled = client.post(
        f"/api/v1/obligations/{created['id']}/reschedule",
        {"expected_version": 1, "frequency": "monthly", "first_due": "2027-07-31", "count": 2},
        content_type=JSON,
    )
    assert rescheduled.status_code == 200, rescheduled.json()
    assert rescheduled.json()["schedule_version"] == 2
    listing = client.get(f"/api/v1/contracts/{contract.id}/obligations").json()
    assert listing[0]["id"] == created["id"]
    deadline = client.post(
        f"/api/v1/contracts/{contract.id}/deadlines",
        {
            "kind": "reminder",
            "title": "Lembrete",
            "anchor": "fixed",
            "fixed_date": "2027-05-01",
            "offset_days": 0,
        },
        content_type=JSON,
    ).json()
    assert client.post(f"/api/v1/deadlines/{deadline['id']}/complete").json()["status"] == "done"
    assert client.get(f"/api/v1/contracts/{contract.id}/deadlines").json()[0]["status"] == "done"
    assert (
        client.post(
            f"/api/v1/contracts/{contract.id}/renewal/decline", {"reason": "x"}, content_type=JSON
        ).status_code
        == 409
    )
