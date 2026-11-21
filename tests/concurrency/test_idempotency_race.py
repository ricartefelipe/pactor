"""Duas requisições simultâneas com a mesma chave de idempotência.

Cada thread usa a própria conexão do Django (conexões separadas no
PostgreSQL). A sincronização é determinística: a segunda só tenta reservar a
chave depois que a primeira reservou e antes de ela confirmar.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from django.db import connections

from pactor.core import idempotency
from pactor.core.errors import IdempotencyConflictError
from pactor.core.idempotency import StoredResponse
from pactor.core.tenancy import tenant_context
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


def _run_pair(org_id: uuid.UUID, actor: uuid.UUID, second_fingerprint: str) -> object:
    reserved = threading.Event()
    results: dict[str, object] = {}

    def first() -> None:
        try:
            with tenant_context(org_id):
                assert (
                    idempotency.begin(
                        actor_id=actor, scope="op", key="chave-12345", fingerprint="f1"
                    )
                    is None
                )
                reserved.set()
                # Mantém a transação aberta até a segunda conexão estar bloqueada.
                second_started.wait(timeout=10)
                threading.Event().wait(0.3)
                idempotency.complete(
                    actor_id=actor, scope="op", key="chave-12345", status=201, body={"id": 1}
                )
        finally:
            connections.close_all()

    def second() -> None:
        try:
            reserved.wait(timeout=10)
            second_started.set()
            with tenant_context(org_id):
                results["second"] = idempotency.begin(
                    actor_id=actor, scope="op", key="chave-12345", fingerprint=second_fingerprint
                )
        except IdempotencyConflictError as exc:
            results["second"] = exc
        finally:
            connections.close_all()

    second_started = threading.Event()
    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    return results["second"]


def test_concurrent_same_key_same_content_replays_first_response() -> None:
    org, owner = factories.organization()
    outcome = _run_pair(org.id, owner.id, "f1")
    assert outcome == StoredResponse(status=201, body={"id": 1})


def test_concurrent_same_key_different_content_conflicts() -> None:
    org, owner = factories.organization()
    outcome = _run_pair(org.id, owner.id, "f2")
    assert isinstance(outcome, IdempotencyConflictError)


def test_failed_first_attempt_releases_key() -> None:
    org, owner = factories.organization()
    with pytest.raises(RuntimeError), tenant_context(org.id):
        idempotency.begin(actor_id=owner.id, scope="op", key="chave-falha", fingerprint="f")
        raise RuntimeError
    with tenant_context(org.id):
        assert (
            idempotency.begin(actor_id=owner.id, scope="op", key="chave-falha", fingerprint="g")
            is None
        )
