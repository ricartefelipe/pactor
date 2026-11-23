"""Casos de uso de vigência, aditivos e encerramento.

Cada transição trava o contrato, valida a ação contra a situação atual
(transições incompatíveis são rejeitadas), exige justificativa nas ações
manuais e registra histórico somente-inserção e auditoria.
"""

from __future__ import annotations

import uuid
import zoneinfo
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.audit import services as audit
from pactor.contracts import services as contract_services
from pactor.contracts.domain import ContractType, DraftingStatus
from pactor.contracts.lifecycle import (
    COUNTS_AS_ACTIVE,
    ConditionChange,
    ConditionField,
    Conditions,
    LifecycleAction,
    LifecycleError,
    LifecycleStatus,
    conditions_at,
    due_transition,
    next_status,
    status_after_signature,
)
from pactor.contracts.models import AmendmentChange, Contract, LifecycleEvent, LifecyclePolicy
from pactor.contracts.selectors import get_visible_contract, require_editable
from pactor.core.errors import DomainValidationError, InvalidTransitionError
from pactor.core.tenancy import require_tenant, tenant_context
from pactor.identity.models import Organization
from pactor.plans import services as plans

MAX_DESCRIPTION = 500


def local_today(organization: Organization, *, now: datetime | None = None) -> date:
    """Data local da organização (regras de calendário usam datas locais)."""
    moment = now or timezone.now()
    return moment.astimezone(zoneinfo.ZoneInfo(organization.time_zone)).date()


def _record(
    contract: Contract,
    action: LifecycleAction,
    before: LifecycleStatus,
    *,
    effective_date: date,
    reason: str = "",
    principal: Principal | None = None,
) -> None:
    LifecycleEvent.objects.create(
        organization_id=contract.organization_id,
        contract=contract,
        action=action.value,
        from_status=before.value,
        to_status=contract.lifecycle_status,
        effective_date=effective_date,
        reason=reason[:500],
        actor_id=principal.actor_id if principal else None,
    )
    audit.record(
        principal,
        action=f"lifecycle.{action.value}",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "from": before.value,
            "to": contract.lifecycle_status,
            "effective_date": effective_date.isoformat(),
            "reason": reason[:200],
        },
    )


def _apply(
    contract: Contract,
    action: LifecycleAction,
    target: LifecycleStatus,
    *,
    effective_date: date,
    reason: str = "",
    principal: Principal | None = None,
) -> None:
    before = LifecycleStatus(contract.lifecycle_status)
    try:
        contract.lifecycle_status = next_status(before, action, target).value
    except LifecycleError as exc:
        raise InvalidTransitionError(
            str(exc), details={"from": before.value, "action": action.value}
        ) from exc
    contract.version += 1
    contract.updated_at = timezone.now()
    # A situação de elaboração entra junto: ao registrar a assinatura, as duas
    # situações mudam na mesma gravação (o banco exige consistência entre elas).
    contract.save(
        update_fields=[
            "lifecycle_status",
            "drafting_status",
            "version",
            "updated_at",
            "effective_start",
            "effective_end",
            "signed_on",
        ]
    )
    _record(
        contract, action, before, effective_date=effective_date, reason=reason, principal=principal
    )


def active_contract_count() -> int:
    """Contratos que ocupam o limite do plano.

    Vigentes, aguardando início, suspensos ou em assinatura.
    """
    return (
        Contract.objects.filter(organization_id=require_tenant())
        .filter(lifecycle_status__in=[s.value for s in COUNTS_AS_ACTIVE])
        .count()
        + Contract.objects.filter(
            organization_id=require_tenant(), drafting_status=DraftingStatus.SIGNING
        ).count()
    )


def ensure_capacity_for_new_active() -> None:
    plans.ensure_capacity(active_contracts=active_contract_count() + 1)


def register_signature(contract: Contract, *, signed_on: date) -> None:
    """Chamado com o contrato travado quando o envelope vigente é concluído."""
    contract.signed_on = signed_on
    contract.effective_start = contract.planned_start or signed_on
    contract.effective_end = contract.planned_end
    target = status_after_signature(contract.effective_start, signed_on)
    _apply(contract, LifecycleAction.REGISTER_SIGNATURE, target, effective_date=signed_on)


def advance_due(organization_id: uuid.UUID, *, today: date) -> int:
    """Aplica transições automáticas devidas (início e término). Idempotente."""
    applied = 0
    with tenant_context(organization_id):
        candidates = list(
            Contract.objects.select_for_update()
            .filter(
                lifecycle_status__in=[
                    LifecycleStatus.AWAITING_START,
                    LifecycleStatus.ACTIVE,
                    LifecycleStatus.SUSPENDED,
                ]
            )
            .order_by("id")
        )
        for contract in candidates:
            while (
                due := due_transition(
                    LifecycleStatus(contract.lifecycle_status),
                    contract.effective_start,
                    contract.effective_end,
                    today,
                )
            ) is not None:
                action, target = due
                effective = (
                    contract.effective_start
                    if action is LifecycleAction.START
                    else contract.effective_end
                )
                _apply(contract, action, target, effective_date=effective or today)
                applied += 1
    return applied


def _policy() -> LifecyclePolicy:
    policy, _ = LifecyclePolicy.objects.get_or_create(organization_id=require_tenant())
    return policy


def _lock_for_action(principal: Principal, contract_id: uuid.UUID, reason: str) -> Contract:
    authorize(principal, Permission.CONTRACTS_LIFECYCLE)
    plans.ensure_writable()
    if not reason.strip():
        raise DomainValidationError("Informe a justificativa.")
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    return contract


