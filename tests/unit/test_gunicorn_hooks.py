"""Ganchos do Gunicorn para métricas multiprocesso."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from prometheus_client import multiprocess

from pactor.config import gunicorn


@dataclass
class FakeWorker:
    pid: int


def test_on_starting_creates_directory_and_discards_stale_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "metricas"
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(directory))
    gunicorn.on_starting(object())
    assert directory.is_dir()
    (directory / "counter_123.db").write_bytes(b"antigo")
    (directory / "manter.txt").write_text("outro")
    gunicorn.on_starting(object())
    assert sorted(p.name for p in directory.iterdir()) == ["manter.txt"]


def test_hooks_do_nothing_without_multiprocess_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    marked: list[int] = []
    monkeypatch.setattr(multiprocess, "mark_process_dead", marked.append)
    gunicorn.on_starting(object())
    gunicorn.child_exit(object(), FakeWorker(pid=42))
    assert marked == []
    assert list(tmp_path.iterdir()) == []


def test_child_exit_marks_the_worker_process_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    marked: list[int] = []
    monkeypatch.setattr(multiprocess, "mark_process_dead", marked.append)
    gunicorn.child_exit(object(), FakeWorker(pid=42))
    assert marked == [42]
