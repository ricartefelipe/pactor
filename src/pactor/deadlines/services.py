"""Casos de uso de prazos, renovação e alertas.

Alertas usam data local da organização para a regra (``fire_on``) e um
instante com fuso (``fire_at``, às 08:00 locais) para o disparo. A
restrição única por (alvo, tipo, data) torna o agendamento idempotente; o
disparo marca o alerta como enviado e emite evento na outbox na mesma
transação, recuperando alertas atrasados após indisponibilidade.
"""

from __future__ import annotations

import uuid
import zoneinfo
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.audit import services as audit
from pactor.contracts import lifecycle_services as lifecycle
from pactor.contracts.lifecycle import LifecycleStatus
from pactor.contracts.models import Contract
from pactor.contracts.selectors import get_visible_contract, require_editable
from pactor.core import outbox
from pactor.core.errors import (
    ConflictError,
    DomainValidationError,
    InvalidTransitionError,
    NotFoundError,
)
from pactor.core.tenancy import require_tenant, tenant_context
from pactor.deadlines.calendar import BusinessCalendar, DayCount, DeadlineRule, Roll, add_months
from pactor.deadlines.models import (
    Alert,
    AlertKind,
    AlertStatus,
    ContractDeadline,
    DeadlineAnchor,
    DeadlineKind,
    DeadlineStatus,
    Holiday,
    HolidayCalendar,
    RenewalMode,
    RenewalPolicy,
    RenewalRecord,
)
from pactor.identity.models import Organization, OrganizationStatus
from pactor.plans import services as plans
from pactor.plans.models import Module

ALERT_LOCAL_TIME = time(8, 0)
MAX_OFFSET_DAYS = 3650
MAX_RENEWAL_MONTHS = 120


# ---------------------------------------------------------------------------
# Calendário e alertas
# ---------------------------------------------------------------------------
def business_calendar(calendar_id: uuid.UUID | None) -> BusinessCalendar:
    organization_id = require_tenant()
    selected = (
        HolidayCalendar.objects.filter(organization_id=organization_id, id=calendar_id).first()
        if calendar_id
        else HolidayCalendar.objects.filter(
            organization_id=organization_id, is_default=True
        ).first()
    )
    if selected is None:
        return BusinessCalendar()
    days = Holiday.objects.filter(organization_id=organization_id, calendar=selected).values_list(
        "day", flat=True
    )
    return BusinessCalendar(holidays=frozenset(days))


def fire_at(organization: Organization, day: date) -> datetime:
    return datetime.combine(day, ALERT_LOCAL_TIME, tzinfo=zoneinfo.ZoneInfo(organization.time_zone))


@dataclass(frozen=True, slots=True)
class AlertSpec:
    kind: AlertKind
    fire_on: date
    subject: str


def schedule_alerts(
    *,
    target_type: str,
    target_id: uuid.UUID,
    contract: Contract,
    recipients: Iterable[uuid.UUID],
    specs: Iterable[AlertSpec],
) -> int:
    """Agenda alertas sem duplicar: alertas já existentes para a mesma data são mantidos.

    Devolve a quantidade efetivamente inserida (medida no banco).
    """
    organization = Organization.objects.get(id=contract.organization_id)
    existing = Alert.objects.filter(
        organization_id=contract.organization_id, target_type=target_type, target_id=target_id
    )
    before = existing.count()
    Alert.objects.bulk_create(
        [
            Alert(
                organization_id=contract.organization_id,
                target_type=target_type,
                target_id=target_id,
                contract=contract,
                kind=spec.kind.value,
                fire_on=spec.fire_on,
                fire_at=fire_at(organization, spec.fire_on),
                recipient_ids=sorted(set(recipients)),
                subject=spec.subject[:200],
            )
            for spec in specs
        ],
        ignore_conflicts=True,
    )
    return existing.count() - before


def cancel_alerts(*, target_type: str, target_id: uuid.UUID) -> int:
    return Alert.objects.filter(
        organization_id=require_tenant(),
        target_type=target_type,
        target_id=target_id,
        status=AlertStatus.PENDING,
    ).update(status=AlertStatus.CANCELLED, updated_at=timezone.now())


