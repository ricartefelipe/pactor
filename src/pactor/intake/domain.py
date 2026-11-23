"""Transições de solicitações de contrato."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class RequestState(StrEnum):
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    CONVERTED = "converted"


TRANSITIONS: Mapping[RequestState, frozenset[RequestState]] = MappingProxyType(
    {
        RequestState.SUBMITTED: frozenset(
            {RequestState.IN_REVIEW, RequestState.REJECTED, RequestState.CANCELLED}
        ),
        RequestState.IN_REVIEW: frozenset(
            {RequestState.CONVERTED, RequestState.REJECTED, RequestState.CANCELLED}
        ),
        RequestState.REJECTED: frozenset(),
        RequestState.CANCELLED: frozenset(),
        RequestState.CONVERTED: frozenset(),
    }
)


def can_transition(current: RequestState, target: RequestState) -> bool:
    return target in TRANSITIONS[current]
