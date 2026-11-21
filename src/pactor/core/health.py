"""Endpoints de liveness e readiness.

Liveness indica apenas que o processo responde. Readiness verifica as
dependências necessárias para atender requisições: banco, cache e broker.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET
from kombu import Connection

logger = logging.getLogger(__name__)

_BROKER_TIMEOUT_SECONDS = 2


def _check_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def _check_cache() -> None:
    cache.set("pactor:readiness", "ok", timeout=5)
    if cache.get("pactor:readiness") != "ok":
        raise RuntimeError("cache não devolveu o valor gravado")


def _check_broker() -> None:
    with Connection(settings.CELERY_BROKER_URL, connect_timeout=_BROKER_TIMEOUT_SECONDS) as conn:
        conn.ensure_connection(max_retries=1, timeout=_BROKER_TIMEOUT_SECONDS)


READINESS_CHECKS: dict[str, Callable[[], None]] = {
    "database": _check_database,
    "cache": _check_cache,
    "broker": _check_broker,
}


# Health checks não abrem a transação por requisição (ATOMIC_REQUESTS):
# liveness não pode depender do banco, e readiness testa cada dependência.
@transaction.non_atomic_requests
@require_GET
def live(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@transaction.non_atomic_requests
@require_GET
def ready(_request: HttpRequest) -> JsonResponse:
    results: dict[str, str] = {}
    for name, check in READINESS_CHECKS.items():
        try:
            check()
        except Exception:
            # Readiness precisa sobreviver a qualquer falha de dependência; o
            # erro completo vai para o log e a resposta expõe apenas o nome.
            logger.exception("readiness_check_failed", extra={"check": name})
            results[name] = "unavailable"
        else:
            results[name] = "ok"
    healthy = all(value == "ok" for value in results.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": results},
        status=200 if healthy else 503,
    )
