"""Dinheiro: Decimal, BRL, limites, arredondamento, rateio e reajuste."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pactor.finance.money import (
    MAX_AMOUNT,
    Money,
    MoneyError,
    adjust,
    allocate,
    parse_amount,
    parse_rate,
)

amounts = st.decimals(
    min_value=0, max_value=MAX_AMOUNT, places=2, allow_nan=False, allow_infinity=False
)


@pytest.mark.parametrize(
    "raw",
    [
        1.5,
        True,
        None,
        "NaN",
        "Infinity",
        "-1",
        "1.001",
        "1e20",
        "1e30",  # excede a precisão do contexto decimal ao quantizar
        "-1e40",
        "abc",
        Decimal("sNaN"),
    ],
)
def test_invalid_amounts_are_rejected(raw: object) -> None:
    with pytest.raises(MoneyError):
        parse_amount(raw)


def test_valid_amounts_keep_two_places() -> None:
    assert parse_amount(" 10.5 ") == Decimal("10.50")
    assert parse_amount(Decimal("0")) == Decimal("0.00")
    assert parse_amount(str(MAX_AMOUNT)) == MAX_AMOUNT


def test_money_currency_and_addition() -> None:
    assert Money(Decimal("1.10")) + Money(Decimal("2.20")) == Money(Decimal("3.30"))
    with pytest.raises(MoneyError, match="moeda"):
        Money(Decimal("1.00"), "USD")


@given(amounts, st.integers(min_value=1, max_value=360))
def test_property_allocation_is_exact_and_fair(total: Decimal, parts: int) -> None:
    installments = allocate(total, parts)
    assert len(installments) == parts
    assert sum(installments) == total
    assert max(installments) - min(installments) <= Decimal("0.01")
    assert installments == sorted(installments, reverse=True)
    assert all(i == i.quantize(Decimal("0.01")) for i in installments)


def test_allocation_examples() -> None:
    assert allocate(Decimal("100.00"), 3) == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    with pytest.raises(MoneyError):
        allocate(Decimal("1.00"), 0)


@pytest.mark.parametrize(
    ("amount", "rate", "expected"),
    [
        ("1000.00", "4.5", "1045.00"),
        ("0.05", "10", "0.06"),  # 0,055 → 0,06 (meio-par: 5 seguido de nada arredonda para par 6)
        ("0.25", "10", "0.28"),  # 0,275 → 0,28
        ("100.00", "-3.25", "96.75"),
        ("333.33", "0.000001", "333.33"),
    ],
)
def test_adjustment_rounds_once_half_even(amount: str, rate: str, expected: str) -> None:
    assert adjust(Decimal(amount), parse_rate(rate)) == Decimal(expected)


@pytest.mark.parametrize(
    "raw", [0.1, "x", "NaN", "1.0000001", "1000", "-1000", "1e30", "-1e40", False]
)
def test_invalid_rates(raw: object) -> None:
    with pytest.raises(MoneyError):
        parse_rate(raw)


@given(
    amounts,
    st.decimals(min_value=-99, max_value=99, places=6, allow_nan=False, allow_infinity=False),
)
def test_property_adjustment_result_has_two_places(amount: Decimal, rate: Decimal) -> None:
    exact = amount * (Decimal(1) + rate / Decimal(100))
    if exact.quantize(Decimal("0.01")) > MAX_AMOUNT:
        with pytest.raises(MoneyError, match="máximo"):
            adjust(amount, rate)
        return
    result = adjust(amount, rate)
    assert result == result.quantize(Decimal("0.01"))
    assert 0 <= result <= MAX_AMOUNT


def test_adjustment_beyond_the_maximum_is_rejected() -> None:
    with pytest.raises(MoneyError, match="máximo"):
        adjust(Decimal("200000000000.00"), parse_rate("500"))
