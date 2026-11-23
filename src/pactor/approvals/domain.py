"""Regras puras de workflows de aprovação.

Condições são dados validados: campo permitido, operador permitido e valor do
tipo esperado. Não há avaliação de expressões, chamadas de função nem
templates; a avaliação é determinística e total (todo campo tem valor ou
``None``, e operadores sobre ``None`` resultam em falso).

Rodadas: etapas com o mesmo ``order`` correm em paralelo; grupos de ``order``
crescente são sequenciais. Uma etapa é aprovada quando o número de
aprovadores distintos que aprovaram atinge o quórum. Cada vaga de aprovador
conta no máximo uma vez, seja o voto direto ou por delegação.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType


class WorkflowDefinitionError(ValueError):
    """Definição de workflow inválida; a mensagem indica o ponto do problema."""


class FieldKind(StrEnum):
    TEXT = "text"
    DECIMAL = "decimal"


class Operator(StrEnum):
    EQ = "eq"
    NE = "ne"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


ALLOWED_FIELDS: Mapping[str, FieldKind] = MappingProxyType(
    {
        "contract_type": FieldKind.TEXT,
        "department_id": FieldKind.TEXT,
        "classification": FieldKind.TEXT,
        "confidentiality": FieldKind.TEXT,
        "total_value": FieldKind.DECIMAL,
    }
)

_OPERATORS_BY_KIND: Mapping[FieldKind, frozenset[Operator]] = MappingProxyType(
    {
        FieldKind.TEXT: frozenset({Operator.EQ, Operator.NE, Operator.IN, Operator.NOT_IN}),
        FieldKind.DECIMAL: frozenset(
            {Operator.EQ, Operator.NE, Operator.GT, Operator.GTE, Operator.LT, Operator.LTE}
        ),
    }
)

MAX_RULES = 50
MAX_STEPS = 10
MAX_APPROVERS_PER_STEP = 25

type Scalar = str | Decimal
type ConditionValue = Scalar | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Condition:
    field: str
    operator: Operator
    value: ConditionValue

    def matches(self, facts: Mapping[str, Scalar | None]) -> bool:
        actual = facts.get(self.field)
        if actual is None:
            return False
        if isinstance(self.value, tuple):
            contained = isinstance(actual, str) and actual in self.value
            return contained if self.operator is Operator.IN else not contained
        if self.operator in _EQUALITY:
            return (actual == self.value) is (self.operator is Operator.EQ)
        if isinstance(actual, Decimal) and isinstance(self.value, Decimal):
            return _ORDERING[self.operator](actual, self.value)
        return False


_EQUALITY = frozenset({Operator.EQ, Operator.NE})
_ORDERING: Mapping[Operator, Callable[[Decimal, Decimal], bool]] = MappingProxyType(
    {
        Operator.GT: lambda a, b: a > b,
        Operator.GTE: lambda a, b: a >= b,
        Operator.LT: lambda a, b: a < b,
        Operator.LTE: lambda a, b: a <= b,
    }
)


@dataclass(frozen=True, slots=True)
class StepDefinition:
    key: str
    name: str
    order: int
    quorum: int
    approver_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    conditions: tuple[Condition, ...]
    steps: tuple[StepDefinition, ...]

    def matches(self, facts: Mapping[str, Scalar | None]) -> bool:
        return all(condition.matches(facts) for condition in self.conditions)


@dataclass(frozen=True, slots=True)
class WorkflowRules:
    rules: tuple[Rule, ...]

    def select(self, facts: Mapping[str, Scalar | None]) -> tuple[int, Rule]:
        """Primeira regra (em ordem) cujas condições casam. A última é o padrão."""
        for index, rule in enumerate(self.rules):
            if rule.matches(facts):
                return index, rule
        raise WorkflowDefinitionError(
            "Nenhuma regra aplicável; defina uma regra padrão sem condições."
        )


# ---------------------------------------------------------------------------
# Leitura e validação da definição (JSON armazenado)
# ---------------------------------------------------------------------------
MAX_LIST_VALUES = 50
_KEY_MAX_LENGTH = 40


def _fail(where: str, message: str) -> WorkflowDefinitionError:
    return WorkflowDefinitionError(f"{where}: {message}")


def _parse_decimal(raw: object, where: str) -> Decimal:
    if not isinstance(raw, str | int) or isinstance(raw, bool):
        raise _fail(where, "valor decimal deve ser texto ou inteiro")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise _fail(where, "decimal inválido") from exc
    if not value.is_finite():
        raise _fail(where, "decimal deve ser finito")
    return value


def _parse_text_list(raw: object, where: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_LIST_VALUES:
        raise _fail(where, f"lista de 1 a {MAX_LIST_VALUES} textos obrigatória")
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise _fail(where, "lista deve conter apenas textos")
        values.append(item)
    return tuple(sorted(values))


def parse_condition(raw: object, where: str) -> Condition:
    if not isinstance(raw, Mapping):
        raise _fail(where, "condição deve ser objeto")
    field = raw.get("field")
    if not isinstance(field, str) or field not in ALLOWED_FIELDS:
        raise _fail(where, f"campo não permitido: {field!r}")
    try:
        operator = Operator(str(raw.get("operator")))
    except ValueError as exc:
        raise _fail(where, "operador desconhecido") from exc
    kind = ALLOWED_FIELDS[field]
    if operator not in _OPERATORS_BY_KIND[kind]:
        raise _fail(where, f"operador {operator.value} não aceito para {field}")
    raw_value = raw.get("value")
    value: ConditionValue
    if operator in {Operator.IN, Operator.NOT_IN}:
        value = _parse_text_list(raw_value, where)
    elif kind is FieldKind.DECIMAL:
        value = _parse_decimal(raw_value, where)
    elif isinstance(raw_value, str):
        value = raw_value
    else:
        raise _fail(where, "texto obrigatório")
    return Condition(field=field, operator=operator, value=value)


def _parse_uuid_list(raw: object, where: str) -> tuple[uuid.UUID, ...]:
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_APPROVERS_PER_STEP:
        raise _fail(where, f"informe de 1 a {MAX_APPROVERS_PER_STEP} aprovadores")
    parsed: list[uuid.UUID] = []
    for item in raw:
        try:
            parsed.append(uuid.UUID(str(item)))
        except ValueError as exc:
            raise _fail(where, "identificador de aprovador inválido") from exc
    if len(set(parsed)) != len(parsed):
        raise _fail(where, "aprovador repetido na etapa")
    return tuple(parsed)


def parse_step(raw: object, where: str) -> StepDefinition:
    if not isinstance(raw, Mapping):
        raise _fail(where, "etapa deve ser objeto")
    key, name, order, quorum = raw.get("key"), raw.get("name"), raw.get("order"), raw.get("quorum")
    if (
        not isinstance(key, str)
        or not key
        or len(key) > _KEY_MAX_LENGTH
        or not key.replace("_", "").isalnum()
    ):
        raise _fail(where, "chave da etapa deve ser alfanumérica (com _), até 40 caracteres")
    if not isinstance(name, str) or not name.strip():
        raise _fail(where, "nome da etapa obrigatório")
    if not isinstance(order, int) or isinstance(order, bool) or order < 1:
        raise _fail(where, "ordem deve ser inteiro positivo")
    approvers = _parse_uuid_list(raw.get("approver_ids"), where)
    if not isinstance(quorum, int) or isinstance(quorum, bool) or not 1 <= quorum <= len(approvers):
        raise _fail(where, "quórum deve estar entre 1 e o número de aprovadores")
    return StepDefinition(
        key=key, name=name.strip(), order=order, quorum=quorum, approver_ids=approvers
    )


def parse_rules(raw: object) -> WorkflowRules:
    """Valida a definição completa; a última regra deve ser o padrão (sem condições)."""
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_RULES:
        raise WorkflowDefinitionError(f"Informe de 1 a {MAX_RULES} regras.")
    rules: list[Rule] = []
    for index, raw_rule in enumerate(raw):
        where = f"regra {index + 1}"
        if not isinstance(raw_rule, Mapping):
            raise _fail(where, "regra deve ser objeto")
        name = raw_rule.get("name")
        raw_conditions = raw_rule.get("conditions", [])
        raw_steps = raw_rule.get("steps")
        if not isinstance(name, str) or not name.strip():
            raise _fail(where, "nome obrigatório")
        if not isinstance(raw_conditions, list):
            raise _fail(where, "condições devem ser lista")
        if not isinstance(raw_steps, list) or not 0 < len(raw_steps) <= MAX_STEPS:
            raise _fail(where, f"informe de 1 a {MAX_STEPS} etapas")
        conditions = tuple(
            parse_condition(c, f"{where}, condição {i + 1}") for i, c in enumerate(raw_conditions)
        )
        steps = tuple(parse_step(s, f"{where}, etapa {i + 1}") for i, s in enumerate(raw_steps))
        if len({s.key for s in steps}) != len(steps):
            raise _fail(where, "chaves de etapa repetidas")
        rules.append(Rule(name=name.strip(), conditions=conditions, steps=steps))
    if rules[-1].conditions:
        raise WorkflowDefinitionError("A última regra deve ser a padrão, sem condições.")
    return WorkflowRules(rules=tuple(rules))


def facts_from_metadata(metadata: Mapping[str, object]) -> dict[str, Scalar | None]:
    """Fatos para as condições, extraídos do snapshot da revisão submetida."""
    facts: dict[str, Scalar | None] = {}
    for field, kind in ALLOWED_FIELDS.items():
        raw = metadata.get(field)
        if raw is None or raw == "":
            facts[field] = None
        elif kind is FieldKind.DECIMAL:
            facts[field] = Decimal(str(raw))
        else:
            facts[field] = str(raw)
    return facts


# ---------------------------------------------------------------------------
# Avaliação de rodadas
# ---------------------------------------------------------------------------
class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class StepState(StrEnum):
    WAITING = "waiting"
    ACTIVE = "active"
    APPROVED = "approved"
    BLOCKED = "blocked"


class RoundOutcome(StrEnum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


@dataclass(frozen=True, slots=True)
class StepSlots:
    """Etapa em andamento: vagas de aprovadores e quem ainda tem acesso."""

    key: str
    order: int
    quorum: int
    approver_ids: frozenset[uuid.UUID]
    eligible_ids: frozenset[uuid.UUID]


@dataclass(frozen=True, slots=True)
class CastVote:
    step_key: str
    approver_id: uuid.UUID
    decision: Decision


@dataclass(frozen=True, slots=True)
class RoundEvaluation:
    outcome: RoundOutcome
    steps: Mapping[str, StepState]

    def active_steps(self) -> frozenset[str]:
        return frozenset(k for k, state in self.steps.items() if state is StepState.ACTIVE)


def evaluate_round(steps: Sequence[StepSlots], votes: Iterable[CastVote]) -> RoundEvaluation:
    """Situação da rodada a partir das etapas e dos votos registrados.

    Votos de quem não ocupa vaga na etapa são ignorados (a validação de quem
    pode votar acontece antes do registro). Uma rejeição ou pedido de ajustes
    em etapa ativa encerra a rodada.
    """
    by_step: dict[str, dict[uuid.UUID, Decision]] = {s.key: {} for s in steps}
    slots = {s.key: s for s in steps}
    for vote in votes:
        step = slots.get(vote.step_key)
        if step is not None and vote.approver_id in step.approver_ids:
            by_step[vote.step_key].setdefault(vote.approver_id, vote.decision)
    states: dict[str, StepState] = {}
    previous_groups_approved = True
    for order in sorted({s.order for s in steps}):
        group = [s for s in steps if s.order == order]
        group_approved = True
        for step in group:
            decisions = by_step[step.key]
            if not previous_groups_approved:
                states[step.key] = StepState.WAITING
                group_approved = False
                continue
            if Decision.REJECT in decisions.values():
                return RoundEvaluation(
                    RoundOutcome.REJECTED, {**states, step.key: StepState.ACTIVE}
                )
            if Decision.REQUEST_CHANGES in decisions.values():
                return RoundEvaluation(
                    RoundOutcome.CHANGES_REQUESTED, {**states, step.key: StepState.ACTIVE}
                )
            approvals = sum(1 for d in decisions.values() if d is Decision.APPROVE)
            pending_eligible = len((step.eligible_ids & step.approver_ids) - decisions.keys())
            if approvals >= step.quorum:
                states[step.key] = StepState.APPROVED
            elif approvals + pending_eligible < step.quorum:
                states[step.key] = StepState.BLOCKED
                group_approved = False
            else:
                states[step.key] = StepState.ACTIVE
                group_approved = False
        previous_groups_approved = previous_groups_approved and group_approved
    outcome = RoundOutcome.APPROVED if previous_groups_approved else RoundOutcome.OPEN
    return RoundEvaluation(outcome, states)
