"""Tarefas agendadas de prazos: ciclo diário de contratos e disparo de alertas."""

from __future__ import annotations

from celery import shared_task

from pactor.core import consumers
from pactor.deadlines import services
from pactor.identity.models import Organization, OrganizationStatus
from pactor.obligations import services as obligations


@shared_task(name="pactor.deadlines.daily_cycle", ignore_result=True)
def daily_cycle() -> dict[str, int]:
    """Renovações automáticas e transições de vigência, organização por organização.

    Idempotente: rodar de hora em hora recupera execuções perdidas sem duplicar efeitos.
    """
    totals = {"renewals": 0, "lifecycle": 0, "occurrences": 0}
    with consumers.isolated_task(None):
        for organization in Organization.objects.filter(status=OrganizationStatus.ACTIVE).order_by(
            "id"
        ):
            result = services.daily_cycle(organization)
            result["occurrences"] = obligations.extend_horizon(organization.id)
            for key, value in result.items():
                totals[key] = totals.get(key, 0) + value
    return totals


@shared_task(name="pactor.deadlines.dispatch_alerts", ignore_result=True)
def dispatch_alerts() -> int:
    with consumers.isolated_task(None):
        return services.dispatch_due_alerts()
