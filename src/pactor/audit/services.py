"""Gravação de eventos de auditoria na organização da transação corrente."""

from __future__ import annotations

from collections.abc import Mapping

from pactor.access.policies import Principal
from pactor.audit.models import AuditEvent
from pactor.core.errors import AccessDeniedError
from pactor.core.observability import current_log_context, redact
from pactor.core.tenancy import require_tenant

_MAX_CHANGE_VALUE_LENGTH = 500


def _truncate(value: object) -> object:
    if isinstance(value, str) and len(value) > _MAX_CHANGE_VALUE_LENGTH:
        return value[:_MAX_CHANGE_VALUE_LENGTH] + "…"
    if isinstance(value, Mapping):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_truncate(v) for v in value]
    return value


def record(
    principal: Principal | None,
    *,
    action: str,
    resource_type: str,
    resource_id: object,
    changes: Mapping[str, object] | None = None,
    actor_label: str | None = None,
) -> AuditEvent:
    """Registra a operação. ``principal=None`` identifica ações do sistema."""
    organization_id = require_tenant()
    if principal is not None and principal.organization_id != organization_id:
        raise AccessDeniedError("Principal não pertence à organização da transação.")
    sanitized = _truncate(redact(dict(changes or {})))
    return AuditEvent.objects.create(
        organization_id=organization_id,
        actor_id=principal.actor_id if principal else None,
        actor_label=actor_label or (str(principal.actor_id) if principal else "sistema"),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        changes=sanitized if isinstance(sanitized, dict) else {},
        request_id=current_log_context().get("request_id", ""),
    )
