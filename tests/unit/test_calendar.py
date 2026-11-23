"""Calendário: dias corridos e úteis, feriados, fim de mês, bissextos e recorrência."""

from __future__ import annotations

import calendar as stdlib_calendar
from datetime import date, timedelta
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pactor.deadlines.calendar import (
    BusinessCalendar,
    CalendarError,
    DayCount,
    DeadlineRule,
    Frequency,
    Recurrence,
    Roll,
    add_months,
    occurrences,
)

HOLIDAYS = BusinessCalendar(holidays=frozenset({date(2027, 4, 21), date(2027, 11, 15)}))
dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31))


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2027, 1, 31), 1, date(2027, 2, 28)),
        (date(2028, 1, 31), 1, date(2028, 2, 29)),
        (date(2028, 2, 29), 12, date(2029, 2, 28)),
        (date(2027, 3, 31), -1, date(2027, 2, 28)),
        (date(2027, 12, 15), 1, date(2028, 1, 15)),
        (date(2027, 1, 15), -13, date(2025, 12, 15)),
    ],
)
def test_add_months_clamps_to_month_end(start: date, months: int, expected: date) -> None:
    assert add_months(start, months) == expected


@given(dates, st.integers(min_value=-240, max_value=240))
def test_property_add_months_keeps_day_or_month_end(start: date, months: int) -> None:
    result = add_months(start, months)
    assert (result.year * 12 + result.month) - (start.year * 12 + start.month) == months
    last = stdlib_calendar.monthrange(result.year, result.month)[1]
    assert result.day == min(start.day, last)


def test_calendar_days_do_not_count_anchor() -> None:
    assert DeadlineRule(-30).compute(date(2027, 12, 31), HOLIDAYS) == date(2027, 12, 1)
    assert DeadlineRule(0).compute(date(2027, 12, 31), HOLIDAYS) == date(2027, 12, 31)


def test_business_days_skip_weekends_and_holidays() -> None:
    # 2027-04-22 (quinta) menos 2 dias úteis: 21/04 é feriado → 20/04 e 19/04.
    rule = DeadlineRule(-2, DayCount.BUSINESS)
    assert rule.compute(date(2027, 4, 22), HOLIDAYS) == date(2027, 4, 19)
    # Sexta + 1 dia útil = segunda.
    assert DeadlineRule(1, DayCount.BUSINESS).compute(date(2027, 4, 16), HOLIDAYS) == date(
        2027, 4, 19
    )


@pytest.mark.parametrize(
    ("roll", "expected"),
    [
        (Roll.NONE, date(2027, 11, 13)),
        (Roll.FOLLOWING, date(2027, 11, 16)),
        (Roll.PRECEDING, date(2027, 11, 12)),
    ],
)
def test_roll_moves_weekend_and_holiday_results(roll: Roll, expected: date) -> None:
    # 13/11/2027 é sábado; 15/11 é feriado; próximo dia útil é 16/11.
    assert DeadlineRule(0, roll=roll).compute(date(2027, 11, 13), HOLIDAYS) == expected


@given(dates, st.integers(min_value=-60, max_value=60))
def test_property_business_offsets_land_on_business_days(anchor: date, offset: int) -> None:
    result = DeadlineRule(offset, DayCount.BUSINESS).compute(anchor, HOLIDAYS)
    if offset != 0:
        assert HOLIDAYS.is_business_day(result)
        assert (result > anchor) == (offset > 0)
    business_between = sum(
        1
        for i in range(1, abs((result - anchor).days) + 1)
        if HOLIDAYS.is_business_day(anchor + timedelta(days=i if offset > 0 else -i))
    )
    assert business_between == abs(offset)


def test_calendar_without_business_days_fails_explicitly() -> None:
    never = BusinessCalendar(weekend=frozenset(range(7)))
    with pytest.raises(CalendarError):
        never.add_business_days(date(2027, 1, 1), 1)


def test_monthly_recurrence_is_anchored_to_start() -> None:
    result = list(
        occurrences(date(2027, 1, 31), Recurrence(Frequency.MONTHLY), horizon=date(2027, 5, 31))
    )
    assert result == [
        (1, date(2027, 1, 31)),
        (2, date(2027, 2, 28)),
        (3, date(2027, 3, 31)),
        (4, date(2027, 4, 30)),
        (5, date(2027, 5, 31)),
    ]


def test_recurrence_limits_and_frequencies() -> None:
    start = date(2027, 1, 10)
    assert list(occurrences(start, Recurrence(Frequency.ONCE), horizon=date(2030, 1, 1))) == [
        (1, start)
    ]
    assert (
        len(
            list(
                occurrences(
                    start, Recurrence(Frequency.WEEKLY, interval=2), horizon=date(2027, 2, 10)
                )
            )
        )
        == 3
    )
    assert [
        d
        for _, d in occurrences(
            start, Recurrence(Frequency.QUARTERLY, count=2), horizon=date(2030, 1, 1)
        )
    ] == [start, date(2027, 4, 10)]
    assert [
        d
        for _, d in occurrences(
            start, Recurrence(Frequency.YEARLY, until=date(2028, 6, 1)), horizon=date(2030, 1, 1)
        )
    ] == [start, date(2028, 1, 10)]
    assert (
        len(list(occurrences(start, Recurrence(Frequency.SEMIANNUAL), horizon=date(2028, 1, 10))))
        == 3
    )
    with pytest.raises(CalendarError):
        Recurrence(Frequency.MONTHLY, interval=0)
    with pytest.raises(CalendarError):
        Recurrence(Frequency.MONTHLY, count=0)


@given(
    dates,
    st.sampled_from([f for f in Frequency if f is not Frequency.ONCE]),
    st.integers(1, 3),
    st.integers(0, 800),
)
def test_property_regeneration_is_stable(
    start: date, frequency: Frequency, interval: int, extra_days: int
) -> None:
    """Estender o horizonte só acrescenta ocorrências; as anteriores não mudam."""
    rule = Recurrence(frequency, interval=interval)
    short = list(occurrences(start, rule, horizon=start + timedelta(days=200)))
    long = list(occurrences(start, rule, horizon=start + timedelta(days=200 + extra_days)))
    assert long[: len(short)] == short
    assert [s for s, _ in long] == list(range(1, len(long) + 1))
    assert all(a[1] < b[1] for a, b in pairwise(long))
