"""Casos de uso de obrigações e ocorrências.

Regras:

* Ocorrências têm identidade estável (obrigação, versão do cronograma,
  sequência). Gerar de novo é idempotente (restrição única no banco).
* Mudar o cronograma cria nova versão: ocorrências futuras pendentes da
  versão anterior são canceladas; concluídas e submetidas são preservadas.
* Obrigação que exige evidência não é concluída sem arquivo liberado.
* Dependência: uma ocorrência só é concluída quando a obrigação da qual
  depende não tem ocorrências pendentes com vencimento até a mesma data.
* Atraso é derivado: ocorrência pendente com vencimento anterior à data
  local de hoje. Escalonamento dispara após ``escalation_days_after``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import QuerySet
from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal, has_permission
from pactor.audit import services as audit
from pactor.contracts import lifecycle_services as lifecycle
from pactor.contracts.selectors import get_visible_contract, require_editable, visible_contracts
from pactor.core.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    InvalidTransitionError,
    NotFoundError,
)
from pactor.core.tenancy import require_tenant, tenant_context
from pactor.deadlines import services as deadlines
from pactor.deadlines.calendar import CalendarError, Frequency, Recurrence, add_months, occurrences
from pactor.deadlines.models import AlertKind
from pactor.files.models import FileStatus, StoredFile
from pactor.identity.models import Membership, MembershipStatus, Organization
from pactor.obligations.models import (
    Obligation,
    ObligationCategory,
    ObligationOccurrence,
    OccurrenceStatus,
    ResponsibleParty,
)
from pactor.plans import services as plans
from pactor.plans.models import Module

HORIZON_MONTHS = 24
_TARGET = "occurrence"


@dataclass(frozen=True, slots=True)
class ObligationInput:
    title: str
    category: ObligationCategory
    responsible_id: uuid.UUID
    responsible_party: ResponsibleParty
    frequency: Frequency
    first_due: date
    description: str = ""
    interval: int = 1
    until: date | None = None
    count: int | None = None
    requires_evidence: bool = False
    requires_approval: bool = False
    reminder_days_before: int = 7
    escalation_days_after: int = 3
    depends_on_id: uuid.UUID | None = None


def _today() -> date:
    return lifecycle.local_today(Organization.objects.get(id=require_tenant()))


def _recurrence(obligation: Obligation) -> Recurrence:
    try:
        return Recurrence(
            Frequency(obligation.frequency), obligation.interval, obligation.until, obligation.count
        )
    except CalendarError as exc:
        raise DomainValidationError(str(exc)) from exc


def _occurrence_alerts(
    obligation: Obligation, occurrence: ObligationOccurrence
) -> list[deadlines.AlertSpec]:
    specs = [
        deadlines.AlertSpec(
            AlertKind.DUE, occurrence.due_date, f"Obrigação vence hoje: {obligation.title}"
        )
    ]
    if obligation.reminder_days_before:
        specs.append(
            deadlines.AlertSpec(
                AlertKind.REMINDER,
                occurrence.due_date - timedelta(days=obligation.reminder_days_before),
                f"Obrigação em {obligation.reminder_days_before} dias: {obligation.title}",
            )
        )
    specs.append(
        deadlines.AlertSpec(
            AlertKind.ESCALATION,
            occurrence.due_date + timedelta(days=obligation.escalation_days_after),
            f"Obrigação em atraso: {obligation.title}",
        )
    )
    return specs


def generate_occurrences(obligation: Obligation, *, horizon: date) -> int:
    """Cria ocorrências faltantes até o horizonte. Idempotente."""
    if not obligation.is_active:
        return 0
    wanted = list(occurrences(obligation.first_due, _recurrence(obligation), horizon=horizon))
    existing = ObligationOccurrence.objects.filter(
        obligation=obligation, schedule_version=obligation.schedule_version
    )
    before = existing.count()
    # ignore_conflicts devolve todos os objetos enviados; a quantidade realmente
    # inserida é medida no banco, para que regerações idempotentes reportem zero.
    ObligationOccurrence.objects.bulk_create(
        [
            ObligationOccurrence(
                organization_id=obligation.organization_id,
                obligation=obligation,
                schedule_version=obligation.schedule_version,
                sequence=sequence,
                due_date=due,
            )
            for sequence, due in wanted
        ],
        ignore_conflicts=True,
    )
    current = ObligationOccurrence.objects.filter(
        obligation=obligation,
        schedule_version=obligation.schedule_version,
        status=OccurrenceStatus.PENDING,
    ).select_related("obligation__contract")
    for occurrence in current:
        # O escalonamento vai ao responsável e ao responsável pelo contrato.
        deadlines.schedule_alerts(
            target_type=_TARGET,
            target_id=occurrence.id,
            contract=obligation.contract,
            recipients=[obligation.responsible_id, obligation.contract.owner_id],
            specs=_occurrence_alerts(obligation, occurrence),
        )
    return existing.count() - before


def _validate_dependency(
    obligation_contract_id: uuid.UUID, depends_on_id: uuid.UUID | None
) -> None:
    if depends_on_id is None:
        return
    if not Obligation.objects.filter(
        organization_id=require_tenant(), id=depends_on_id, contract_id=obligation_contract_id
    ).exists():
        raise DomainValidationError("A dependência deve ser obrigação do mesmo contrato.")


def create_obligation(
    principal: Principal, contract_id: uuid.UUID, data: ObligationInput
) -> Obligation:
    authorize(principal, Permission.OBLIGATIONS_MANAGE)
    plans.ensure_module(Module.OBLIGATIONS)
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    if not data.title.strip():
        raise DomainValidationError("Informe o título da obrigação.")
    if not Membership.objects.filter(
        organization_id=contract.organization_id,
        user_id=data.responsible_id,
        status=MembershipStatus.ACTIVE,
    ).exists():
        raise DomainValidationError("Responsável deve participar da organização.")
    _validate_dependency(contract.id, data.depends_on_id)
    obligation = Obligation.objects.create(
        organization_id=contract.organization_id,
        contract=contract,
        title=data.title.strip(),
        description=data.description.strip(),
        category=data.category.value,
        responsible_id=data.responsible_id,
        responsible_party=data.responsible_party.value,
        requires_evidence=data.requires_evidence,
        requires_approval=data.requires_approval,
        frequency=data.frequency.value,
        interval=data.interval,
        first_due=data.first_due,
        until=data.until,
        count=data.count,
        reminder_days_before=data.reminder_days_before,
        escalation_days_after=data.escalation_days_after,
        depends_on_id=data.depends_on_id,
    )
    _recurrence(obligation)
    generated = generate_occurrences(obligation, horizon=lifecycle_horizon())
    audit.record(
        principal,
        action="obligation.created",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "obligation_id": str(obligation.id),
            "frequency": obligation.frequency,
            "occurrences": generated,
        },
    )
    return obligation


def lifecycle_horizon() -> date:
    return add_months(_today(), HORIZON_MONTHS)


def change_schedule(
    principal: Principal,
    obligation_id: uuid.UUID,
    *,
    expected_version: int,
    frequency: Frequency,
    first_due: date,
    interval: int = 1,
    until: date | None = None,
    count: int | None = None,
) -> Obligation:
    """Novo cronograma: futuras pendentes canceladas; concluídas e submetidas preservadas."""
    authorize(principal, Permission.OBLIGATIONS_MANAGE)
    obligation = _locked_obligation(principal, obligation_id)
    if obligation.version != expected_version:
        raise ConflictError(
            "A obrigação mudou desde a sua leitura.",
            details={"current_version": obligation.version},
        )
    today = _today()
    future_pending = ObligationOccurrence.objects.filter(
        obligation=obligation, status=OccurrenceStatus.PENDING, due_date__gte=today
    )
    for occurrence in future_pending:
        deadlines.cancel_alerts(target_type=_TARGET, target_id=occurrence.id)
    cancelled = future_pending.update(status=OccurrenceStatus.CANCELLED)
    obligation.frequency = frequency.value
    obligation.first_due = first_due
    obligation.interval = interval
    obligation.until = until
    obligation.count = count
    obligation.schedule_version += 1
    obligation.version += 1
    obligation.updated_at = timezone.now()
    _recurrence(obligation)
    obligation.save()
    generated = generate_occurrences(obligation, horizon=lifecycle_horizon())
    audit.record(
        principal,
        action="obligation.rescheduled",
        resource_type="contract",
        resource_id=obligation.contract_id,
        changes={
            "obligation_id": str(obligation.id),
            "cancelled": cancelled,
            "generated": generated,
            "schedule_version": obligation.schedule_version,
        },
    )
    return obligation


def _locked_obligation(principal: Principal, obligation_id: uuid.UUID) -> Obligation:
    obligation = (
        Obligation.objects.select_for_update()
        .filter(organization_id=require_tenant(), id=obligation_id)
        .first()
    )
    if obligation is None:
        raise NotFoundError("Obrigação não encontrada.")
    get_visible_contract(principal, obligation.contract_id)
    return obligation


def _locked_occurrence(principal: Principal, occurrence_id: uuid.UUID) -> ObligationOccurrence:
    occurrence = (
        ObligationOccurrence.objects.select_for_update(of=("self",))
        .select_related("obligation")
        .filter(organization_id=require_tenant(), id=occurrence_id)
        .first()
    )
    if occurrence is None:
        raise NotFoundError("Ocorrência não encontrada.")
    get_visible_contract(principal, occurrence.obligation.contract_id)
    return occurrence


def _check_dependency(occurrence: ObligationOccurrence) -> None:
    depends_on = occurrence.obligation.depends_on_id
    if depends_on is None:
        return
    blocking = ObligationOccurrence.objects.filter(
        obligation_id=depends_on,
        status__in=[OccurrenceStatus.PENDING, OccurrenceStatus.SUBMITTED],
        due_date__lte=occurrence.due_date,
    ).exists()
    if blocking:
        raise InvalidTransitionError("Há ocorrência pendente da obrigação da qual esta depende.")


def submit_completion(
    principal: Principal, occurrence_id: uuid.UUID, *, evidence_id: uuid.UUID | None, note: str = ""
) -> ObligationOccurrence:
    authorize(principal, Permission.OBLIGATIONS_COMPLETE)
    plans.ensure_writable()
    occurrence = _locked_occurrence(principal, occurrence_id)
    obligation = occurrence.obligation
    if principal.actor_id != obligation.responsible_id and not has_permission(
        principal, Permission.OBLIGATIONS_MANAGE
    ):
        raise AccessDeniedError("Somente o responsável ou a gestão concluem a obrigação.")
    if occurrence.status != OccurrenceStatus.PENDING:
        raise InvalidTransitionError(
            "Ocorrência não está pendente.", details={"status": occurrence.status}
        )
    evidence: StoredFile | None = None
    if evidence_id is not None:
        evidence = StoredFile.objects.filter(
            organization_id=occurrence.organization_id, id=evidence_id
        ).first()
        if evidence is None or evidence.uploaded_by_id != principal.actor_id:
            raise DomainValidationError("Evidência inexistente ou enviada por outra pessoa.")
        if evidence.status != FileStatus.RELEASED:
            raise ConflictError("A evidência ainda não foi liberada pela verificação de arquivos.")
    if obligation.requires_evidence and evidence is None:
        raise DomainValidationError("Esta obrigação exige evidência documental.")
    _check_dependency(occurrence)
    now = timezone.now()
    occurrence.evidence = evidence
    occurrence.note = note.strip()[:1000]
    occurrence.submitted_by_id = principal.actor_id
    occurrence.submitted_at = now
    if obligation.requires_approval:
        occurrence.status = OccurrenceStatus.SUBMITTED.value
    else:
        occurrence.status = OccurrenceStatus.COMPLETED.value
        occurrence.completed_by_id = principal.actor_id
        occurrence.completed_at = now
        deadlines.cancel_alerts(target_type=_TARGET, target_id=occurrence.id)
    occurrence.save()
    audit.record(
        principal,
        action=f"obligation.{occurrence.status}",
        resource_type="contract",
        resource_id=obligation.contract_id,
        changes={
            "occurrence_id": str(occurrence.id),
            "evidence": str(evidence.id) if evidence else None,
            "evidence_sha256": evidence.sha256 if evidence else None,
        },
    )
    return occurrence


def review_completion(
    principal: Principal, occurrence_id: uuid.UUID, *, approve: bool, note: str = ""
) -> ObligationOccurrence:
    """Aprova ou devolve uma conclusão submetida; quem submeteu não aprova."""
    authorize(principal, Permission.OBLIGATIONS_MANAGE)
    occurrence = _locked_occurrence(principal, occurrence_id)
    if occurrence.status != OccurrenceStatus.SUBMITTED:
        raise InvalidTransitionError("Ocorrência não está aguardando aprovação.")
    if occurrence.submitted_by_id == principal.actor_id:
        raise AccessDeniedError("Quem submeteu a conclusão não pode aprová-la.")
    if approve:
        occurrence.status = OccurrenceStatus.COMPLETED.value
        occurrence.completed_by_id = principal.actor_id
        occurrence.completed_at = timezone.now()
        deadlines.cancel_alerts(target_type=_TARGET, target_id=occurrence.id)
    else:
        if not note.strip():
            raise DomainValidationError("Informe o motivo da devolução.")
        occurrence.status = OccurrenceStatus.PENDING.value
        occurrence.note = note.strip()[:1000]
    occurrence.save()
    audit.record(
        principal,
        action="obligation.completion_reviewed",
        resource_type="contract",
        resource_id=occurrence.obligation.contract_id,
        changes={"occurrence_id": str(occurrence.id), "approved": approve},
    )
    return occurrence


def occurrences_for(
    principal: Principal, *, start: date | None = None, end: date | None = None
) -> QuerySet[ObligationOccurrence]:
    """Ocorrências de contratos visíveis ao principal, em ordem determinística."""
    queryset = ObligationOccurrence.objects.filter(
        organization_id=principal.organization_id,
        obligation__contract__in=visible_contracts(principal).values("id"),
    ).select_related("obligation", "obligation__contract")
    if start is not None:
        queryset = queryset.filter(due_date__gte=start)
    if end is not None:
        queryset = queryset.filter(due_date__lte=end)
    return queryset.order_by("due_date", "id")


def extend_horizon(organization_id: uuid.UUID) -> int:
    """Gera ocorrências que entraram no horizonte (agendador). Idempotente."""
    created = 0
    with tenant_context(organization_id):
        horizon = lifecycle_horizon()
        for obligation in (
            Obligation.objects.filter(is_active=True).select_related("contract").order_by("id")
        ):
            created += generate_occurrences(obligation, horizon=horizon)
    return created