def dispatch_due_alerts(*, now: datetime | None = None, limit: int = 500) -> int:
    """Dispara alertas vencidos de todas as organizações (um tenant por vez). Idempotente."""
    moment = now or timezone.now()
    fired = 0
    for organization_id in (
        Organization.objects.filter(status=OrganizationStatus.ACTIVE)
        .order_by("id")
        .values_list("id", flat=True)
    ):
        with tenant_context(organization_id):
            due = list(
                Alert.objects.select_for_update(skip_locked=True)
                .filter(status=AlertStatus.PENDING, fire_at__lte=moment)
                .order_by("fire_at", "id")[:limit]
            )
            for alert in due:
                alert.status = AlertStatus.SENT.value
                alert.sent_at = moment
                alert.updated_at = moment
                alert.save(update_fields=["status", "sent_at", "updated_at"])
                outbox.emit(
                    "alerts.fired", {"alert_id": str(alert.id)}, organization_id=organization_id
                )
                fired += 1
    return fired


# ---------------------------------------------------------------------------
# Prazos
# ---------------------------------------------------------------------------
def _anchor_date(contract: Contract, deadline: ContractDeadline) -> date | None:
    match DeadlineAnchor(deadline.anchor):
        case DeadlineAnchor.EFFECTIVE_END:
            return contract.effective_end or contract.planned_end
        case DeadlineAnchor.EFFECTIVE_START:
            return contract.effective_start or contract.planned_start
        case DeadlineAnchor.FIXED:
            return deadline.fixed_date


def _compute(contract: Contract, deadline: ContractDeadline) -> date | None:
    anchor = _anchor_date(contract, deadline)
    if anchor is None:
        return None
    rule = DeadlineRule(deadline.offset_days, DayCount(deadline.day_count), Roll(deadline.roll))
    return rule.compute(anchor, business_calendar(deadline.holiday_calendar_id))


def _deadline_specs(deadline: ContractDeadline) -> list[AlertSpec]:
    if deadline.due_date is None:
        return []
    specs = [AlertSpec(AlertKind.DUE, deadline.due_date, f"Prazo hoje: {deadline.title}")]
    if deadline.notify_days_before:
        specs.append(
            AlertSpec(
                AlertKind.REMINDER,
                deadline.due_date - timedelta(days=deadline.notify_days_before),
                f"Prazo em {deadline.notify_days_before} dias: {deadline.title}",
            )
        )
    return specs


def _reschedule(contract: Contract, deadline: ContractDeadline) -> None:
    due = _compute(contract, deadline)
    if due != deadline.due_date:
        cancel_alerts(target_type="deadline", target_id=deadline.id)
    deadline.due_date = due
    deadline.status = DeadlineStatus.PENDING.value if due else DeadlineStatus.UNSCHEDULED.value
    deadline.save(update_fields=["due_date", "status"])
    schedule_alerts(
        target_type="deadline",
        target_id=deadline.id,
        contract=contract,
        recipients=[contract.owner_id],
        specs=_deadline_specs(deadline),
    )


@dataclass(frozen=True, slots=True)
class DeadlineInput:
    kind: DeadlineKind
    title: str
    anchor: DeadlineAnchor
    offset_days: int
    day_count: DayCount = DayCount.CALENDAR
    roll: Roll = Roll.NONE
    fixed_date: date | None = None
    holiday_calendar_id: uuid.UUID | None = None
    notify_days_before: int = 0


