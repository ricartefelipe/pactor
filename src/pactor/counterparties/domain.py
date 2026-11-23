"""Validação de documentos brasileiros (CPF e CNPJ) por dígitos verificadores.

A validação confirma apenas a consistência matemática do número; ela não
consulta cadastros oficiais nem atesta a situação da pessoa ou empresa.
"""

from __future__ import annotations

import re
from enum import StrEnum

_NON_DIGITS = re.compile(r"\D")
_CPF_LENGTH = 11
_CNPJ_LENGTH = 14
# Módulo 11: restos 0 e 1 resultam em dígito verificador zero.
_MIN_REMAINDER = 2


class PartyKind(StrEnum):
    INDIVIDUAL = "individual"
    COMPANY = "company"


class InvalidDocumentError(ValueError):
    """Documento com tamanho, formato ou dígitos verificadores inválidos."""


def only_digits(value: str) -> str:
    return _NON_DIGITS.sub("", value)


def _check_digit(digits: str, weights: range | list[int]) -> int:
    total = sum(int(d) * w for d, w in zip(digits, weights, strict=True))
    remainder = total % 11
    return 0 if remainder < _MIN_REMAINDER else 11 - remainder


def cpf_check_digits(base: str) -> str:
    first = _check_digit(base, range(10, 1, -1))
    second = _check_digit(base + str(first), range(11, 1, -1))
    return f"{first}{second}"


_CNPJ_WEIGHTS_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
_CNPJ_WEIGHTS_2 = [6, *_CNPJ_WEIGHTS_1]


def cnpj_check_digits(base: str) -> str:
    first = _check_digit(base, _CNPJ_WEIGHTS_1)
    second = _check_digit(base + str(first), _CNPJ_WEIGHTS_2)
    return f"{first}{second}"


def normalize_document(kind: PartyKind, raw: str) -> str:
    """Devolve somente os dígitos de um CPF/CNPJ válido ou lança erro."""
    digits = only_digits(raw)
    expected_length = _CPF_LENGTH if kind is PartyKind.INDIVIDUAL else _CNPJ_LENGTH
    if len(digits) != expected_length:
        raise InvalidDocumentError(f"Documento deve ter {expected_length} dígitos.")
    if len(set(digits)) == 1:
        raise InvalidDocumentError("Documento com dígitos repetidos é inválido.")
    base, given = digits[:-2], digits[-2:]
    computed = cpf_check_digits(base) if kind is PartyKind.INDIVIDUAL else cnpj_check_digits(base)
    if computed != given:
        raise InvalidDocumentError("Dígitos verificadores não conferem.")
    return digits