def suspend(
    principal: Principal, contract_id: uuid.UUID, *, reason: str, effective_date: date
) -> Contract:
    contract = _lock_for_action(principal, contract_id, reason)
    if not _policy().allow_suspension:
        raise InvalidTransitionError("A política da organização não permite suspender contratos.")
    _apply(
        contract,
        LifecycleAction.SUSPEND,
        LifecycleStatus.SUSPENDED,
        effective_date=effective_date,
        reason=reason,
        principal=principal,
    )
    return contract


def resume(
    principal: Principal, contract_id: uuid.UUID, *, reason: str, effective_date: date
) -> Contract:
    contract = _lock_for_action(principal, contract_id, reason)
    _apply(
        contract,
        LifecycleAction.RESUME,
        LifecycleStatus.ACTIVE,
        effective_date=effective_date,
        reason=reason,
        principal=principal,
    )
    return contract


def terminate(
    principal: Principal, contract_id: uuid.UUID, *, reason: str, effective_date: date
) -> Contract:
    """Registra a rescisão informada (o Pactor não avalia sua validade jurídica)."""
    contract = _lock_for_action(principal, contract_id, reason)
    if contract.effective_start is not None and effective_date < contract.effective_start:
        raise DomainValidationError("A rescisão não pode ser anterior ao início da vigência.")
    contract.effective_end = effective_date
    _apply(
        contract,
        LifecycleAction.TERMINATE,
        LifecycleStatus.TERMINATED,
        effective_date=effective_date,
        reason=reason,
        principal=principal,
    )
    return contract


def archive(
    principal: Principal, contract_id: uuid.UUID, *, reason: str, effective_date: date
) -> Contract:
    contract = _lock_for_action(principal, contract_id, reason)
    _apply(
        contract,
        LifecycleAction.ARCHIVE,
        LifecycleStatus.ARCHIVED,
        effective_date=effective_date,
        reason=reason,
        principal=principal,
    )
    return contract


# ---------------------------------------------------------------------------
# Aditivos
# ---------------------------------------------------------------------------
def _parse_value(field: ConditionField, raw: str) -> Decimal | date | str | None:
    if field is ConditionField.DESCRIPTION:
        if len(raw) > MAX_DESCRIPTION:
            raise DomainValidationError(f"Descrição com até {MAX_DESCRIPTION} caracteres.")
        return raw.strip()
    if raw == "":
        return None
    try:
        if field is ConditionField.TOTAL_VALUE:
            value = Decimal(raw)
            if not value.is_finite() or value < 0 or value != value.quantize(Decimal("0.01")):
                raise DomainValidationError(
                    "Valor deve ser decimal finito, não negativo, com até 2 casas."
                )
            return value
        return date.fromisoformat(raw)
    except (InvalidOperation, ValueError) as exc:
        raise DomainValidationError(
            "Valor inválido para o campo.", details={"field": field.value}
        ) from exc


def add_amendment_change(
    principal: Principal,
    amendment_id: uuid.UUID,
    *,
    field: ConditionField,
    value: str,
    effective_from: date,
) -> AmendmentChange:
    """Registra no aditivo uma alteração de condição do contrato principal.

    A alteração faz parte do conteúdo submetido do aditivo: se o aditivo já
    estava em aprovação ou aprovado, a aprovação deixa de valer.
    """
    authorize(principal, Permission.CONTRACTS_EDIT)
    plans.ensure_writable()
    amendment = get_visible_contract(principal, amendment_id, for_update=True)
    require_editable(principal, amendment)
    if amendment.contract_type != ContractType.AMENDMENT or amendment.parent_id is None:
        raise DomainValidationError("Alterações de condição só podem ser registradas em aditivos.")
    if amendment.drafting_status not in {
        DraftingStatus.DRAFT,
        DraftingStatus.IN_APPROVAL,
        DraftingStatus.APPROVED,
    }:
        raise InvalidTransitionError("Aditivo assinado ou cancelado não recebe novas alterações.")
    parsed = _parse_value(field, value)
    change = AmendmentChange.objects.create(
        organization_id=amendment.organization_id,
        amendment=amendment,
        parent_id=amendment.parent_id,
        field=field.value,
        value="" if parsed is None else str(parsed),
        effective_from=effective_from,
        created_by_id=principal.actor_id,
    )
    contract_services.invalidate_for_content_change(amendment, reason="amendment_changed")
    audit.record(
        principal,
        action="amendment.change_added",
        resource_type="contract",
        resource_id=amendment.id,
        changes={
            "field": field.value,
            "value": change.value,
            "effective_from": effective_from.isoformat(),
        },
    )
    return change


def amendment_changes(contract: Contract) -> Sequence[AmendmentChange]:
    return list(
        AmendmentChange.objects.filter(
            organization_id=contract.organization_id, amendment=contract
        ).order_by("created_at", "id")
    )


def current_conditions(principal: Principal, contract_id: uuid.UUID, *, on: date) -> Conditions:
    """Condições vigentes em ``on`` a partir do original e dos aditivos assinados."""
    contract = get_visible_contract(principal, contract_id)
    original = Conditions(
        total_value=contract.total_value,
        effective_end=contract.effective_end or contract.planned_end,
        description="",
    )
    changes = [
        ConditionChange(
            sequence=index,
            field=ConditionField(change.field),
            value=_parse_value(ConditionField(change.field), change.value),
            effective_from=change.effective_from,
        )
        for index, change in enumerate(
            AmendmentChange.objects.filter(
                organization_id=contract.organization_id,
                parent=contract,
                amendment__drafting_status=DraftingStatus.SIGNED,
            ).order_by("amendment__signed_on", "created_at", "id")
        )
    ]
    return conditions_at(original, changes, on)
