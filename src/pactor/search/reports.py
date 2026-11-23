"""Relatórios agregados e exportação, sempre restritos aos contratos visíveis."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from django.db.models import Count

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal, has_permission
from pactor.approvals.services import inbox
from pactor.contracts import lifecycle_services as lifecycle
from pactor.contracts.selectors import visible_contracts
from pactor.deadlines.models import ContractDeadline, DeadlineStatus
from pactor.identity.models import Organization
from pactor.obligations.models import ObligationOccurrence, OccurrenceStatus

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
UPCOMING_DAYS = 30
EXPORT_LIMIT = 5000


def _today(principal: Principal) -> date:
    return lifecycle.local_today(Organization.objects.get(id=principal.organization_id))


def dashboard(principal: Principal) -> dict[str, object]:
    today = _today(principal)
    visible = visible_contracts(principal)
    ids = visible.values("id")
    by_status = {
        row["drafting_status"]: row["n"]
        for row in visible.values("drafting_status").annotate(n=Count("id")).order_by()
    }
    by_lifecycle = {
        row["lifecycle_status"]: row["n"]
        for row in visible.values("lifecycle_status").annotate(n=Count("id")).order_by()
    }
    upcoming = ContractDeadline.objects.filter(
        contract__in=ids,
        status=DeadlineStatus.PENDING,
        due_date__range=(today, today + timedelta(days=UPCOMING_DAYS)),
    ).count()
    overdue = ObligationOccurrence.objects.filter(
        obligation__contract__in=ids, status=OccurrenceStatus.PENDING, due_date__lt=today
    ).count()
    pending_votes = (
        inbox(principal).count() if has_permission(principal, Permission.APPROVALS_VOTE) else 0
    )
    return {
        "today": today.isoformat(),
        "contracts_total": sum(by_status.values()),
        "by_drafting_status": by_status,
        "by_lifecycle_status": by_lifecycle,
        "deadlines_next_30_days": upcoming,
        "overdue_obligations": overdue,
        "pending_approvals": pending_votes,
    }


def _cell(value: object) -> str:
    """Neutraliza fórmulas em planilhas (injeção CSV)."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def export_contracts_csv(principal: Principal) -> str:
    authorize(principal, Permission.REPORTS_READ)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Referência",
            "Título",
            "Tipo",
            "Contraparte",
            "Elaboração",
            "Vigência",
            "Início",
            "Término",
            "Valor (BRL)",
        ]
    )
    for contract in visible_contracts(principal).order_by("reference", "id")[:EXPORT_LIMIT]:
        writer.writerow(
            [
                _cell(contract.reference),
                _cell(contract.title),
                _cell(contract.get_contract_type_display()),
                _cell(contract.counterparty.name),
                _cell(contract.get_drafting_status_display()),
                _cell(contract.get_lifecycle_status_display()),
                _cell(contract.effective_start or contract.planned_start),
                _cell(contract.effective_end or contract.planned_end),
                _cell(contract.total_value),
            ]
        )
    return buffer.getvalue()
