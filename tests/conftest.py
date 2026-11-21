"""Configuração compartilhada da suíte.

Cada diretório aplica seu marcador automaticamente, permitindo executar
subconjuntos (``-m unit``, ``-m db``) sem depender de anotações manuais.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_MARKERS_BY_DIR = {
    "unit": "unit",
    "integration": "db",
    "concurrency": "concurrency",
    "infra": "infra",
    "external": "external",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    tests_root = Path(__file__).parent
    for item in items:
        relative = Path(item.path).relative_to(tests_root)
        marker = _MARKERS_BY_DIR.get(relative.parts[0])
        if marker is not None:
            item.add_marker(getattr(pytest.mark, marker))
