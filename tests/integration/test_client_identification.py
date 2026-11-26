"""Identificação do cliente atrás de proxy reverso e métodos fora das rotas da API.

Atrás de um proxy, todo cliente chega com o IP do proxy. Sem declarar quantos
proxies confiáveis reescrevem o X-Forwarded-For, os limites por IP do allauth e
do DRF passam a valer para todos os clientes juntos; sem proxy, o cabeçalho é
ignorado, porque o próprio cliente poderia forjá-lo.
"""

from __future__ import annotations

import uuid

import pytest
from django.test import Client, override_settings
from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import AnonRateThrottle

from pactor.access.policies import Role
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)

PROXY = "172.18.0.2"
WRONG_PASSWORD = "senha-errada-de-teste"  # nosec B105 - credencial de teste


def _fresh_ip() -> str:
    """Endereço de documentação (RFC 3849) novo a cada chamada, para não herdar contadores.

    O allauth agrupa IPv6 por /64 nos limites; por isso o que varia é o prefixo.
    """
    suffix = uuid.uuid4().hex
    return f"2001:db8:{suffix[:4]}:{suffix[4:8]}::1"


def _failed_login(client_ip: str | None) -> bool:
    """Tenta entrar com senha errada; diz se a tentativa foi barrada pelo limite de falhas."""
    headers = {"X-Forwarded-For": client_ip} if client_ip else {}
    response = Client(REMOTE_ADDR=PROXY).post(
        "/_allauth/browser/v1/auth/login",
        # Um e-mail novo por tentativa isola o limite por IP do limite por conta.
        {"email": f"{uuid.uuid4().hex}@exemplo.test", "password": WRONG_PASSWORD},
        content_type="application/json",
        headers=headers,
    )
    assert response.status_code == 400
    # O allauth headless recusa a tentativa excedente como erro de validação, e não com 429.
    return "too_many_login_attempts" in {error["code"] for error in response.json()["errors"]}


@override_settings(ALLAUTH_TRUSTED_PROXY_COUNT=1, ACCOUNT_RATE_LIMITS={"login_failed": "3/m/ip"})
def test_login_failures_are_limited_per_client_behind_a_trusted_proxy() -> None:
    attacker, visitor = _fresh_ip(), _fresh_ip()
    assert [_failed_login(attacker) for _ in range(4)] == [False, False, False, True]
    # Outro cliente atrás do mesmo proxy não herda o bloqueio.
    assert _failed_login(visitor) is False


@override_settings(ALLAUTH_TRUSTED_PROXY_COUNT=0, ACCOUNT_RATE_LIMITS={"login_failed": "3/m/ip"})
def test_forged_forwarded_for_is_ignored_without_trusted_proxy() -> None:
    # Sem proxy declarado, trocar o X-Forwarded-For não escapa do limite da conexão.
    with override_settings(CACHES=_isolated_cache()):
        assert [_failed_login(_fresh_ip()) for _ in range(4)] == [False, False, False, True]


def _isolated_cache() -> dict[str, dict[str, str]]:
    # O IP da conexão (PROXY) é fixo: um cache próprio evita herdar contadores de outro teste.
    return {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": uuid.uuid4().hex,
        }
    }


@pytest.mark.parametrize(("num_proxies", "expected"), [(0, PROXY), (1, "203.0.113.9")])
def test_api_throttle_identifies_client_by_trusted_proxy_count(
    num_proxies: int, expected: str
) -> None:
    raw = APIRequestFactory().get(
        "/api/v1/me", REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR="203.0.113.9"
    )
    with override_settings(REST_FRAMEWORK={"NUM_PROXIES": num_proxies}):
        assert num_proxies == api_settings.NUM_PROXIES
        assert AnonRateThrottle().get_ident(Request(raw)) == expected


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options"])
def test_methods_without_handler_answer_405_instead_of_failing(method: str) -> None:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    client = factories.client_for(manager, org)
    response = getattr(client, method)("/api/v1/members/directory", content_type="application/json")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


@pytest.mark.parametrize("method", ["LIST", "SETUP", "DISPATCH"])
def test_methods_outside_http_answer_405_even_when_named_like_view_attributes(method: str) -> None:
    org, _ = factories.organization()
    client = factories.client_for(factories.member(org, [Role.CONTRACT_MANAGER]), org)
    response = client.generic(method, "/api/v1/members/directory")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


@pytest.mark.parametrize("path", ["/api/v1/search?q=contrato", "/api/v1/members/directory"])
def test_head_uses_the_get_permission(path: str) -> None:
    org, _ = factories.organization()
    client = factories.client_for(factories.member(org, [Role.CONTRACT_MANAGER]), org)
    assert client.get(path).status_code == 200
    head = client.head(path)
    assert head.status_code == 200
    assert head.content == b""
