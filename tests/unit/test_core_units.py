"""Regras puras de infraestrutura: tokens, backoff, envelopes e fingerprints."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.test import override_settings
from hypothesis import given
from hypothesis import strategies as st

from pactor.core.errors import DomainValidationError
from pactor.core.idempotency import request_fingerprint, validate_key
from pactor.core.outbox import EventEnvelope, backoff_for
from pactor.core.tasks import retry_countdown
from pactor.identity.invitations import make_token, parse_token

ORG = uuid.UUID(int=7)
INV = uuid.UUID(int=8)


def test_invitation_token_roundtrip() -> None:
    parsed = parse_token(make_token(ORG, INV))
    assert (parsed.organization_id, parsed.invitation_id) == (ORG, INV)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t + "0",
        lambda t: t.replace(str(INV), str(uuid.UUID(int=9))),
        lambda t: t.replace(str(ORG), str(uuid.UUID(int=6))),
        lambda _t: "x.y.z",
        lambda t: t.rsplit(".", 1)[0],
    ],
)
def test_tampered_invitation_token_is_rejected(mutate: object) -> None:
    assert callable(mutate)
    with pytest.raises(DomainValidationError):
        parse_token(mutate(make_token(ORG, INV)))


def test_invitation_token_depends_on_secret_key() -> None:
    token = make_token(ORG, INV)
    with override_settings(SECRET_KEY="outra-chave"), pytest.raises(DomainValidationError):
        parse_token(token)


def test_backoff_grows_and_is_capped() -> None:
    assert backoff_for(0) == timedelta(seconds=2)
    assert backoff_for(3) == timedelta(seconds=8)
    assert backoff_for(50) == timedelta(minutes=15)


@given(st.integers(min_value=0, max_value=100))
def test_retry_countdown_is_monotonic_and_bounded(retries: int) -> None:
    assert 5 <= retry_countdown(retries) <= 600
    assert retry_countdown(retries) <= retry_countdown(retries + 1)


def test_envelope_message_roundtrip() -> None:
    envelope = EventEnvelope(
        id=uuid.uuid4(), topic="t", organization_id=ORG, payload={"a": 1}, payload_version=2
    )
    assert EventEnvelope.from_message(envelope.to_message()) == envelope
    system = EventEnvelope(
        id=uuid.uuid4(), topic="t", organization_id=None, payload={}, payload_version=1
    )
    assert EventEnvelope.from_message(system.to_message()) == system


def test_malformed_envelope_is_rejected() -> None:
    with pytest.raises(TypeError, match="malformada"):
        EventEnvelope.from_message(
            {"id": str(uuid.uuid4()), "topic": "t", "payload": [], "payload_version": 1}
        )


def test_idempotency_key_validation() -> None:
    assert validate_key("abcDEF-123_x") == "abcDEF-123_x"
    for bad in (None, "curta", "com espaço 123", "x" * 129):
        with pytest.raises(DomainValidationError):
            validate_key(bad)


def test_fingerprint_is_canonical() -> None:
    a = request_fingerprint("post", "/x", {"b": 1, "a": [1, 2]})
    b = request_fingerprint("POST", "/x", {"a": [1, 2], "b": 1})
    assert a == b
    assert a != request_fingerprint("POST", "/x", {"a": [2, 1], "b": 1})
    assert a != request_fingerprint("POST", "/y", {"a": [1, 2], "b": 1})
