"""Entrega da aplicação web na mesma origem da API."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import Client, override_settings

INDEX = b'<!doctype html><html lang="pt-BR"><body><div id="root"></div></body></html>'


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_bytes(INDEX)
    return tmp_path


@pytest.mark.parametrize(
    "path", ["/", "/contratos", "/contratos/abc/assinatura", "/convites/aceitar?token=x"]
)
def test_application_routes_receive_the_web_app(client: Client, dist: Path, path: str) -> None:
    with override_settings(FRONTEND_DIST_DIR=dist):
        response = client.get(path)
    assert response.status_code == 200
    assert response.content == INDEX
    assert response["Content-Type"] == "text/html; charset=utf-8"
    assert response["Cache-Control"] == "no-cache"
    assert "script-src 'self'" in response["Content-Security-Policy"]
    assert "pactor_csrftoken" in response.cookies


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/inexistente",
        "/api",
        "/_allauth/inexistente",
        "/static/nada.js",
        "/webhooks/x",
        "/o/x",
    ],
)
def test_service_prefixes_never_fall_back_to_the_web_app(
    client: Client, dist: Path, path: str
) -> None:
    with override_settings(FRONTEND_DIST_DIR=dist):
        response = client.get(path)
    assert response.status_code == 404
    assert response.content != INDEX


def test_missing_build_is_reported_without_masking_as_success(
    client: Client, tmp_path: Path
) -> None:
    with override_settings(FRONTEND_DIST_DIR=tmp_path):
        response = client.get("/contratos")
    assert response.status_code == 503
    assert "npm run build" in response.content.decode()


def test_web_app_only_answers_get(client: Client, dist: Path) -> None:
    with override_settings(FRONTEND_DIST_DIR=dist):
        assert client.post("/contratos").status_code == 405