def create_deadline(
    principal: Principal, contract_id: uuid.UUID, data: DeadlineInput
) -> ContractDeadline:
    authorize(principal, Permission.OBLIGATIONS_MANAGE)
    plans.ensure_module(Module.OBLIGATIONS)
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    if not data.title.strip():
        raise DomainValidationError("Informe o título do prazo.")
    if abs(data.offset_days) > MAX_OFFSET_DAYS:
        raise DomainValidationError("Deslocamento acima do limite.")
    if data.anchor is DeadlineAnchor.FIXED and data.fixed_date is None:
        raise DomainValidationError("Prazo com data fixa exige a data.")
    if (
        data.holiday_calendar_id
        and not HolidayCalendar.objects.filter(
            organization_id=contract.organization_id, id=data.holiday_calendar_id
        ).exists()
    ):
        raise DomainValidationError("Calendário de feriados inexistente.")
    deadline = ContractDeadline.objects.create(
        organization_id=contract.organization_id,
        contract=contract,
        kind=data.kind.value,
        title=data.title.strip(),
        anchor=data.anchor.value,
        fixed_date=data.fixed_date,
        offset_days=data.offset_days,
        day_count=data.day_count.value,
        roll=data.roll.value,
        holiday_calendar_id=data.holiday_calendar_id,
        notify_days_before=data.notify_days_before,
        created_by_id=principal.actor_id,
    )
    _reschedule(contract, deadline)
    audit.record(
        principal,
        action="deadline.created",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "deadline_id": str(deadline.id),
            "kind": data.kind.value,
            "anchor": data.anchor.value,
            "offset_days": data.offset_days,
            "day_count": data.day_count.value,
            "roll": data.roll.value,
            "due_date": deadline.due_date.isoformat() if deadline.due_date else None,
        },
    )
    return deadline


def create_holiday_calendar(
    principal: Principal, *, name: str, holidays: Iterable[tuple[date, str]], is_default: bool
) -> HolidayCalendar:
    """Calendário de feriados informado pela organização (não há lista oficial embutida)."""
    authorize(principal, Permission.OBLIGATIONS_MANAGE)
    organization_id = require_tenant()
    if not name.strip():
        raise DomainValidationError("Informe o nome do calendário.")
    if is_default:
        HolidayCalendar.objects.filter(organization_id=organization_id, is_default=True).update(
            is_default=False
        )
    created = HolidayCalendar.objects.create(
        organization_id=organization_id, name=name.strip(), is_default=is_default
    )
    Holiday.objects.bulk_create(
        Holiday(
            organization_id=organization_id, calendar=created, day=day, name=label.strip()[:120]
        )
        for day, label in dict(holidays).items()
    )
    audit.record(
        principal,
        action="holiday_calendar.created",
        resource_type="holiday_calendar",
        resource_id=created.id,
        changes={"name": created.name, "default": is_default},
    )
    return created


def recompute_for_contract(contract: Contract) -> int:
    """Recalcula prazos pendentes após mudança de datas (ex.: renovação)."""
    pending = list(
        ContractDeadline.objects.filter(
            organization_id=contract.organization_id,
            contract=contract,
            status__in=[DeadlineStatus.PENDING, DeadlineStatus.UNSCHEDULED],
        )
    )
    for deadline in pending:
        _reschedule(contract, deadline)
    return len(pending)


def complete_deadline(principal: Principal, deadline_id: uuid.UUID) -> ContractDeadline:
    authorize(principal, Permission.OBLIGATIONS_MANAGE)
    deadline = (
        ContractDeadline.objects.select_for_update()
        .filter(organization_id=require_tenant(), id=deadline_id)
        .first()
    )
    if deadline is None:
        raise NotFoundError("Prazo não encontrado.")
    get_visible_contract(principal, deadline.contract_id)
    if deadline.status != DeadlineStatus.PENDING:
        raise ConflictError("Somente prazos pendentes podem ser cumpridos.")
    deadline.status = DeadlineStatus.DONE.value
    deadline.done_at = timezone.now()
    deadline.done_by_id = principal.actor_id
    deadline.save(update_fields=["status", "done_at", "done_by"])
    cancel_alerts(target_type="deadline", target_id=deadline.id)
    audit.record(
        principal,
        action="deadline.done",
        resource_type="contract",
        resource_id=deadline.contract_id,
        changes={"deadline_id": str(deadline.id)},
    )
    return deadline


