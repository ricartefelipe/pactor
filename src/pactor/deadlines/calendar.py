"""Cálculo de prazos com calendário explícito (regras puras).

Definições:

* **Âncora**: a data de referência (ex.: término da vigência). O dia da
  âncora não é contado: "30 dias antes" de 31/12 é 01/12.
* **Dias corridos**: deslocamento direto no calendário. **Dias úteis**:
  contam apenas dias que não são fim de semana nem feriado do calendário
  informado.
* **Ajuste** (``roll``): se o resultado cair em dia não útil, ``following``
  avança para o próximo dia útil, ``preceding`` recua para o anterior e
  ``none`` mantém a data.
* **Meses**: somar meses preserva o dia da âncora quando ele existe no mês de
  destino; caso contrário usa o último dia do mês (31/01 + 1 mês = 28/02 ou
  29/02 em ano bissexto). Recorrências sempre partem da data inicial, nunca da
  ocorrência anterior, para não "encolher" o dia (31 → 28 → 28...).

Nenhum prazo legal é presumido: toda regra vem de configuração registrada.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

SATURDAY, SUNDAY = 5, 6
MAX_BUSINESS_SEARCH_DAYS = 3660


class DayCount(StrEnum):
    CALENDAR = "calendar"
    BUSINESS = "business"


class Roll(StrEnum):
    NONE = "none"
    FOLLOWING = "following"
    PRECEDING = "preceding"


class CalendarError(ValueError):
    """Calendário inconsistente (ex.: nenhum dia útil em um intervalo plausível)."""


@dataclass(frozen=True, slots=True)
class BusinessCalendar:
    holidays: frozenset[date] = frozenset()
    weekend: frozenset[int] = field(default_factory=lambda: frozenset({SATURDAY, SUNDAY}))

    def is_business_day(self, day: date) -> bool:
        return day.weekday() not in self.weekend and day not in self.holidays

    def _step(self, day: date, direction: int) -> date:
        for _ in range(MAX_BUSINESS_SEARCH_DAYS):
            day += timedelta(days=direction)
            if self.is_business_day(day):
                return day
        raise CalendarError("nenhum dia útil encontrado no intervalo de busca")

    def roll(self, day: date, rule: Roll) -> date:
        if rule is Roll.NONE or self.is_business_day(day):
            return day
        return self._step(day, 1 if rule is Roll.FOLLOWING else -1)

    def add_business_days(self, day: date, amount: int) -> date:
        direction = 1 if amount >= 0 else -1
        current = day
        for _ in range(abs(amount)):
            current = self._step(current, direction)
        return current


def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


@dataclass(frozen=True, slots=True)
class DeadlineRule:
    """Deslocamento em relação à âncora. Negativo: antes; positivo: depois."""

    offset: int
    day_count: DayCount = DayCount.CALENDAR
    roll: Roll = Roll.NONE

    def compute(self, anchor: date, business: BusinessCalendar) -> date:
        if self.day_count is DayCount.CALENDAR:
            target = anchor + timedelta(days=self.offset)
        else:
            target = business.add_business_days(anchor, self.offset)
        return business.roll(target, self.roll)


class Frequency(StrEnum):
    ONCE = "once"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    YEARLY = "yearly"


_MONTHS = {
    Frequency.MONTHLY: 1,
    Frequency.QUARTERLY: 3,
    Frequency.SEMIANNUAL: 6,
    Frequency.YEARLY: 12,
}


@dataclass(frozen=True, slots=True)
class Recurrence:
    frequency: Frequency
    interval: int = 1
    until: date | None = None
    count: int | None = None

    def __post_init__(self) -> None:
        if self.interval < 1:
            raise CalendarError("intervalo deve ser positivo")
        if self.count is not None and self.count < 1:
            raise CalendarError("quantidade deve ser positiva")


def occurrences(
    start: date, recurrence: Recurrence, *, horizon: date
) -> Iterator[tuple[int, date]]:
    """Ocorrências (sequência a partir de 1, data) até ``horizon`` inclusive.

    A sequência é estável: a n-ésima ocorrência é sempre calculada a partir de
    ``start`` e ``n``, de modo que regenerar não muda números nem datas.
    """
    sequence = 1
    while True:
        if recurrence.frequency is Frequency.ONCE:
            due = start if sequence == 1 else None
        elif recurrence.frequency is Frequency.WEEKLY:
            due = start + timedelta(weeks=(sequence - 1) * recurrence.interval)
        else:
            due = add_months(
                start, (sequence - 1) * recurrence.interval * _MONTHS[recurrence.frequency]
            )
        if due is None or due > horizon:
            return
        if recurrence.until is not None and due > recurrence.until:
            return
        if recurrence.count is not None and sequence > recurrence.count:
            return
        yield sequence, due
        sequence += 1
