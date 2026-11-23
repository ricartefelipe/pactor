"""Workflows: validação de definição, condições e avaliação de rodadas."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pactor.approvals.domain import (
    MAX_APPROVERS_PER_STEP,
    MAX_LIST_VALUES,
    MAX_RULES,
    MAX_STEPS,
    CastVote,
    Condition,
    Decision,
    Operator,
    RoundOutcome,
    Rule,
    StepDefinition,
    StepSlots,
    StepState,
    WorkflowDefinitionError,
    WorkflowRules,
    evaluate_round,
    facts_from_metadata,
    parse_rules,
)

A, B, C, D = (uuid.UUID(int=i) for i in range(1, 5))


def _step(
    key: str, approvers: list[uuid.UUID], *, order: int = 1, quorum: int = 1
) -> dict[str, object]:
    return {
        "key": key,
        "name": key,
        "order": order,
        "quorum": quorum,
        "approver_ids": [str(a) for a in approvers],
    }


def _rules(*extra: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [*extra, {"name": "Padrão", "conditions": [], "steps": [_step("gestor", [A])]}]


HIGH_VALUE = {
    "name": "Alto valor",
    "conditions": [
        {"field": "total_value", "operator": "gt", "value": "50000.00"},
        {"field": "contract_type", "operator": "in", "value": ["services", "supply"]},
    ],
    "steps": [_step("juridico", [A, B], quorum=2), _step("financeiro", [C], order=2)],
}


def test_rule_selection_is_ordered_and_falls_back_to_default() -> None:
    rules = parse_rules(_rules(HIGH_VALUE))
    high: dict[str, str | Decimal | None] = {
        "total_value": Decimal("50000.01"),
        "contract_type": "services",
    }
    assert rules.select(high)[1].name == "Alto valor"
    assert (
        rules.select({"total_value": Decimal("50000.00"), "contract_type": "services"})[1].name
        == "Padrão"
    )
    assert rules.select({"total_value": None, "contract_type": "services"})[0] == 1


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ([], "1 a"),
        (
            [
                {
                    "name": "X",
                    "conditions": [{"field": "total_value", "operator": "gt", "value": "1"}],
                    "steps": [_step("a", [A])],
                }
            ],
            "padrão",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "__class__", "operator": "eq", "value": "x"}],
                    "steps": [_step("a", [A])],
                }
            ),
            "campo não permitido",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "contract_type", "operator": "gt", "value": "x"}],
                    "steps": [_step("a", [A])],
                }
            ),
            "não aceito",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "contract_type", "operator": "exec", "value": "x"}],
                    "steps": [_step("a", [A])],
                }
            ),
            "desconhecido",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "total_value", "operator": "gt", "value": "Infinity"}],
                    "steps": [_step("a", [A])],
                }
            ),
            "finito",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "total_value", "operator": "gt", "value": "abc"}],
                    "steps": [_step("a", [A])],
                }
            ),
            "inválido",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "total_value", "operator": "gt", "value": True}],
                    "steps": [_step("a", [A])],
                }
            ),
            "decimal",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "classification", "operator": "in", "value": []}],
                    "steps": [_step("a", [A])],
                }
            ),
            "lista",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "classification", "operator": "eq", "value": 3}],
                    "steps": [_step("a", [A])],
                }
            ),
            "texto",
        ),
        (_rules({"name": "X", "conditions": [], "steps": [_step("a", [A], quorum=2)]}), "quórum"),
        (_rules({"name": "X", "conditions": [], "steps": [_step("a", [A, A])]}), "repetido"),
        (
            _rules({"name": "X", "conditions": [], "steps": [_step("a", [A]), _step("a", [B])]}),
            "repetidas",
        ),
        (_rules({"name": "X", "conditions": [], "steps": [_step("a b", [A])]}), "alfanumérica"),
        (_rules({"name": "X", "conditions": [], "steps": []}), "etapas"),
        (_rules({"name": "", "conditions": [], "steps": [_step("a", [A])]}), "nome"),
        (
            _rules({"name": "X", "conditions": [], "steps": [{**_step("a", [A]), "order": 0}]}),
            "ordem",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [],
                    "steps": [{**_step("a", [A]), "approver_ids": ["x"]}],
                }
            ),
            "identificador",
        ),
    ],
)
def test_invalid_definitions_are_rejected(definition: object, message: str) -> None:
    with pytest.raises(WorkflowDefinitionError, match=message):
        parse_rules(definition)


def test_expression_like_values_are_plain_text() -> None:
    condition = Condition("classification", Operator.EQ, "__import__('os').system('x')")
    assert condition.matches({"classification": "__import__('os').system('x')"})
    assert not condition.matches({"classification": "outra"})


@given(
    st.decimals(allow_nan=False, allow_infinity=False, places=2),
    st.decimals(allow_nan=False, allow_infinity=False, places=2),
)
def test_property_decimal_operators_are_consistent(actual: Decimal, threshold: Decimal) -> None:
    facts = {"total_value": actual}
    results = {
        op: Condition("total_value", op, threshold).matches(facts)
        for op in (Operator.GT, Operator.LTE, Operator.EQ, Operator.NE, Operator.GTE, Operator.LT)
    }
    assert results[Operator.GT] != results[Operator.LTE]
    assert results[Operator.EQ] != results[Operator.NE]
    assert results[Operator.GTE] != results[Operator.LT]
    assert not Condition("total_value", Operator.GT, threshold).matches({"total_value": None})


def test_text_operators_and_missing_facts() -> None:
    assert Condition("contract_type", Operator.NOT_IN, ("nda",)).matches(
        {"contract_type": "services"}
    )
    assert not Condition("contract_type", Operator.IN, ("nda",)).matches(
        {"contract_type": "services"}
    )
    assert Condition("contract_type", Operator.NE, "nda").matches({"contract_type": "services"})
    assert not Condition("total_value", Operator.GT, Decimal(1)).matches({"total_value": "texto"})


def test_facts_from_revision_metadata() -> None:
    facts = facts_from_metadata(
        {
            "total_value": "10.50",
            "contract_type": "nda",
            "department_id": None,
            "classification": "",
        }
    )
    assert facts == {
        "total_value": Decimal("10.50"),
        "contract_type": "nda",
        "department_id": None,
        "classification": None,
        "confidentiality": None,
    }


def _slots(*steps: StepSlots) -> list[StepSlots]:
    return list(steps)


def _slot(
    key: str,
    approvers: set[uuid.UUID],
    *,
    order: int = 1,
    quorum: int = 1,
    eligible: set[uuid.UUID] | None = None,
) -> StepSlots:
    return StepSlots(
        key,
        order,
        quorum,
        frozenset(approvers),
        frozenset(eligible if eligible is not None else approvers),
    )


def test_sequential_groups_and_parallel_steps() -> None:
    steps = _slots(
        _slot("juridico", {A}), _slot("compliance", {B}), _slot("financeiro", {C}, order=2)
    )
    start = evaluate_round(steps, [])
    assert start.outcome is RoundOutcome.OPEN
    assert start.active_steps() == {"juridico", "compliance"}
    assert start.steps["financeiro"] is StepState.WAITING
    half = evaluate_round(steps, [CastVote("juridico", A, Decision.APPROVE)])
    assert half.active_steps() == {"compliance"}
    both = evaluate_round(
        steps,
        [CastVote("juridico", A, Decision.APPROVE), CastVote("compliance", B, Decision.APPROVE)],
    )
    assert both.active_steps() == {"financeiro"}
    done = evaluate_round(
        steps,
        [
            CastVote("juridico", A, Decision.APPROVE),
            CastVote("compliance", B, Decision.APPROVE),
            CastVote("financeiro", C, Decision.APPROVE),
        ],
    )
    assert done.outcome is RoundOutcome.APPROVED


def test_quorum_counts_distinct_slots_once() -> None:
    steps = _slots(_slot("comite", {A, B, C}, quorum=2))
    repeated = [CastVote("comite", A, Decision.APPROVE), CastVote("comite", A, Decision.APPROVE)]
    assert evaluate_round(steps, repeated).outcome is RoundOutcome.OPEN
    outsider = [CastVote("comite", A, Decision.APPROVE), CastVote("comite", D, Decision.APPROVE)]
    assert evaluate_round(steps, outsider).outcome is RoundOutcome.OPEN
    assert (
        evaluate_round(
            steps,
            [CastVote("comite", A, Decision.APPROVE), CastVote("comite", B, Decision.APPROVE)],
        ).outcome
        is RoundOutcome.APPROVED
    )


def test_rejection_and_change_requests_close_round() -> None:
    steps = _slots(_slot("comite", {A, B}, quorum=1))
    assert (
        evaluate_round(steps, [CastVote("comite", A, Decision.REJECT)]).outcome
        is RoundOutcome.REJECTED
    )
    assert (
        evaluate_round(steps, [CastVote("comite", B, Decision.REQUEST_CHANGES)]).outcome
        is RoundOutcome.CHANGES_REQUESTED
    )


def test_step_without_enough_eligible_approvers_is_blocked() -> None:
    steps = _slots(_slot("comite", {A, B}, quorum=2, eligible={A}))
    evaluation = evaluate_round(steps, [CastVote("comite", A, Decision.APPROVE)])
    assert evaluation.steps["comite"] is StepState.BLOCKED
    assert evaluation.outcome is RoundOutcome.OPEN


@given(
    st.lists(st.tuples(st.sampled_from([A, B, C, D]), st.sampled_from(list(Decision))), max_size=8)
)
def test_property_approval_requires_quorum_of_distinct_slot_approvals(
    votes: list[tuple[uuid.UUID, Decision]],
) -> None:
    steps = _slots(_slot("comite", {A, B, C}, quorum=2))
    cast = [CastVote("comite", approver, decision) for approver, decision in votes]
    outcome = evaluate_round(steps, cast).outcome
    first_by_slot: dict[uuid.UUID, Decision] = {}
    for approver, decision in votes:
        if approver in {A, B, C}:
            first_by_slot.setdefault(approver, decision)
    if outcome is RoundOutcome.APPROVED:
        assert sum(d is Decision.APPROVE for d in first_by_slot.values()) >= 2
        assert Decision.REJECT not in first_by_slot.values()


def test_waiting_parallel_steps_all_report_state() -> None:
    steps = _slots(_slot("a", {A}), _slot("b", {B}, order=2), _slot("c", {C}, order=2))
    evaluation = evaluate_round(steps, [])
    assert dict(evaluation.steps) == {
        "a": StepState.ACTIVE,
        "b": StepState.WAITING,
        "c": StepState.WAITING,
    }
    approved_first = evaluate_round(steps, [CastVote("a", A, Decision.APPROVE)])
    assert dict(approved_first.steps) == {
        "a": StepState.APPROVED,
        "b": StepState.ACTIVE,
        "c": StepState.ACTIVE,
    }


def test_closing_decisions_report_step_states() -> None:
    steps = _slots(_slot("a", {A}), _slot("b", {B}, order=2))
    rejected = evaluate_round(
        steps, [CastVote("a", A, Decision.APPROVE), CastVote("b", B, Decision.REJECT)]
    )
    assert dict(rejected.steps) == {"a": StepState.APPROVED, "b": StepState.ACTIVE}
    changes = evaluate_round(steps, [CastVote("a", A, Decision.REQUEST_CHANGES)])
    assert dict(changes.steps) == {"a": StepState.ACTIVE}


def test_select_without_matching_rule_fails_explicitly() -> None:
    only_conditional = WorkflowRules(
        rules=(Rule("X", (Condition("contract_type", Operator.EQ, "nda"),), ()),)
    )
    with pytest.raises(WorkflowDefinitionError, match="regra padrão"):
        only_conditional.select({"contract_type": "services"})


def test_parsed_definition_has_exact_structure() -> None:
    parsed = parse_rules(_rules(HIGH_VALUE))
    high = parsed.rules[0]
    assert high.conditions == (
        Condition("total_value", Operator.GT, Decimal("50000.00")),
        Condition("contract_type", Operator.IN, ("services", "supply")),
    )
    assert high.steps == (
        StepDefinition("juridico", "juridico", 1, 2, (A, B)),
        StepDefinition("financeiro", "financeiro", 2, 1, (C,)),
    )
    eq = parse_rules(
        [
            {
                "name": "P",
                "conditions": [{"field": "classification", "operator": "eq", "value": "alto"}],
                "steps": [_step("s", [A])],
            },
            *_rules(),
        ]
    )
    assert eq.rules[0].conditions[0].value == "alto"
    assert eq.rules[0].name == "P"


def test_rule_without_conditions_key_is_default() -> None:
    parsed = parse_rules([{"name": "Padrão", "steps": [{**_step("s", [A]), "name": "  Gestão  "}]}])
    assert parsed.rules[0].conditions == ()
    assert parsed.rules[0].steps[0].name == "Gestão"


def test_error_messages_point_to_rule_and_step() -> None:
    bad = [
        {
            "name": "X",
            "conditions": [],
            "steps": [_step("a", [A]), {**_step("b", [A]), "quorum": 0}],
        }
    ]
    with pytest.raises(WorkflowDefinitionError, match=r"^regra 1, etapa 2: quórum"):
        parse_rules(bad)
    bad_condition = _rules(
        {
            "name": "X",
            "conditions": [{"field": "x", "operator": "eq", "value": "1"}],
            "steps": [_step("a", [A])],
        }
    )
    with pytest.raises(WorkflowDefinitionError, match=r"^regra 1, condição 1: campo"):
        parse_rules(bad_condition)
    second = [*_rules(), {"name": "", "steps": [_step("a", [A])]}]
    with pytest.raises(WorkflowDefinitionError, match=r"^regra 2: nome"):
        parse_rules(second)


@pytest.mark.parametrize("value", [1.5, None, [], {}])
def test_decimal_condition_rejects_non_textual_numbers(value: object) -> None:
    with pytest.raises(WorkflowDefinitionError, match=r"^regra 1, condição 1: valor decimal"):
        parse_rules(
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "total_value", "operator": "gt", "value": value}],
                    "steps": [_step("a", [A])],
                }
            )
        )


def test_integer_decimal_values_are_accepted() -> None:
    parsed = parse_rules(
        _rules(
            {
                "name": "X",
                "conditions": [{"field": "total_value", "operator": "gt", "value": 10}],
                "steps": [_step("a", [A])],
            }
        )
    )
    assert parsed.rules[0].conditions[0].value == Decimal(10)


@pytest.mark.parametrize("field", ["order", "quorum"])
def test_boolean_is_not_accepted_as_integer(field: str) -> None:
    with pytest.raises(WorkflowDefinitionError):
        parse_rules([{"name": "P", "steps": [{**_step("a", [A]), field: True}]}])


def test_non_text_step_name_is_rejected() -> None:
    with pytest.raises(WorkflowDefinitionError, match="nome da etapa"):
        parse_rules([{"name": "P", "steps": [{**_step("a", [A]), "name": 5}]}])


def test_limits_are_inclusive() -> None:
    many = [uuid.UUID(int=i) for i in range(1, MAX_APPROVERS_PER_STEP + 2)]
    assert (
        len(
            parse_rules([{"name": "P", "steps": [_step("a", many[:-1])]}])
            .rules[0]
            .steps[0]
            .approver_ids
        )
        == MAX_APPROVERS_PER_STEP
    )
    with pytest.raises(WorkflowDefinitionError, match="aprovadores"):
        parse_rules([{"name": "P", "steps": [_step("a", many)]}])
    with pytest.raises(WorkflowDefinitionError, match="aprovadores"):
        parse_rules([{"name": "P", "steps": [_step("a", [])]}])
    values = [f"v{i}" for i in range(MAX_LIST_VALUES + 1)]
    ok = {
        "name": "X",
        "conditions": [{"field": "classification", "operator": "in", "value": values[:-1]}],
        "steps": [_step("a", [A])],
    }
    accepted = parse_rules(_rules(ok)).rules[0].conditions[0].value
    assert isinstance(accepted, tuple)
    assert len(accepted) == MAX_LIST_VALUES
    single = {
        "name": "X",
        "conditions": [{"field": "classification", "operator": "in", "value": ["um"]}],
        "steps": [_step("a", [A])],
    }
    assert parse_rules(_rules(single)).rules[0].conditions[0].value == ("um",)
    too_many = {
        "name": "X",
        "conditions": [{"field": "classification", "operator": "in", "value": values}],
        "steps": [_step("a", [A])],
    }
    with pytest.raises(WorkflowDefinitionError, match="lista"):
        parse_rules(_rules(too_many))
    key_ok = "k" * 40
    assert (
        parse_rules([{"name": "P", "steps": [_step(key_ok, [A])]}]).rules[0].steps[0].key == key_ok
    )
    with pytest.raises(WorkflowDefinitionError, match="chave"):
        parse_rules([{"name": "P", "steps": [_step("k" * 41, [A])]}])
    steps = [_step(f"s{i}", [A], order=i + 1) for i in range(MAX_STEPS + 1)]
    assert len(parse_rules([{"name": "P", "steps": steps[:-1]}]).rules[0].steps) == MAX_STEPS
    with pytest.raises(WorkflowDefinitionError, match="etapas"):
        parse_rules([{"name": "P", "steps": steps}])
    rule = {
        "name": "R",
        "conditions": [{"field": "classification", "operator": "eq", "value": "x"}],
        "steps": [_step("a", [A])],
    }
    rules = [rule] * (MAX_RULES - 1) + [{"name": "P", "steps": [_step("a", [A])]}]
    assert len(parse_rules(rules).rules) == MAX_RULES
    with pytest.raises(WorkflowDefinitionError, match="regras"):
        parse_rules([rule, *rules])


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ([{"name": "P", "steps": [{**_step("a", [A]), "key": 5}]}], r"^regra 1, etapa 1: chave"),
        ([{"name": "P", "steps": ["etapa"]}], r"^regra 1, etapa 1: etapa deve ser objeto$"),
        (["regra"], r"^regra 1: regra deve ser objeto$"),
        (
            [{"name": "P", "conditions": "x", "steps": [_step("a", [A])]}],
            r"^regra 1: condições devem ser lista$",
        ),
        (
            _rules({"name": "X", "conditions": ["c"], "steps": [_step("a", [A])]}),
            r"^regra 1, condição 1: condição deve ser objeto$",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [
                        {"field": "classification", "operator": "in", "value": ["a", 1]}
                    ],
                    "steps": [_step("a", [A])],
                }
            ),
            r"^regra 1, condição 1: lista deve conter apenas textos$",
        ),
        (
            _rules(
                {
                    "name": "X",
                    "conditions": [{"field": "classification", "operator": "in", "value": "a"}],
                    "steps": [_step("a", [A])],
                }
            ),
            r"^regra 1, condição 1: lista de 1 a",
        ),
    ],
)
def test_structural_errors_have_location_and_reason(definition: object, message: str) -> None:
    with pytest.raises(WorkflowDefinitionError, match=message):
        parse_rules(definition)


def test_step_keys_accept_underscores() -> None:
    parsed = parse_rules([{"name": "P", "steps": [_step("juridico_externo", [A])]}])
    assert parsed.rules[0].steps[0].key == "juridico_externo"
