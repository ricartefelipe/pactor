"""Filtros de listagem explícitos e validados.

Cada listagem declara um serializer com os filtros permitidos; parâmetros
desconhecidos são rejeitados em vez de ignorados, para que um erro de
digitação não devolva silenciosamente um resultado mais amplo.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework.request import Request

# Parâmetros de paginação e formato tratados fora dos filtros.
_RESERVED = frozenset({"cursor", "page_size"})


def parse_filters(
    request: Request, serializer_class: type[serializers.Serializer[Any]]
) -> dict[str, Any]:
    params = {k: v for k, v in request.query_params.items() if k not in _RESERVED}
    serializer = serializer_class(data=params)
    unknown = set(params) - set(serializer.fields)
    if unknown:
        raise serializers.ValidationError(
            {key: ["Filtro não permitido."] for key in sorted(unknown)}
        )
    serializer.is_valid(raise_exception=True)
    return {k: v for k, v in serializer.validated_data.items() if v not in (None, "")}
