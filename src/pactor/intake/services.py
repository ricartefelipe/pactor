"""Casos de uso de solicitações de contrato."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Q, QuerySet
from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Confidentiality, Permission, Principal, has_permission
from pactor.audit import services as audit
from pactor.contracts import services as contracts
from pactor.contracts.domain import ContractType, PartyRole
from pactor.contracts.models import Contract
from pactor.core.errors import (
    AccessDeniedError,
    DomainValidationError,
    InvalidTransitionError,
    NotFoundError,
)
from pactor.core.tenancy import require_tenant
from pactor.counterparties.models import Counterparty
from pactor.identity.models import Department
from pactor.intake.domain import RequestState, can_transition
from pactor.intake.models import ContractRequest, Priority, RequestEvent, RequestSequence
from pactor.plans import services as plans


@dataclass(frozen=True, slots=True)
class RequestInput:
    title: str
    contract_type: ContractType
    objective: str
    priority: Priority = Priority.NORMAL
    department_id: uuid.UUID | None = None
    counterparty_id: uuid.UUID | None = None
    estimated_value: Decimal | None = None
    desired_start: date | None = None
    desired_end: date | None = None


def requests_for(principal: Principal) -> QuerySet[ContractRequest]:
    """Gestores veem todas as solicitações; os demais, somente as próprias."""
    base = ContractRequest.objects.filter(organization_id=principal.organization_id).select_related(
        "requester", "department", "counterparty", "assignee"
    )
    if has_permission(principal, Permission.REQUESTS_MANAGE):
        return base
    if has_permission(principal, Permission.REQUESTS_CREATE):
        return base.filter(Q(requester_id=principal.actor_id) | Q(assignee_id=principal.actor_id))
    return base.none()


def get_request(
    principal: Principal, request_id: uuid.UUID, *, for_update: bool = False
) -> ContractRequest:
    queryset = requests_for(principal)
    if for_update:
        queryset = ContractRequest.objects.select_for_update().filter(id__in=queryset.values("id"))
    found = queryset.filter(id=request_id).first()
    if found is None:
        raise NotFoundError("Solicitação não encontrada.")
    return found


def _next_reference() -> str:
    today = timezone.localdate()
    sequence, _ = RequestSequence.objects.select_for_update().get_or_create(
        organization_id=require_tenant(), year=today.year
    )
    sequence.last_value += 1
    sequence.save(update_fields=["last_value"])
    return f"SOL-{today.year}-{sequence.last_value:05d}"


def _history(
    principal: Principal, request: ContractRequest, action: str, before: str, note: str = ""
) -> None:
    RequestEvent.objects.create(
        organization_id=request.organization_id,
        request=request,
        actor_id=principal.actor_id,
        action=action,
        from_status=before,
        to_status=request.status,
        note=note[:500],
    )
    audit.record(
        principal,
        action=f"request.{action}",
        resource_type="request",
        resource_id=request.id,
        changes={"from": before, "to": request.status, "note": note[:200]},
    )


def create_request(principal: Principal, data: RequestInput) -> ContractRequest:
    authorize(principal, Permission.REQUESTS_CREATE)
    plans.ensure_writable()
    organization_id = require_tenant()
    if not data.title.strip() or not data.objective.strip():
        raise DomainValidationError("Título e objetivo são obrigatórios.")
    if data.estimated_value is not None and (
        not data.estimated_value.is_finite() or data.estimated_value < 0
    ):
        raise DomainValidationError("Valor estimado inválido.")
    if data.desired_start and data.desired_end and data.desired_end < data.desired_start:
        raise DomainValidationError("Término desejado anterior ao início.")
    if (
        data.department_id
        and not Department.objects.filter(
            organization_id=organization_id, id=data.department_id
        ).exists()
    ):
        raise DomainValidationError("Departamento inexistente.")
    if (
        data.counterparty_id
        and not Counterparty.objects.filter(
            organization_id=organization_id, id=data.counterparty_id
        ).exists()
    ):
        raise DomainValidationError("Contraparte inexistente.")
    request = ContractRequest.objects.create(
        organization_id=organization_id,
        reference=_next_reference(),
        requester_id=principal.actor_id,
        department_id=data.department_id,
        contract_type=data.contract_type.value,
        counterparty_id=data.counterparty_id,
        title=data.title.strip(),
        objective=data.objective.strip(),
        estimated_value=data.estimated_value,
        desired_start=data.desired_start,
        desired_end=data.desired_end,
        priority=data.priority.value,
    )
    _history(principal, request, "submitted", "")
    return request


def _move(
    principal: Principal,
    request: ContractRequest,
    target: RequestState,
    action: str,
    note: str = "",
) -> ContractRequest:
    current = RequestState(request.status)
    if not can_transition(current, target):
        raise InvalidTransitionError(
            "Transição não permitida.", details={"from": current.value, "to": target.value}
        )
    request.status = target.value
    request.version += 1
    request.updated_at = timezone.now()
    request.save()
    _history(principal, request, action, current.value, note)
    return request


def start_review(
    principal: Principal, request_id: uuid.UUID, *, assignee_id: uuid.UUID | None = None
) -> ContractRequest:
    authorize(principal, Permission.REQUESTS_MANAGE)
    request = get_request(principal, request_id, for_update=True)
    request.assignee_id = assignee_id or principal.actor_id
    return _move(principal, request, RequestState.IN_REVIEW, "review_started")


def reject(principal: Principal, request_id: uuid.UUID, *, reason: str) -> ContractRequest:
    authorize(principal, Permission.REQUESTS_MANAGE)
    if not reason.strip():
        raise DomainValidationError("Informe o motivo da recusa.")
    return _move(
        principal,
        get_request(principal, request_id, for_update=True),
        RequestState.REJECTED,
        "rejected",
        reason,
    )


def cancel(principal: Principal, request_id: uuid.UUID, *, reason: str) -> ContractRequest:
    request = get_request(principal, request_id, for_update=True)
    if request.requester_id != principal.actor_id and not has_permission(
        principal, Permission.REQUESTS_MANAGE
    ):
        raise AccessDeniedError("Somente o solicitante ou a gestão cancelam a solicitação.")
    return _move(principal, request, RequestState.CANCELLED, "cancelled", reason)


def convert(
    principal: Principal,
    request_id: uuid.UUID,
    *,
    owner_id: uuid.UUID,
    organization_role: PartyRole,
    counterparty_role: PartyRole,
    confidentiality: Confidentiality = Confidentiality.INTERNAL,
) -> Contract:
    """Converte a solicitação em contrato, preservando o vínculo e o solicitante."""
    authorize(principal, Permission.REQUESTS_MANAGE)
    request = get_request(principal, request_id, for_update=True)
    if request.counterparty_id is None:
        raise DomainValidationError("Informe a contraparte antes de converter.")
    if not can_transition(RequestState(request.status), RequestState.CONVERTED):
        raise InvalidTransitionError("Somente solicitações em análise são convertidas.")
    contract = contracts.create_contract(
        principal,
        contracts.ContractInput(
            title=request.title,
            contract_type=ContractType(request.contract_type),
            counterparty_id=request.counterparty_id,
            organization_role=organization_role,
            counterparty_role=counterparty_role,
            owner_id=owner_id,
            department_id=request.department_id,
            requester_id=request.requester_id,
            confidentiality=confidentiality,
            total_value=request.estimated_value,
            planned_start=request.desired_start,
            planned_end=request.desired_end,
        ),
    )
    request.contract = contract
    _move(principal, request, RequestState.CONVERTED, "converted", contract.reference)
    return contract
