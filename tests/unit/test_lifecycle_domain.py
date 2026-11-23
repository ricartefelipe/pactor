"""Vigência e resolução de condições por data."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pactor.contracts.lifecycle import (
    ConditionChange,
    ConditionField,
    Conditions,
    LifecycleAction,
    LifecycleError,
    LifecycleStatus,
    conditions_at,
    due_transition,
    is_in_force,
    next_status,
    status_after_signature,
)

S, A = LifecycleStatus, LifecycleAction
D = date(2027, 3, 10)
ORIGINAL = Conditions(
    total_value=Decimal("1000.00"), effective_end=date(2027, 12, 31), description=""
)


def test_signature_with_future_start_awaits_and_today_starts() -> None:
    assert status_after_signature(D + timedelta(days=1), D) is S.AWAITING_START
    assert status_after_signature(D, D) is S.ACTIVE
    assert status_after_signature(D - timedelta(days=5), D) is S.ACTIVE


@pytest.mark.parametrize(
    ("current", "action", "target"),
    [
        (S.NOT_STARTED, A.REGISTER_SIGNATURE, S.ACTIVE),
        (S.AWAITING_START, A.START, S.ACTIVE),
        (S.ACTIVE, A.SUSPEND, S.SUSPENDED),
        (S.SUSPENDED, A.RESUME, S.ACTIVE),
        (S.SUSPENDED, A.EXPIRE, S.ENDED),
        (S.AWAITING_START, A.TERMINATE, S.TERMINATED),
        (S.ENDED, A.ARCHIVE, S.ARCHIVED),
        (S.TERMINATED, A.ARCHIVE, S.ARCHIVED),
    ],
)
def test_allowed_transitions(
    current: LifecycleStatus, action: LifecycleAction, target: LifecycleStatus
) -> None:
    assert next_status(current, action, target) is target


@pytest.mark.parametrize(
    ("current", "action", "target"),
    [
        (S.NOT_STARTED, A.START, S.ACTIVE),
        (S.ACTIVE, A.ARCHIVE, S.ARCHIVED),
        (S.ARCHIVED, A.RESUME, S.ACTIVE),
        (S.TERMINATED, A.RESUME, S.ACTIVE),
        (S.SUSPENDED, A.SUSPEND, S.SUSPENDED),
        (S.NOT_STARTED, A.REGISTER_SIGNATURE, S.ENDED),
    ],
)
def test_incompatible_transitions_are_rejected(
    current: LifecycleStatus, action: LifecycleAction, target: LifecycleStatus
) -> None:
    with pytest.raises(LifecycleError):
        next_status(current, action, target)


def test_in_force_bounds_are_inclusive_and_indefinite_term() -> None:
    assert is_in_force(D, D, D)
    assert not is_in_force(D, D, D + timedelta(days=1))
    assert not is_in_force(D, None, D - timedelta(days=1))
    assert is_in_force(D, None, date(2100, 1, 1))
    assert not is_in_force(None, None, D)


def test_due_transitions_for_scheduler() -> None:
    assert due_transition(S.AWAITING_START, D, None, D) == (A.START, S.ACTIVE)
    assert due_transition(S.AWAITING_START, D, None, D - timedelta(days=1)) is None
    assert due_transition(S.ACTIVE, D, D, D) is None  # último dia ainda vige
    assert due_transition(S.ACTIVE, D, D, D + timedelta(days=1)) == (A.EXPIRE, S.ENDED)
    assert due_transition(S.SUSPENDED, D, D, D + timedelta(days=1)) == (A.EXPIRE, S.ENDED)
    assert due_transition(S.ACTIVE, D, None, date(2100, 1, 1)) is None
    # Agendador indisponível: início e término já passaram → START e, depois, EXPIRE.
    late = D + timedelta(days=400)
    assert due_transition(S.AWAITING_START, D, D + timedelta(days=10), late) == (A.START, S.ACTIVE)
    assert due_transition(S.ACTIVE, D, D + timedelta(days=10), late) == (A.EXPIRE, S.ENDED)
    assert due_transition(S.TERMINATED, D, D, late) is None


def test_conditions_apply_effective_changes_in_order() -> None:
    changes = [
        ConditionChange(2, ConditionField.TOTAL_VALUE, Decimal("1500.00"), date(2027, 6, 1)),
        ConditionChange(1, ConditionField.TOTAL_VALUE, Decimal("1200.00"), date(2027, 6, 1)),
        ConditionChange(3, ConditionField.EFFECTIVE_END, date(2028, 12, 31), date(2027, 9, 1)),
        ConditionChange(4, ConditionField.DESCRIPTION, "Inclui suporte 24x7", date(2027, 9, 1)),
    ]
    assert conditions_at(ORIGINAL, changes, date(2027, 5, 31)) == ORIGINAL
    assert conditions_at(ORIGINAL, changes, date(2027, 6, 1)).total_value == Decimal("1500.00")
    final = conditions_at(ORIGINAL, changes, date(2027, 9, 1))
    assert final == Conditions(Decimal("1500.00"), date(2028, 12, 31), "Inclui suporte 24x7")
    cleared = conditions_at(
        ORIGINAL, [ConditionChange(1, ConditionField.EFFECTIVE_END, None, D)], D
    )
    assert cleared.effective_end is None


def test_conditions_reject_wrong_value_types() -> None:
    with pytest.raises(LifecycleError):
        conditions_at(ORIGINAL, [ConditionChange(1, ConditionField.TOTAL_VALUE, "1000", D)], D)
    with pytest.raises(LifecycleError):
        conditions_at(
            ORIGINAL, [ConditionChange(1, ConditionField.EFFECTIVE_END, "2027-01-01", D)], D
        )


dates = st.dates(min_value=date(2026, 1, 1), max_value=date(2030, 12, 31))


@given(
    st.lists(st.tuples(dates, st.decimals(min_value=0, max_value=10**6, places=2)), max_size=8),
    dates,
)
def test_property_original_is_preserved_and_future_changes_ignored(
    raw: list[tuple[date, Decimal]], on: date
) -> None:
    changes = [ConditionChange(i, ConditionField.TOTAL_VALUE, v, d) for i, (d, v) in enumerate(raw)]
    result = conditions_at(ORIGINAL, changes, on)
    assert ORIGINAL.total_value == Decimal("1000.00")  # original nunca é alterado
    effective = sorted(
        (c for c in changes if c.effective_from <= on), key=lambda c: (c.effective_from, c.sequence)
    )
    expected = effective[-1].value if effective else ORIGINAL.total_value
    assert result.total_value == expected
