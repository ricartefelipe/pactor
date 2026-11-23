"""Publicação de nova revisão concorrente ao voto final da revisão anterior.

As duas operações travam a linha do contrato. Qualquer que seja a ordem em
que o PostgreSQL concede o lock, o estado final precisa respeitar a
invariante: contrato aprovado implica aprovação da revisão vigente com o
mesmo fingerprint. Cada execução força uma ordem diferente segurando o lock
na primeira thread até a segunda estar bloqueada.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from decimal import Decimal

import pytest
from django.db import connections

from pactor.access.policies import Role
from pactor.approvals import services as approvals
from pactor.approvals.domain import Decision
from pactor.approvals.models import ApprovalRound
from pactor.contracts import services as contracts
from pactor.contracts.models import Contract, Revision
from pactor.core.errors import ConflictError
from pactor.core.tenancy import tenant_context
from pactor.identity.models import Organization, User
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


def _scenario() -> tuple[Organization, User, User, Contract, ApprovalRound]:
    org, admin = factories.organization()
    author = factories.member(org, [Role.CONTRACT_MANAGER])
    approver = factories.member(org, [Role.APPROVER])
    rules = [
        {
            "name": "Padrão",
            "conditions": [],
            "steps": [
                {
                    "key": "unica",
                    "name": "Única",
                    "order": 1,
                    "quorum": 1,
                    "approver_ids": [str(approver.id)],
                }
            ],
        }
    ]
    contract = factories.contract(org, author, total_value=Decimal("10.00"))
    with tenant_context(org.id):
        approvals.publish_workflow(factories.principal(org, admin), name="W", rules=rules)
        principal = factories.principal_for_owner(org, author)
        revision = contracts.publish_revision(principal, contract.id, expected_draft_version=1)
        contracts.save_draft(
            principal, contract.id, content="Conteúdo novo concorrente", expected_version=1
        )
        round_ = approvals.submit_for_approval(
            principal, contract.id, expected_fingerprint=revision.fingerprint
        )
    return org, author, approver, contract, round_


type Work = Callable[[threading.Event | None], object]


def _run_in_order(first: Work, second: Work) -> dict[str, object]:
    """``first`` sinaliza depois de travar o contrato; só então ``second`` começa.

    A primeira thread mantém a transação (e o lock) aberta por alguns instantes
    após sinalizar, garantindo que a segunda fique bloqueada esperando o commit.
    """
    results: dict[str, object] = {}
    locked = threading.Event()

    def run(name: str, work: Work, signal: threading.Event | None) -> None:
        try:
            results[name] = work(signal)
        except ConflictError as exc:
            results[name] = exc
        finally:
            connections.close_all()

    first_thread = threading.Thread(target=run, args=("first", first, locked))
    first_thread.start()
    assert locked.wait(timeout=10)
    second_thread = threading.Thread(target=run, args=("second", second, None))
    second_thread.start()
    first_thread.join(timeout=30)
    second_thread.join(timeout=30)
    return results


def _assert_invariant(org: Organization, contract: Contract) -> Contract:
    with tenant_context(org.id):
        fresh = Contract.objects.select_related("current_revision").get(id=contract.id)
        if fresh.drafting_status == "approved":
            assert fresh.current_revision is not None
            assert fresh.approved_revision_id == fresh.current_revision_id
            assert fresh.approved_fingerprint == fresh.current_revision.fingerprint
        assert ApprovalRound.objects.filter(contract=contract, status="open").count() == 0
        return fresh


_HOLD_SECONDS = 0.5


def _hold(signal: threading.Event | None) -> None:
    if signal is not None:
        signal.set()
        time.sleep(_HOLD_SECONDS)  # mantém o lock do contrato enquanto a outra thread espera


def _vote_work(org: Organization, approver: User, round_: ApprovalRound) -> Work:
    def work(signal: threading.Event | None) -> object:
        with tenant_context(org.id):
            vote = approvals.cast_vote(
                factories.principal(org, approver),
                round_.id,
                step_key="unica",
                decision=Decision.APPROVE,
                expected_fingerprint=round_.fingerprint,
            )
            _hold(signal)
            return vote

    return work


def _publish_work(org: Organization, author: User, contract: Contract) -> Work:
    def work(signal: threading.Event | None) -> object:
        with tenant_context(org.id):
            revision = contracts.publish_revision(
                factories.principal_for_owner(org, author), contract.id, expected_draft_version=2
            )
            _hold(signal)
            return revision

    return work


def test_vote_first_then_publication_invalidates_approval() -> None:
    org, author, approver, contract, round_ = _scenario()
    results = _run_in_order(_vote_work(org, approver, round_), _publish_work(org, author, contract))
    assert isinstance(results["second"], Revision)
    fresh = _assert_invariant(org, contract)
    # A aprovação da revisão 1 deixou de valer com a publicação da revisão 2.
    assert fresh.drafting_status == "draft"
    assert fresh.approved_revision_id is None


def test_publication_first_then_vote_is_rejected() -> None:
    org, author, approver, contract, round_ = _scenario()
    results = _run_in_order(_publish_work(org, author, contract), _vote_work(org, approver, round_))
    assert isinstance(results["first"], Revision)
    assert isinstance(results["second"], ConflictError)
    fresh = _assert_invariant(org, contract)
    assert fresh.drafting_status == "draft"
    with tenant_context(org.id):
        assert ApprovalRound.objects.get(id=round_.id).status == "superseded"
