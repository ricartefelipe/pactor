"""Disputas reais entre conexões nas escritas financeiras do mesmo contrato."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from django.db import connections

from pactor.access.policies import Role
from pactor.contracts.models import Contract
from pactor.core.errors import ConflictError
from pactor.core.tenancy import tenant_context
from pactor.deadlines.calendar import Frequency
from pactor.finance import services
from pactor.finance.models import AdjustmentRecord, Installment
from pactor.identity.models import Organization, User
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


def _race(workers: list[Callable[[], object]]) -> list[object]:
    barrier = threading.Barrier(len(workers))
    results: list[object] = [None] * len(workers)

    def run(index: int, work: Callable[[], object]) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = work()
        except ConflictError as exc:
            results[index] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def _setup() -> tuple[Organization, User, Contract]:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    finance = factories.member(org, [Role.FINANCE])
    contract = factories.contract(org, manager)
    return org, finance, contract


def test_concurrent_schedules_receive_distinct_sequences() -> None:
    org, finance, contract = _setup()

    def milestone() -> object:
        with tenant_context(org.id):
            return services.add_milestone(
                factories.principal(org, finance),
                contract.id,
                description="Marco",
                due_date=date(2030, 1, 31),
                amount="100.00",
            )

    def schedule() -> object:
        with tenant_context(org.id):
            return services.schedule_installments(
                factories.principal(org, finance),
                contract.id,
                total="300.00",
                parts=3,
                first_due=date(2030, 1, 31),
                frequency=Frequency.MONTHLY,
                description="Parcela",
            )

    results = _race([milestone, schedule])
    assert not any(isinstance(r, BaseException) or r is None for r in results)
    with tenant_context(org.id):
        sequences = sorted(
            Installment.objects.filter(contract=contract).values_list("sequence", flat=True)
        )
    assert sequences == [1, 2, 3, 4]


def test_concurrent_adjustments_with_the_same_index_apply_once() -> None:
    org, finance, contract = _setup()
    with tenant_context(org.id):
        principal = factories.principal(org, finance)
        services.add_milestone(
            principal, contract.id, description="Marco", due_date=date(2030, 1, 31), amount="100.00"
        )
        value = services.register_index_value(
            principal,
            series_name="Índice Z (fictício)",
            source="Fonte fictícia",
            period=date(2029, 12, 1),
            rate=Decimal("10"),
            source_note="Nota",
            demonstrative=True,
        )

    def apply() -> object:
        with tenant_context(org.id):
            return services.apply_adjustment(
                factories.principal(org, finance),
                contract.id,
                index_value_id=value.id,
                applies_from=date(2030, 1, 1),
            )

    results = _race([apply, apply])
    assert sum(isinstance(r, AdjustmentRecord) for r in results) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    with tenant_context(org.id):
        assert Installment.objects.get(contract=contract).amount == Decimal("110.00")