# ---------------------------------------------------------------------------
# Renovação
# ---------------------------------------------------------------------------
def configure_renewal(
    principal: Principal,
    contract_id: uuid.UUID,
    *,
    mode: RenewalMode,
    term_months: int,
    max_renewals: int | None,
) -> RenewalPolicy:
    """Configura a renovação. A automática registra quem a autorizou e quando."""
    authorize(principal, Permission.CONTRACTS_LIFECYCLE)
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    if not 1 <= term_months <= MAX_RENEWAL_MONTHS:
        raise DomainValidationError(f"Prazo de renovação entre 1 e {MAX_RENEWAL_MONTHS} meses.")
    policy, _ = RenewalPolicy.objects.select_for_update().get_or_create(
        organization_id=contract.organization_id, contract=contract
    )
    policy.mode = mode.value
    policy.term_months = term_months
    policy.max_renewals = max_renewals
    if mode is RenewalMode.AUTOMATIC:
        policy.authorized_by_id = principal.actor_id
        policy.authorized_at = timezone.now()
    else:
        policy.authorized_by = None
        policy.authorized_at = None
    policy.save()
    audit.record(
        principal,
        action="renewal.configured",
        resource_type="contract",
        resource_id=contract.id,
        changes={"mode": mode.value, "term_months": term_months, "max_renewals": max_renewals},
    )
    return policy


def register_non_renewal(
    principal: Principal, contract_id: uuid.UUID, *, reason: str
) -> RenewalPolicy:
    authorize(principal, Permission.CONTRACTS_LIFECYCLE)
    if not reason.strip():
        raise DomainValidationError("Informe o motivo da não renovação.")
    contract = get_visible_contract(principal, contract_id, for_update=True)
    policy = (
        RenewalPolicy.objects.select_for_update()
        .filter(organization_id=contract.organization_id, contract=contract)
        .first()
    )
    if policy is None or policy.mode == RenewalMode.NONE:
        raise InvalidTransitionError("Contrato sem política de renovação.")
    policy.non_renewal_registered_at = timezone.now()
    policy.non_renewal_reason = reason.strip()[:500]
    policy.save(update_fields=["non_renewal_registered_at", "non_renewal_reason"])
    audit.record(
        principal,
        action="renewal.declined",
        resource_type="contract",
        resource_id=contract.id,
        changes={"reason": policy.non_renewal_reason},
    )
    return policy


def process_automatic_renewals(organization_id: uuid.UUID, *, today: date) -> int:
    """Renova contratos com renovação automática autorizada cujo término chegou.

    Executada antes do encerramento automático de vigência. A renovação vale
    no dia seguinte ao término: o contrato continua vigente sem interrupção.
    Cada execução registra base (término anterior), resultado e regra.
    """
    renewed = 0
    with tenant_context(organization_id):
        policies = list(
            RenewalPolicy.objects.select_for_update()
            .select_related("contract")
            .filter(
                mode=RenewalMode.AUTOMATIC,
                non_renewal_registered_at__isnull=True,
                contract__lifecycle_status=LifecycleStatus.ACTIVE,
                contract__effective_end__lt=today,
            )
            .order_by("id")
        )
        for policy in policies:
            contract = Contract.objects.select_for_update().get(id=policy.contract_id)
            done = RenewalRecord.objects.filter(contract=contract).count()
            while (
                contract.effective_end is not None
                and contract.effective_end < today
                and (policy.max_renewals is None or done < policy.max_renewals)
            ):
                previous = contract.effective_end
                new_end = add_months(previous, policy.term_months)
                done += 1
                RenewalRecord.objects.create(
                    organization_id=organization_id,
                    contract=contract,
                    sequence=done,
                    previous_end=previous,
                    new_end=new_end,
                    rule={
                        "mode": policy.mode,
                        "term_months": policy.term_months,
                        "authorized_by": str(policy.authorized_by_id),
                        "authorized_at": policy.authorized_at.isoformat()
                        if policy.authorized_at
                        else None,
                    },
                )
                contract.effective_end = new_end
                contract.version += 1
                contract.save(update_fields=["effective_end", "version"])
                audit.record(
                    None,
                    action="renewal.executed",
                    resource_type="contract",
                    resource_id=contract.id,
                    changes={
                        "sequence": done,
                        "previous_end": previous.isoformat(),
                        "new_end": new_end.isoformat(),
                    },
                )
                renewed += 1
            recompute_for_contract(contract)
    return renewed


def daily_cycle(organization: Organization, *, today: date | None = None) -> dict[str, int]:
    """Ordem: renovações automáticas, depois transições de vigência."""
    local = today or lifecycle.local_today(organization)
    return {
        "renewals": process_automatic_renewals(organization.id, today=local),
        "lifecycle": lifecycle.advance_due(organization.id, today=local),
    }
