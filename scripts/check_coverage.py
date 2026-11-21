"""Verifica cobertura de linhas e de branches por escopo.

O ``fail_under`` do coverage combina linhas e branches em um único número;
os critérios de aceite do Pactor exigem limites separados por escopo, então
este script lê o relatório JSON e aplica cada gate declarado no pyproject.
Escopos sem arquivos medidos falham, para que um glob incorreto não aprove
silenciosamente um gate vazio.
"""

from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Gate:
    name: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    min_lines: float
    min_branches: float

    def matches(self, path: str) -> bool:
        return any(fnmatch(path, pattern) for pattern in self.include) and not any(
            fnmatch(path, pattern) for pattern in self.exclude
        )


@dataclass
class Totals:
    statements: int = 0
    covered_lines: int = 0
    branches: int = 0
    covered_branches: int = 0

    @property
    def line_pct(self) -> float:
        return 100.0 if self.statements == 0 else 100.0 * self.covered_lines / self.statements

    @property
    def branch_pct(self) -> float:
        return 100.0 if self.branches == 0 else 100.0 * self.covered_branches / self.branches


def load_gates() -> list[Gate]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [
        Gate(
            name=raw["name"],
            include=tuple(raw["include"]),
            exclude=tuple(raw.get("exclude", ())),
            min_lines=float(raw["min_lines"]),
            min_branches=float(raw["min_branches"]),
        )
        for raw in config["tool"]["pactor"]["coverage-gates"]
    ]


def main(report_path: str) -> int:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    failed = False
    for gate in load_gates():
        totals = Totals()
        files = 0
        for path, data in report["files"].items():
            if not gate.matches(path):
                continue
            files += 1
            summary = data["summary"]
            totals.statements += summary["num_statements"]
            totals.covered_lines += summary["covered_lines"]
            totals.branches += summary.get("num_branches", 0)
            totals.covered_branches += summary.get("covered_branches", 0)
        ok = (
            files > 0
            and totals.line_pct >= gate.min_lines
            and totals.branch_pct >= gate.min_branches
        )
        failed = failed or not ok
        sys.stdout.write(
            f"[{'OK' if ok else 'FALHA'}] {gate.name}: {files} arquivos, "
            f"linhas {totals.line_pct:.2f}% (mín. {gate.min_lines}), "
            f"branches {totals.branch_pct:.2f}% (mín. {gate.min_branches})\n",
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "var/coverage.json"))
