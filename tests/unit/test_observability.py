import importlib
import logging
import uuid

import pytest

from pactor.core import metrics
from pactor.core.observability import (
    REDACTED,
    LogContextFilter,
    RedactionFilter,
    bind_log_context,
    current_log_context,
    entity_correlation,
    extend_log_context,
    redact,
)


def test_bind_log_context_nests_and_restores() -> None:
    with bind_log_context(request_id="r1"):
        with bind_log_context(contract_id="c1"):
            assert current_log_context() == {"request_id": "r1", "contract_id": "c1"}
        assert current_log_context() == {"request_id": "r1"}
    assert current_log_context() == {}


def test_bind_log_context_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="desconhecidas"), bind_log_context(user_password="x"):
        pass


def test_bind_log_context_ignores_none_values() -> None:
    with bind_log_context(request_id="r1", contract_id=None):
        assert current_log_context() == {"request_id": "r1"}


def test_redact_masks_sensitive_keys_recursively() -> None:
    payload = {
        "password": "p",
        "nested": {"access_token": "t", "ok": 1},
        "items": [{"secret": "s"}],
    }
    assert redact(payload) == {
        "password": REDACTED,
        "nested": {"access_token": REDACTED, "ok": 1},
        "items": [{"secret": REDACTED}],
    }


def test_redact_masks_inline_credentials() -> None:
    assert redact("Authorization: Bearer abc.def") == f"Authorization: Bearer {REDACTED}"
    assert redact("url?token=xyz&a=1") == f"url?token={REDACTED}&a=1"


def test_redaction_filter_applies_to_message_and_extras() -> None:
    record = logging.makeLogRecord({"msg": "login password=%s", "args": ("hunter2",)})
    record.__dict__.update(api_key="k", payload={"token": "t"})
    assert RedactionFilter().filter(record)
    assert record.msg == f"login password={REDACTED}"
    assert record.__dict__["api_key"] == REDACTED
    assert record.__dict__["payload"] == {"token": REDACTED}


def test_log_context_filter_copies_correlation_ids() -> None:
    record = logging.makeLogRecord({"msg": "x"})
    with bind_log_context(request_id="r9"):
        LogContextFilter().filter(record)
    assert record.__dict__["request_id"] == "r9"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"contract_id": "abc", "pk": "1"}, {"contract_id": "abc"}),
        ({"revision_id": uuid.UUID(int=1)}, {"revision_id": str(uuid.UUID(int=1))}),
        ({"envelope_id": ""}, {}),
        ({"contract_id": 10, "envelope_id": None}, {}),
    ],
)
def test_entity_correlation_accepts_only_known_identifier_values(
    values: dict[str, object], expected: dict[str, str]
) -> None:
    assert entity_correlation(values) == expected


def test_extend_log_context_lasts_until_the_enclosing_block_ends() -> None:
    with bind_log_context(request_id="r1"):
        extend_log_context(organization_id="o1")
        assert current_log_context() == {"request_id": "r1", "organization_id": "o1"}
        with pytest.raises(ValueError, match="desconhecidas"):
            extend_log_context(usuario="x")
    assert current_log_context() == {}


def test_importing_metrics_does_not_query_the_database() -> None:
    """Sem marcador de banco: qualquer consulta no import falharia aqui."""
    importlib.reload(metrics)
