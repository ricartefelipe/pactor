"""Casos de uso financeiros: cronograma, índices, reajustes e confirmações.

Distinções mantidas:

* **Valor contratado**: condição do contrato (com aditivos, por data).
* **Projeção**: soma das parcelas previstas e confirmadas, não canceladas.
* **Obrigação de pagamento**: parcelas previstas vencidas e não confirmadas.
* **Confirmação**: registro informado por usuário (não é conciliação bancária).

Reajustes só alteram parcelas previstas com vencimento a partir da data de
efeito; parcelas confirmadas e suas confirmações nunca mudam.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Max, Sum

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.audit import services as audit
from pactor.contracts import lifecycle_services as lifecycle
from pactor.contracts.selectors import get_visible_contract
from pactor.core.errors import (
    ConflictError,
    DomainValidationError,
    InvalidTransitionError,
    NotFoundError,
)
from pactor.core.tenancy import require_tenant
from pactor.deadlines.calendar import Frequency, Recurrence, occurrences
from pactor.finance.models import (
    AdjustmentRecord,
    IndexSeries,
    IndexValue,
    Installment,
    InstallmentKind,
    InstallmentStatus,
    PaymentConfirmation,
)
from pactor.finance.money import MoneyError, adjust, allocate, parse_amount, parse_rate
from pactor.identity.models import Organization
from pactor.plans import services as plans
from pactor.plans.models import Module

MAX_INSTALLMENTS = 360


def _amount(raw: object, field: str) -> Decimal:
    try:
        return parse_amount(raw)
    except MoneyError as exc:
        raise DomainValidationError(str(exc), details={field: [str(exc)]}) from exc


def _require_finance(principal: Principal, contract_id: uuid.UUID, *, write: bool) -> None:
    """Autoriza e, nas escritas, trava o contrato: serializa a numeração das
    parcelas e os reajustes concorrentes do mesmo contrato."""
    authorize(principal, Permission.FINANCE_WRITE if write else Permission.FINANCE_READ)
    if write:
        plans.ensure_module(Module.FINANCE)
    get_visible_contract(principal, contract_id, for_update=write)


def schedule_installments(
    principal: Principal,
    contract_id: uuid.UUID,
    *,
    total: object,
    parts: int,
    first_due: date,
    frequency: Frequency,
    description: str,
) -> list[Installment]:
    """Gera parcelas cujo total é exato (rateio determinístico de centavos)."""
    _require_finance(principal, contract_id, write=True)
    if not 1 <= parts <= MAX_INSTALLMENTS:
        raise DomainValidationError(f"Quantidade de parcelas entre 1 e {MAX_INSTALLMENTS}.")
    amounts = allocate(_amount(total, "total"), parts)
    try:
        due_dates = [
            d
            for _, d in occurrences(
                first_due, Recurrence(frequency, count=parts), horizon=date(9999, 12, 31)
            )
        ]
    except (ValueError, OverflowError) as exc:
        # Vencimentos depois do ano 9999 não são representáveis.
        raise DomainValidationError(
            "As parcelas ultrapassariam a última data representável."
        ) from exc
    if len(due_dates) != parts:
        raise DomainValidationError("Frequência incompatível com a quantidade de parcelas.")
    start = (
        Installment.objects.filter(
            organization_id=require_tenant(), contract_id=contract_id
        ).aggregate(m=Max("sequence"))["m"]
        or 0
    )
    created = Installment.objects.bulk_create(
        Installment(
            organization_id=require_tenant(),
            contract_id=contract_id,
            sequence=start + index,
            kind=InstallmentKind.INSTALLMENT,
            description=f"{description.strip() or 'Parcela'} {index}/{parts}",
            due_date=due,
            amount=amount,
        )
        for index, (due, amount) in enumerate(zip(due_dates, amounts, strict=True), start=1)
    )
    audit.record(
        principal,
        action="finance.installments_scheduled",
        resource_type="contract",
        resource_id=contract_id,
        changes={"parts": parts, "total": str(sum(amounts)), "first_due": first_due.isoformat()},
    )
    return created


def add_milestone(
    principal: Principal,
    contract_id: uuid.UUID,
    *,
    description: str,
    due_date: date,
    amount: object,
) -> Installment:
    _require_finance(principal, contract_id, write=True)
    if not description.strip():
        raise DomainValidationError("Descreva o marco.")
    sequence = (
        Installment.objects.filter(
            organization_id=require_tenant(), contract_id=contract_id
        ).aggregate(m=Max("sequence"))["m"]
        or 0
    ) + 1
    milestone = Installment.objects.create(
        organization_id=require_tenant(),
        contract_id=contract_id,
        sequence=sequence,
        kind=InstallmentKind.MILESTONE,
        description=description.strip()[:200],
        due_date=due_date,
        amount=_amount(amount, "amount"),
    )
    audit.record(
        principal,
        action="finance.milestone_added",
        resource_type="contract",
        resource_id=contract_id,
        changes={"sequence": sequence, "amount": str(milestone.amount)},
    )
    return milestone


def confirm(
    principal: Principal,
    installment_id: uuid.UUID,
    *,
    amount: object,
    confirmed_on: date,
    reference: str,
) -> PaymentConfirmation:
    """Registra a confirmação informada. Valor e data ficam imutáveis."""
    contract_id = (
        Installment.objects.filter(organization_id=require_tenant(), id=installment_id)
        .values_list("contract_id", flat=True)
        .first()
    )
    if contract_id is None:
        raise NotFoundError("Parcela não encontrada.")
    # Mesma ordem de travas dos reajustes (contrato, depois parcela): evita deadlock.
    _require_finance(principal, contract_id, write=True)
    installment = Installment.objects.select_for_update().get(
        organization_id=require_tenant(), id=installment_id
    )
    if installment.status != InstallmentStatus.PLANNED:
        raise InvalidTransitionError("Somente parcelas previstas podem ser confirmadas.")
    if not reference.strip():
        raise DomainValidationError("Informe a referência do comprovante.")
    confirmation = PaymentConfirmation.objects.create(
        organization_id=installment.organization_id,
        installment=installment,
        amount=_amount(amount, "amount"),
        confirmed_on=confirmed_on,
        reference=reference.strip()[:120],
        confirmed_by_id=principal.actor_id,
    )
    installment.status = InstallmentStatus.CONFIRMED.value
    installment.version += 1
    installment.save(update_fields=["status", "version"])
    audit.record(
        principal,
        action="finance.confirmed",
        resource_type="contract",
        resource_id=installment.contract_id,
        changes={
            "installment": installment.sequence,
            "amount": str(confirmation.amount),
            "reference": confirmation.reference,
        },
    )
    return confirmation


def register_index_value(
    principal: Principal,
    *,
    series_name: str,
    source: str,
    period: date,
    rate: object,
    source_note: str,
    demonstrative: bool,
) -> IndexValue:
    """Registra o valor de um índice com período e origem; nunca é inventado pelo sistema."""
    authorize(principal, Permission.FINANCE_WRITE)
    if not source.strip() or not source_note.strip():
        raise DomainValidationError("Informe a fonte do índice e a nota de origem do valor.")
    try:
        rate_percent = parse_rate(rate)
    except MoneyError as exc:
        raise DomainValidationError(str(exc)) from exc
    organization_id = require_tenant()
    series, _ = IndexSeries.objects.get_or_create(
        organization_id=organization_id,
        name=series_name.strip()[:80],
        defaults={"source": source.strip()[:200], "is_demonstrative": demonstrative},
    )
    if IndexValue.objects.filter(series=series, period=period.replace(day=1)).exists():
        raise ConflictError("Já existe valor registrado para o período.")
    value = IndexValue.objects.create(
        organization_id=organization_id,
        series=series,
        period=period.replace(day=1),
        rate_percent=rate_percent,
        source_note=source_note.strip()[:300],
        registered_by_id=principal.actor_id,
    )
    audit.record(
        principal,
        action="finance.index_registered",
        resource_type="index",
        resource_id=series.id,
        changes={
            "period": value.period.isoformat(),
            "rate": str(rate_percent),
            "demonstrative": series.is_demonstrative,
        },
    )
    return value


def apply_adjustment(
    principal: Principal, contract_id: uuid.UUID, *, index_value_id: uuid.UUID, applies_from: date
) -> AdjustmentRecord:
    """Reajusta parcelas previstas com vencimento a partir de ``applies_from``."""
    _require_finance(principal, contract_id, write=True)
    index_value = IndexValue.objects.filter(
        organization_id=require_tenant(), id=index_value_id
    ).first()
    if index_value is None:
        raise NotFoundError("Valor de índice não encontrado.")
    if AdjustmentRecord.objects.filter(
        organization_id=require_tenant(), contract_id=contract_id, index_value=index_value
    ).exists():
        raise ConflictError("Este valor de índice já reajustou o contrato.")
    targets = list(
        Installment.objects.select_for_update()
        .filter(
            organization_id=require_tenant(),
            contract_id=contract_id,
            status=InstallmentStatus.PLANNED,
            due_date__gte=applies_from,
        )
        .order_by("sequence")
    )
    if not targets:
        raise ConflictError("Nenhuma parcela prevista a partir da data de efeito.")
    affected: list[dict[str, str]] = []
    for installment in targets:
        before = installment.amount
        try:
            installment.amount = adjust(before, index_value.rate_percent)
        except MoneyError as exc:
            raise DomainValidationError(
                f"Parcela {installment.sequence}: {exc}.",
                details={"installment": installment.sequence},
            ) from exc
        installment.version += 1
        installment.save(update_fields=["amount", "version"])
        affected.append(
            {
                "installment_id": str(installment.id),
                "before": str(before),
                "after": str(installment.amount),
            }
        )
    record = AdjustmentRecord.objects.create(
        organization_id=require_tenant(),
        contract_id=contract_id,
        index_value=index_value,
        rate_percent=index_value.rate_percent,
        applies_from=applies_from,
        affected=affected,
        applied_by_id=principal.actor_id,
    )
    audit.record(
        principal,
        action="finance.adjusted",
        resource_type="contract",
        resource_id=contract_id,
        changes={"rate": str(index_value.rate_percent), "installments": len(affected)},
    )
    return record


@dataclass(frozen=True, slots=True)
class FinancialSummary:
    contracted: Decimal | None
    projected: Decimal
    confirmed: Decimal
    overdue: Decimal
    currency: str
    installments: Sequence[Installment]


def summary(principal: Principal, contract_id: uuid.UUID) -> FinancialSummary:
    _require_finance(principal, contract_id, write=False)
    today = lifecycle.local_today(Organization.objects.get(id=require_tenant()))
    conditions = lifecycle.current_conditions(principal, contract_id, on=today)
    installments = list(
        Installment.objects.filter(organization_id=require_tenant(), contract_id=contract_id)
        .select_related("confirmation")
        .order_by("sequence")
    )
    active = [i for i in installments if i.status != InstallmentStatus.CANCELLED]
    confirmed = PaymentConfirmation.objects.filter(
        organization_id=require_tenant(), installment__contract_id=contract_id
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    overdue = sum(
        (i.amount for i in active if i.status == InstallmentStatus.PLANNED and i.due_date < today),
        Decimal("0.00"),
    )
    return FinancialSummary(
        contracted=conditions.total_value,
        projected=sum((i.amount for i in active), Decimal("0.00")),
        confirmed=confirmed,
        overdue=overdue,
        currency="BRL",
        installments=installments,
    )
