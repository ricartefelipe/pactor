"""Confere que uma resposta real tem exatamente os campos do serializer documentado.

Os serializers de resposta usados só no OpenAPI não participam da execução;
esta verificação impede que a documentação (e os tipos gerados para o
frontend) divirja do que a view devolve.
"""

from __future__ import annotations

from collections.abc import Mapping

from rest_framework import serializers


def assert_matches[T](documented: serializers.BaseSerializer[T], data: object) -> None:
    if isinstance(documented, serializers.ListSerializer):
        assert isinstance(data, list), f"esperava lista, recebeu {type(data).__name__}"
        child = documented.child
        assert isinstance(child, serializers.BaseSerializer)
        for item in data:
            assert_matches(child, item)
        return
    assert isinstance(documented, serializers.Serializer)
    assert isinstance(data, Mapping), f"esperava objeto, recebeu {type(data).__name__}"
    assert set(data) == set(documented.fields), (
        f"{type(documented).__name__}: sobrando {sorted(set(data) - set(documented.fields))}, "
        f"faltando {sorted(set(documented.fields) - set(data))}"
    )
    for name, field in documented.fields.items():
        value = data[name]
        if isinstance(field, serializers.BaseSerializer) and value is not None:
            assert_matches(field, value)
