"""Disputas reais entre conexões: edição de rascunho e publicação de revisão.

Uma barreira faz as duas threads lerem a mesma versão antes de gravar; o
resultado esperado é exatamente um sucesso e um conflito, nunca sobrescrita.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from django.db import connections

from pactor.access.policies import Role
from pactor.contracts import services
from pactor.contracts.models import ContractDraft, Revision
from pactor.core.errors import ConflictError, StaleVersionError
from pactor.core.tenancy import tenant_context
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


def _race(workers: list[Callable[[], object]]) -> list[object]:
    barrier = threading.Barrier(len(workers))
    results: list[object] = [None] * len(workers)

    def run(index: int, work: Callable[[], object]) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = work()
        except (StaleVersionError, ConflictError) as exc:
            results[index] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def test_concurrent_draft_saves_never_overwrite_silently() -> None:
    org, _ = factories.organization()
    editor = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, editor)

    def save(text: str) -> Callable[[], object]:
        def work() -> object:
            with tenant_context(org.id):
                principal = factories.principal_for_owner(org, editor)
                return services.save_draft(principal, contract.id, content=text, expected_version=1)

        return work

    results = _race([save("versão A"), save("versão B")])
    winners = [r for r in results if isinstance(r, ContractDraft)]
    losers = [r for r in results if isinstance(r, StaleVersionError)]
    assert len(winners) == 1
    assert len(losers) == 1
    with tenant_context(org.id):
        draft = ContractDraft.objects.get(contract=contract)
    assert draft.version == 2
    assert draft.content == winners[0].content


def test_concurrent_publications_create_a_single_revision() -> None:
    org, _ = factories.organization()
    editor = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, editor)

    def publish() -> object:
        with tenant_context(org.id):
            principal = factories.principal_for_owner(org, editor)
            return services.publish_revision(principal, contract.id, expected_draft_version=1)

    results = _race([publish, publish])
    assert sum(isinstance(r, Revision) for r in results) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    with tenant_context(org.id):
        assert list(
            Revision.objects.filter(contract=contract).values_list("number", flat=True)
        ) == [1]
