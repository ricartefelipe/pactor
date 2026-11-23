"""Modelos de contrato como dados controlados.

Uma definição tem campos tipados e seções de texto com marcadores
``{{chave}}``. A renderização substitui apenas marcadores de campos
declarados e inclui seções opcionais somente quando o campo booleano que as
controla é verdadeiro. Não há expressões, filtros, chamadas de função nem
qualquer avaliação de código enviado por usuários.

Os modelos organizam o texto produzido; não representam conteúdo jurídico
adequado a qualquer situação.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType

PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]{0,39})\s*\}\}")
_ANY_BRACES = re.compile(r"\{\{|\}\}")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_FIELDS = 60
MAX_SECTIONS = 80
MAX_SECTION_LENGTH = 20_000
MAX_TEXT_VALUE = 2_000


class TemplateDefinitionError(ValueError):
    """Definição de modelo inválida."""


class GenerationDataError(ValueError):
    """Dados de preenchimento inválidos; ``errors`` mapeia campo → mensagem."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = dict(errors)


class FieldType(StrEnum):
    TEXT = "text"
    DATE = "date"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    CHOICE = "choice"


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    key: str
    label: str
    type: FieldType
    required: bool
    choices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Section:
    key: str
    title: str
    body: str
    optional_when: str | None = None


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    fields: tuple[FieldDefinition, ...]
    sections: tuple[Section, ...]

    @property
    def field_map(self) -> Mapping[str, FieldDefinition]:
        return {f.key: f for f in self.fields}


type FieldValue = str | bool | Decimal | date


def _fail(where: str, message: str) -> TemplateDefinitionError:
    return TemplateDefinitionError(f"{where}: {message}")


def _parse_field(raw: object, where: str) -> FieldDefinition:
    if not isinstance(raw, Mapping):
        raise _fail(where, "campo deve ser objeto")
    key, label, kind = raw.get("key"), raw.get("label"), raw.get("type")
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise _fail(where, "chave deve começar por letra minúscula e conter apenas a-z, 0-9 e _")
    if not isinstance(label, str) or not label.strip():
        raise _fail(where, "rótulo obrigatório")
    try:
        field_type = FieldType(str(kind))
    except ValueError as exc:
        raise _fail(where, "tipo de campo desconhecido") from exc
    required = raw.get("required", False)
    if not isinstance(required, bool):
        raise _fail(where, "required deve ser booleano")
    choices: tuple[str, ...] = ()
    if field_type is FieldType.CHOICE:
        raw_choices = raw.get("choices")
        if (
            not isinstance(raw_choices, list)
            or not raw_choices
            or not all(isinstance(c, str) and c for c in raw_choices)
        ):
            raise _fail(where, "campo de escolha exige lista de opções em texto")
        choices = tuple(str(c) for c in raw_choices)
    return FieldDefinition(
        key=key, label=label.strip(), type=field_type, required=required, choices=choices
    )


def _parse_section(raw: object, where: str, fields: Mapping[str, FieldDefinition]) -> Section:
    if not isinstance(raw, Mapping):
        raise _fail(where, "seção deve ser objeto")
    key, title, body = raw.get("key"), raw.get("title"), raw.get("body")
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise _fail(where, "chave da seção inválida")
    if not isinstance(title, str) or not title.strip():
        raise _fail(where, "título obrigatório")
    if not isinstance(body, str) or not body.strip() or len(body) > MAX_SECTION_LENGTH:
        raise _fail(where, f"texto obrigatório com até {MAX_SECTION_LENGTH} caracteres")
    referenced = set(PLACEHOLDER.findall(body))
    unknown = referenced - fields.keys()
    if unknown:
        raise _fail(where, f"variáveis não declaradas: {', '.join(sorted(unknown))}")
    # Qualquer par de chaves que não seja um marcador válido é recusado, para
    # que nenhuma sintaxe de template "quase válida" chegue ao texto final.
    if _ANY_BRACES.search(PLACEHOLDER.sub("", body)):
        raise _fail(where, "marcador malformado; use {{chave}}")
    optional_when = raw.get("optional_when")
    if optional_when is not None:
        if not isinstance(optional_when, str) or optional_when not in fields:
            raise _fail(where, "optional_when deve referenciar um campo declarado")
        if fields[optional_when].type is not FieldType.BOOLEAN:
            raise _fail(where, "optional_when deve referenciar campo booleano")
    return Section(key=key, title=title.strip(), body=body, optional_when=optional_when)


