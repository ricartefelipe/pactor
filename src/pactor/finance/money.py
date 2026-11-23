"""Valores monetários (regras puras).

* Sempre ``Decimal`` com moeda explícita; a primeira versão aceita apenas BRL.
* ``float`` é recusado na entrada: valores chegam como texto ou ``Decimal``.
* Precisão de 2 casas, limite de 999.999.999.999,99, sem negativos para
  valores contratuais. Arredondamento meio-par (ROUND_HALF_EVEN), aplicado
  uma única vez no resultado de cada operação.
* Rateio de parcelas é exato: a soma das parcelas é sempre igual ao total; os
  centavos restantes vão para as primeiras parcelas, de forma determinística.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Final

CENT: Final = Decimal("0.01")
MAX_AMOUNT: Final = Decimal("999999999999.99")
SUPPORTED_CURRENCIES: Final = frozenset({"BRL"})
MAX_RATE_PERCENT: Final = Decimal("1000")


class MoneyError(ValueError):
    """Valor monetário inválido."""


def quantize(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def parse_amount(raw: object) -> Decimal:
    """Converte texto/Decimal em valor válido; recusa float, não finitos e excesso de casas."""
    if isinstance(raw, bool) or not isinstance(raw, str | Decimal):
        raise MoneyError("valor deve ser texto ou Decimal (float não é aceito)")
    try:
        value = Decimal(raw.strip()) if isinstance(raw, str) else raw
    except InvalidOperation as exc:
        raise MoneyError("valor inválido") from exc
    if not value.is_finite():
        raise MoneyError("valor deve ser finito")
    # Limites antes da quantização: expoentes grandes (ex.: "1e30") excedem a
    # precisão do contexto decimal e não chegam a ser arredondados.
    if value < 0 or value > MAX_AMOUNT:
        raise MoneyError("valor fora dos limites")
    if value != value.quantize(CENT):
        raise MoneyError("valor com mais de duas casas decimais")
    return quantize(value)


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str = "BRL"

    def __post_init__(self) -> None:
        if self.currency not in SUPPORTED_CURRENCIES:
            raise MoneyError(f"moeda não suportada: {self.currency}")
        parse_amount(self.amount)

    def __add__(self, other: Money) -> Money:
        if other.currency != self.currency:
            raise MoneyError("moedas diferentes")
        return Money(quantize(self.amount + other.amount), self.currency)


def allocate(total: Decimal, parts: int) -> list[Decimal]:
    """Divide ``total`` em ``parts`` parcelas cuja soma é exatamente o total."""
    if parts < 1:
        raise MoneyError("quantidade de parcelas deve ser positiva")
    cents = int(parse_amount(total) * 100)
    base, remainder = divmod(cents, parts)
    return [Decimal(base + (1 if index < remainder else 0)) / 100 for index in range(parts)]


def parse_rate(raw: object) -> Decimal:
    """Taxa de reajuste em percentual (ex.: "4.5" = 4,5%). Até 6 casas decimais."""
    if isinstance(raw, bool) or not isinstance(raw, str | Decimal):
        raise MoneyError("taxa deve ser texto ou Decimal")
    try:
        value = Decimal(raw.strip()) if isinstance(raw, str) else raw
    except InvalidOperation as exc:
        raise MoneyError("taxa inválida") from exc
    if not value.is_finite():
        raise MoneyError("taxa deve ser finita")
    if not -MAX_RATE_PERCENT < value < MAX_RATE_PERCENT:
        raise MoneyError("taxa fora dos limites")
    if value != value.quantize(Decimal("0.000001")):
        raise MoneyError("taxa com mais de 6 casas decimais")
    return value


def adjust(amount: Decimal, rate_percent: Decimal) -> Decimal:
    """Aplica a taxa percentual e arredonda o resultado uma única vez."""
    result = quantize(amount * (Decimal(1) + rate_percent / Decimal(100)))
    if result < 0:
        raise MoneyError("reajuste resultaria em valor negativo")
    if result > MAX_AMOUNT:
        raise MoneyError("reajuste ultrapassaria o valor máximo permitido")
    return result
