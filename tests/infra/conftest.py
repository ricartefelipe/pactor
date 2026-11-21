"""Worker Celery real em subprocesso, conectado ao banco de teste com o papel da aplicação."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - processo de teste com argumentos fixos, sem shell
import sys
import time
from collections.abc import Iterator

import httpx
import pytest
from django.db import connection

MAILPIT_API = os.environ.get("MAILPIT_API", "http://localhost:8025/api/v1")


@pytest.fixture
def celery_worker_process(transactional_db: None) -> Iterator[subprocess.Popen[bytes]]:
    env = {
        **os.environ,
        "POSTGRES_DB": str(connection.settings_dict["NAME"]),
        "POSTGRES_USER": os.environ.get("PACTOR_DB_APP_ROLE", "pactor_app"),
        "POSTGRES_PASSWORD": os.environ.get("PACTOR_APP_PASSWORD", "app-local"),
        "POSTGRES_CONN_MAX_AGE": "0",
        "EMAIL_HOST": os.environ.get("EMAIL_HOST", "localhost"),
        "EMAIL_PORT": os.environ.get("EMAIL_PORT", "1025"),
    }
    process = subprocess.Popen(  # nosec B603 - argumentos fixos
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "pactor",
            "worker",
            "--pool=solo",
            "--queues=default,notifications",
            "--loglevel=WARNING",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            f"--hostname=teste-{os.getpid()}@%h",
        ],
        env=env,
    )
    time.sleep(3)
    try:
        yield process
    finally:
        process.terminate()
        process.wait(timeout=20)


@pytest.fixture
def mailpit() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=MAILPIT_API, timeout=5) as client:
        client.delete("/messages")
        yield client


def wait_until(predicate: object, *, timeout: float = 30.0) -> None:
    assert callable(predicate)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise AssertionError("condição não atingida dentro do prazo")