def parse_definition(raw: object) -> TemplateDefinition:
    if not isinstance(raw, Mapping):
        raise TemplateDefinitionError("Definição deve ser objeto com 'fields' e 'sections'.")
    raw_fields, raw_sections = raw.get("fields"), raw.get("sections")
    if not isinstance(raw_fields, list) or len(raw_fields) > MAX_FIELDS:
        raise TemplateDefinitionError(f"Informe até {MAX_FIELDS} campos.")
    if not isinstance(raw_sections, list) or not 0 < len(raw_sections) <= MAX_SECTIONS:
        raise TemplateDefinitionError(f"Informe de 1 a {MAX_SECTIONS} seções.")
    fields = tuple(_parse_field(f, f"campo {i + 1}") for i, f in enumerate(raw_fields))
    if len({f.key for f in fields}) != len(fields):
        raise TemplateDefinitionError("Chaves de campo repetidas.")
    field_map = {f.key: f for f in fields}
    sections = tuple(
        _parse_section(s, f"seção {i + 1}", field_map) for i, s in enumerate(raw_sections)
    )
    if len({s.key for s in sections}) != len(sections):
        raise TemplateDefinitionError("Chaves de seção repetidas.")
    return TemplateDefinition(fields=fields, sections=sections)


def _text(_field: FieldDefinition, raw: object) -> FieldValue:
    if not isinstance(raw, str):
        raise TypeError("texto")
    if len(raw) > MAX_TEXT_VALUE:
        raise ValueError(f"texto com até {MAX_TEXT_VALUE} caracteres")
    return raw.strip()


def _boolean(_field: FieldDefinition, raw: object) -> FieldValue:
    if not isinstance(raw, bool):
        raise TypeError("verdadeiro ou falso")
    return raw


def _date(_field: FieldDefinition, raw: object) -> FieldValue:
    if not isinstance(raw, str):
        raise TypeError("data no formato AAAA-MM-DD")
    return date.fromisoformat(raw)


def _decimal(_field: FieldDefinition, raw: object) -> FieldValue:
    if not isinstance(raw, str):
        raise TypeError("decimal em texto, ex.: 1500.00")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("decimal inválido") from exc
    if not value.is_finite():
        raise ValueError("decimal deve ser finito")
    return value


def _choice(field: FieldDefinition, raw: object) -> FieldValue:
    if not isinstance(raw, str) or raw not in field.choices:
        raise ValueError("opção não permitida")
    return raw


_COERCERS: Mapping[FieldType, Callable[[FieldDefinition, object], FieldValue]] = MappingProxyType(
    {
        FieldType.TEXT: _text,
        FieldType.BOOLEAN: _boolean,
        FieldType.DATE: _date,
        FieldType.DECIMAL: _decimal,
        FieldType.CHOICE: _choice,
    }
)


def validate_data(
    definition: TemplateDefinition, data: Mapping[str, object]
) -> dict[str, FieldValue]:
    """Valida e converte os dados; rejeita chaves desconhecidas e ausências obrigatórias."""
    fields = definition.field_map
    errors: dict[str, str] = {
        key: "campo não declarado no modelo" for key in data if key not in fields
    }
    values: dict[str, FieldValue] = {}
    for key, field in fields.items():
        raw = data.get(key)
        if raw is None or raw == "":
            if field.required:
                errors[key] = "obrigatório"
            continue
        try:
            values[key] = _COERCERS[field.type](field, raw)
        except (TypeError, ValueError) as exc:
            errors[key] = str(exc)
    if errors:
        raise GenerationDataError(errors)
    return values


def format_value(value: FieldValue) -> str:
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, Decimal):
        quantized = value.quantize(Decimal("0.01"))
        integer, _, cents = f"{quantized:f}".partition(".")
        sign = "-" if integer.startswith("-") else ""
        digits = integer.lstrip("-")
        grouped = ".".join(digits[max(i - 3, 0) : i] for i in range(len(digits), 0, -3)[::-1])
        return f"{sign}{grouped},{cents}"
    return value


def render(definition: TemplateDefinition, values: Mapping[str, FieldValue]) -> str:
    """Texto final: seções na ordem definida, marcadores substituídos."""

    def substitute(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return "" if value is None else format_value(value)

    parts: list[str] = []
    for section in definition.sections:
        if section.optional_when is not None and values.get(section.optional_when) is not True:
            continue
        parts.append(section.title.upper())
        parts.append(PLACEHOLDER.sub(substitute, section.body).strip())
        parts.append("")
    return "\n".join(parts).strip()


def canonical_data(values: Mapping[str, FieldValue]) -> dict[str, str | bool]:
    """Representação JSON estável dos dados usados na geração (registrada na revisão)."""
    canonical: dict[str, str | bool] = {}
    for key in sorted(values):
        value = values[key]
        if isinstance(value, bool):
            canonical[key] = value
        elif isinstance(value, date):
            canonical[key] = value.isoformat()
        elif isinstance(value, Decimal):
            canonical[key] = f"{value:f}"
        else:
            canonical[key] = value
    return canonical
