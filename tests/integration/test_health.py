import pytest
from django.test import Client

from pactor.core import health


def test_liveness_does_not_touch_dependencies(client: Client) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response["X-Request-ID"]) == 32


@pytest.mark.django_db
def test_readiness_reports_all_dependencies_ok(client: Client) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200, response.json()
    assert response.json()["checks"] == {"database": "ok", "cache": "ok", "broker": "ok"}


@pytest.mark.django_db
def test_readiness_degrades_when_a_dependency_fails(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise ConnectionError("broker indisponível")

    monkeypatch.setitem(health.READINESS_CHECKS, "broker", broken)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["broker"] == "unavailable"


def test_valid_incoming_request_id_is_propagated(client: Client) -> None:
    response = client.get("/health/live", HTTP_X_REQUEST_ID="abc12345-trace")
    assert response["X-Request-ID"] == "abc12345-trace"


def test_malformed_request_id_is_replaced(client: Client) -> None:
    response = client.get("/health/live", HTTP_X_REQUEST_ID="bad id\nwith newline")
    assert response["X-Request-ID"] != "bad id\nwith newline"
