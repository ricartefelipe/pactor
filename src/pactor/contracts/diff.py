"""Comparação textual entre revisões, linha a linha.

É uma comparação de texto: aponta linhas inseridas, removidas e alteradas,
sem interpretar o significado das mudanças.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from enum import StrEnum


class ChangeKind(StrEnum):
    EQUAL = "equal"
    INSERT = "insert"
    DELETE = "delete"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True)
class Hunk:
    kind: ChangeKind
    old_start: int
    old_lines: tuple[str, ...]
    new_start: int
    new_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TextDiff:
    hunks: tuple[Hunk, ...]

    @property
    def inserted(self) -> int:
        return sum(
            len(h.new_lines)
            for h in self.hunks
            if h.kind in {ChangeKind.INSERT, ChangeKind.REPLACE}
        )

    @property
    def deleted(self) -> int:
        return sum(
            len(h.old_lines)
            for h in self.hunks
            if h.kind in {ChangeKind.DELETE, ChangeKind.REPLACE}
        )

    @property
    def unchanged(self) -> bool:
        return all(h.kind is ChangeKind.EQUAL for h in self.hunks)


def compare_text(old: str, new: str) -> TextDiff:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    hunks = tuple(
        Hunk(
            kind=ChangeKind(tag),
            old_start=i1 + 1,
            old_lines=tuple(old_lines[i1:i2]),
            new_start=j1 + 1,
            new_lines=tuple(new_lines[j1:j2]),
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    )
    return TextDiff(hunks=hunks)


def apply_diff(old: str, diff: TextDiff) -> str:
    """Reconstrói o texto novo a partir do antigo (usado para validar o diff)."""
    old_lines = old.splitlines()
    result: list[str] = []
    for hunk in diff.hunks:
        if hunk.kind is ChangeKind.EQUAL:
            result.extend(old_lines[hunk.old_start - 1 : hunk.old_start - 1 + len(hunk.old_lines)])
        else:
            result.extend(hunk.new_lines)
    return "\n".join(result)
