"""Aplicação de transições de elaboração a um contrato já bloqueado."""

from __future__ import annotations

from pactor.contracts.domain import DraftingStatus, can_transition
from pactor.contracts.models import Contract
from pactor.core.errors import InvalidTransitionError


def transition(contract: Contract, target: DraftingStatus) -> None:
    current = DraftingStatus(contract.drafting_status)
    if not can_transition(current, target):
        raise InvalidTransitionError(
            "Transição de situação não permitida.",
            details={"from": current.value, "to": target.value},
        )
    contract.drafting_status = target.value
