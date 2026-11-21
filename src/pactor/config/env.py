"""Leitura tipada de variáveis de ambiente.

As funções falham cedo com mensagens claras quando uma variável obrigatória
não está definida ou possui formato inválido, evitando configurações parciais
que só seriam percebidas em produção.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


class ConfigurationError(RuntimeError):
    """Variável de ambiente ausente ou inválida."""


def env_str(
    name: str, default: str | None = None, *, environ: Mapping[str, str] | None = None
) -> str:
    source = os.environ if environ is None else environ
    value = source.get(name, default)
    if value is None:
        raise ConfigurationError(f"Variável de ambiente obrigatória ausente: {name}")
    return value


def env_bool(name: str, *, default: bool, environ: Mapping[str, str] | None = None) -> bool:
    raw = env_str(name, "true" if default else "false", environ=environ).strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigurationError(f"{name} deve ser booleano, recebido: {raw!r}")


def env_int(
    name: str, default: int | None = None, *, environ: Mapping[str, str] | None = None
) -> int:
    raw = env_str(name, None if default is None else str(default), environ=environ)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} deve ser inteiro, recebido: {raw!r}") from exc


def env_list(
    name: str, default: str = "", *, environ: Mapping[str, str] | None = None
) -> list[str]:
    raw = env_str(name, default, environ=environ)
    return [item.strip() for item in raw.split(",") if item.strip()]
