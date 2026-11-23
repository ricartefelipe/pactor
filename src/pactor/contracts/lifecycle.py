"""Vigência de contratos e resolução de condições por data (regras puras).

A vigência é independente da elaboração e da assinatura: um contrato
assinado pode aguardar o início da vigência. Datas de vigência são datas
locais da organização e os limites são inclusivos: o contrato vale do dia de
início ao dia de término, inclusive.

Aditivos nunca reescrevem o contrato original. Cada aditivo assinado
registra alterações com data de efeito; as condições vigentes em uma data
são obtidas aplicando, em ordem determinística, as alterações já efetivas
sobre as condições originais.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType


class LifecycleStatus(StrEnum):
    NOT_STARTED = "not_started"
    AWAITING_START = "awaiting_start"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ENDED = "ended"
    TERMINATED = "terminated"
    ARCHIVED = "archived"


class LifecycleAction(StrEnum):
    REGISTER_SIGNATURE = "register_signature"
    START = "start"
    SUSPEND = "suspend"
    RESUME = "resume"
    EXPIRE = "expire"
    TERMINATE = "terminate"
    ARCHIVE = "archive"


_S = LifecycleStatus
_A = LifecycleAction

TRANSITIONS: Mapping[tuple[LifecycleStatus, LifecycleAction], frozenset[LifecycleStatus]] = (
    MappingProxyType(
        {
            (_S.NOT_STARTED, _A.REGISTER_SIGNATURE): frozenset({_S.AWAITING_START, _S.ACTIVE}),
            (_S.AWAITING_START, _A.START): frozenset({_S.ACTIVE}),
            (_S.ACTIVE, _A.SUSPEND): frozenset({_S.SUSPENDED}),
            (_S.SUSPENDED, _A.RESUME): frozenset({_S.ACTIVE}),
            (_S.ACTIVE, _A.EXPIRE): frozenset({_S.ENDED}),
            (_S.SUSPENDED, _A.EXPIRE): frozenset({_S.ENDED}),
            (_S.AWAITING_START, _A.TERMINATE): frozenset({_S.TERMINATED}),
            (_S.ACTIVE, _A.TERMINATE): frozenset({_S.TERMINATED}),
            (_S.SUSPENDED, _A.TERMINATE): frozenset({_S.TERMINATED}),
            (_S.ENDED, _A.ARCHIVE): frozenset({_S.ARCHIVED}),
            (_S.TERMINATED, _A.ARCHIVE): frozenset({_S.ARCHIVED}),
        }
    )
)

# Situações que contam como contrato ativo para os limites do plano.
COUNTS_AS_ACTIVE = frozenset({_S.AWAITING_START, _S.ACTIVE, _S.SUSPENDED})


class LifecycleError(ValueError):
    """Transição incompatível com a situação ou com as datas."""


def next_status(
    current: LifecycleStatus, action: LifecycleAction, target: LifecycleStatus
) -> LifecycleStatus:
    allowed = TRANSITIONS.get((current, action), frozenset())
    if target not in allowed:
        raise LifecycleError(f"{action.value} não é permitido a partir de {current.value}")
    return target


def status_after_signature(effective_start: date, today: date) -> LifecycleStatus:
    """Assinado com início futuro aguarda a vigência; caso contrário, já vige."""
    return _S.AWAITING_START if effective_start > today else _S.ACTIVE


def is_in_force(start: date | None, end: date | None, on: date) -> bool:
    """Limites inclusivos; término ``None`` significa prazo indeterminado."""
    if start is None or on < start:
        return False
    return end is None or on <= end


def due_transition(
    status: LifecycleStatus, start: date | None, end: date | None, today: date
) -> tuple[LifecycleAction, LifecycleStatus] | None:
    """Transição automática devida na data (para o agendador), ou ``None``."""
    if status is _S.AWAITING_START and start is not None and start <= today:
        # Se o término também já passou (agendador indisponível), a expiração
        # é devolvida na chamada seguinte: o histórico registra as duas transições.
        return (_A.START, _S.ACTIVE)
    if status in {_S.ACTIVE, _S.SUSPENDED} and end is not None and end < today:
        return (_A.EXPIRE, _S.ENDED)
    return None


# ---------------------------------------------------------------------------
# Condições e aditivos
# ---------------------------------------------------------------------------
class ConditionField(StrEnum):
    TOTAL_VALUE = "total_value"
    EFFECTIVE_END = "effective_end"
    DESCRIPTION = "description"


@dataclass(frozen=True, slots=True)
class Conditions:
    total_value: Decimal | None
    effective_end: date | None
    description: str


@dataclass(frozen=True, slots=True)
class ConditionChange:
    """Alteração introduzida por um aditivo assinado."""

    sequence: int  # ordem de registro, para desempate determinístico
    field: ConditionField
    value: Decimal | date | str | None
    effective_from: date


def conditions_at(original: Conditions, changes: Iterable[ConditionChange], on: date) -> Conditions:
    """Condições vigentes em ``on``: original + alterações efetivas até a data.

    Alterações com mesma data de efeito são aplicadas na ordem de registro;
    a última prevalece. Alterações futuras são ignoradas.
    """
    current = original
    effective = sorted(
        (c for c in changes if c.effective_from <= on), key=lambda c: (c.effective_from, c.sequence)
    )
    for change in effective:
        match change.field:
            case ConditionField.TOTAL_VALUE:
                if change.value is not None and not isinstance(change.value, Decimal):
                    raise LifecycleError("valor total deve ser decimal")
                current = replace(current, total_value=change.value)
            case ConditionField.EFFECTIVE_END:
                if change.value is not None and not isinstance(change.value, date):
                    raise LifecycleError("término deve ser data")
                current = replace(current, effective_end=change.value)
            case ConditionField.DESCRIPTION:
                current = replace(current, description=str(change.value or ""))
    return current
