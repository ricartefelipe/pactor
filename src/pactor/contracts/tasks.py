"""Tarefas agendadas de contratos."""

from __future__ import annotations

from celery import shared_task

from pactor.contracts import lifecycle_services
from pactor.core import consumers
from pactor.identity.models import Organization, OrganizationStatus


@shared_task(name="pactor.contracts.advance_lifecycles", ignore_result=True)
def advance_lifecycles() -> int:
    """Inicia e encerra vigências devidas, organização por organização, pela data local."""
    applied = 0
    with consumers.isolated_task(None):
        for organization in Organization.objects.filter(status=OrganizationStatus.ACTIVE).order_by(
            "id"
        ):
            applied += lifecycle_services.advance_due(
                organization.id, today=lifecycle_services.local_today(organization)
            )
    return applied
