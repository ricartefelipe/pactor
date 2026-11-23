"""Modelos como dados: validação, renderização segura e dados de geração."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pactor.drafting.domain import (
    FieldValue,
    GenerationDataError,
    TemplateDefinitionError,
    canonical_data,
    format_value,
    parse_definition,
    render,
    validate_data,
)
from pactor.intake.domain import RequestState, can_transition

DEFINITION: dict[str, list[dict[str, object]]] = {
    "fields": [
        {"key": "contratante", "label": "Contratante", "type": "text", "required": True},
        {"key": "valor", "label": "Valor mensal", "type": "decimal", "required": True},
        {"key": "inicio", "label": "Início", "type": "date", "required": True},
        {
            "key": "foro",
            "label": "Foro",
            "type": "choice",
            "choices": ["São Paulo", "Rio de Janeiro"],
            "required": False,
        },
        {"key": "confidencial", "label": "Cláusula de confidencialidade", "type": "boolean"},
    ],
    "sections": [
        {
            "key": "objeto",
            "title": "Objeto",
            "body": "{{contratante}} contrata serviços por R$ {{ valor }} a partir de {{inicio}}.",
        },
        {
            "key": "sigilo",
            "title": "Confidencialidade",
            "body": "As partes manterão sigilo.",
            "optional_when": "confidencial",
        },
        {"key": "foro", "title": "Foro", "body": "Fica eleito o foro de {{foro}}."},
    ],
}


def test_render_substitutes_declared_fields_and_formats_values() -> None:
    definition = parse_definition(DEFINITION)
    values = validate_data(
        definition,
        {
            "contratante": "Empresa Fictícia Ltda",
            "valor": "12345.6",
            "inicio": "2027-02-01",
            "foro": "São Paulo",
            "confidencial": True,
        },
    )
    text = render(definition, values)
    assert (
        "Empresa Fictícia Ltda contrata serviços por R$ 12.345,60 a partir de 01/02/2027." in text
    )
    assert "CONFIDENCIALIDADE" in text
    assert "{{" not in text
    without = render(definition, {**values, "confidencial": False})
    assert "CONFIDENCIALIDADE" not in without


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("Olá {{desconhecido}}", "não declaradas"),
        ("{{ __class__ }}", "malformado"),
        ("{{contratante|upper}}", "malformado"),
        ("{% if x %}texto{% endif %} {{", "malformado"),
        ("{{contratante.__init__}}", "malformado"),
    ],
)
def test_template_syntax_beyond_placeholders_is_rejected(body: str, message: str) -> None:
    definition = {
        "fields": DEFINITION["fields"],
        "sections": [{"key": "s", "title": "S", "body": body}],
    }
    with pytest.raises(TemplateDefinitionError, match=message):
        parse_definition(definition)


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ([], "objeto"),
        ({"fields": [], "sections": []}, "seções"),
        (
            {
                "fields": [{"key": "A", "label": "x", "type": "text"}],
                "sections": [{"key": "s", "title": "S", "body": "x"}],
            },
            "chave",
        ),
        (
            {
                "fields": [{"key": "a", "label": "", "type": "text"}],
                "sections": [{"key": "s", "title": "S", "body": "x"}],
            },
            "rótulo",
        ),
        (
            {
                "fields": [{"key": "a", "label": "A", "type": "script"}],
                "sections": [{"key": "s", "title": "S", "body": "x"}],
            },
            "tipo",
        ),
        (
            {
                "fields": [{"key": "a", "label": "A", "type": "choice"}],
                "sections": [{"key": "s", "title": "S", "body": "x"}],
            },
            "opções",
        ),
        (
            {
                "fields": [{"key": "a", "label": "A", "type": "text", "required": "sim"}],
                "sections": [{"key": "s", "title": "S", "body": "x"}],
            },
            "booleano",
        ),
        (
            {
                "fields": [{"key": "a", "label": "A", "type": "text"}] * 2,
                "sections": [{"key": "s", "title": "S", "body": "x"}],
            },
            "repetidas",
        ),
        ({"fields": [], "sections": [{"key": "s", "title": "S", "body": "x"}] * 2}, "repetidas"),
        ({"fields": [], "sections": [{"key": "s", "title": "", "body": "x"}]}, "título"),
        ({"fields": [], "sections": [{"key": "s", "title": "S", "body": " "}]}, "texto"),
        (
            {
                "fields": [],
                "sections": [{"key": "s", "title": "S", "body": "x", "optional_when": "nada"}],
            },
            "optional_when",
        ),
        (
            {
                "fields": [{"key": "a", "label": "A", "type": "text"}],
                "sections": [{"key": "s", "title": "S", "body": "x", "optional_when": "a"}],
            },
            "booleano",
        ),
        ({"fields": ["x"], "sections": [{"key": "s", "title": "S", "body": "x"}]}, "objeto"),
        ({"fields": [], "sections": ["x"]}, "objeto"),
    ],
)
def test_invalid_definitions(definition: object, message: str) -> None:
    with pytest.raises(TemplateDefinitionError, match=message):
        parse_definition(definition)


def test_generation_data_is_validated_per_field() -> None:
    definition = parse_definition(DEFINITION)
    with pytest.raises(GenerationDataError) as excinfo:
        validate_data(
            definition,
            {
                "valor": "NaN",
                "inicio": "31/12/2027",
                "foro": "Lisboa",
                "confidencial": "sim",
                "extra": "x",
            },
        )
    errors = excinfo.value.errors
    assert errors["contratante"] == "obrigatório"
    assert errors["valor"] == "decimal deve ser finito"
    assert "inicio" in errors
    assert errors["foro"] == "opção não permitida"
    assert errors["confidencial"] == "verdadeiro ou falso"
    assert errors["extra"] == "campo não declarado no modelo"
    with pytest.raises(GenerationDataError, match="decimal em texto"):
        validate_data(definition, {"contratante": "x", "valor": 10.5, "inicio": "2027-01-01"})
    with pytest.raises(GenerationDataError, match="caracteres"):
        validate_data(definition, {"contratante": "x" * 2001, "valor": "1", "inicio": "2027-01-01"})


def test_canonical_data_is_json_stable() -> None:
    values: dict[str, FieldValue] = {
        "b": Decimal("10.50"),
        "a": date(2027, 1, 2),
        "c": True,
        "d": "texto",
    }
    assert canonical_data(values) == {"a": "2027-01-02", "b": "10.50", "c": True, "d": "texto"}
    assert list(canonical_data(values)) == ["a", "b", "c", "d"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), "0,00"),
        (Decimal("1234567.891"), "1.234.567,89"),
        (Decimal("-1000"), "-1.000,00"),
        (False, "Não"),
    ],
)
def test_format_value(value: Decimal | bool, expected: str) -> None:
    assert format_value(value) == expected


@given(st.text(max_size=200))
def test_property_user_values_are_inserted_literally(value: str) -> None:
    """Valores são substituídos em uma única passagem: chaves no valor nunca são avaliadas."""
    definition = parse_definition(
        {
            "fields": [{"key": "nome", "label": "Nome", "type": "text"}],
            "sections": [{"key": "s", "title": "S", "body": "[{{nome}}]"}],
        }
    )
    rendered = render(
        definition, validate_data(definition, {"nome": value}) if value.strip() else {}
    )
    assert rendered.endswith(f"[{value.strip()}]") if value.strip() else rendered.endswith("[]")


def test_request_transitions() -> None:
    assert can_transition(RequestState.SUBMITTED, RequestState.IN_REVIEW)
    assert can_transition(RequestState.IN_REVIEW, RequestState.CONVERTED)
    assert not can_transition(RequestState.SUBMITTED, RequestState.CONVERTED)
    assert not can_transition(RequestState.CONVERTED, RequestState.CANCELLED)
