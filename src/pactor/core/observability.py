"""Correlação de logs, redação de segredos e métricas HTTP.

O contexto de correlação usa ``contextvars`` apenas para enriquecer logs; ele
nunca é usado como fonte de autorização ou de isolamento de tenants.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Final

from django.db import connection
from django.http import HttpRequest, HttpResponse
from prometheus_client import Counter, Histogram

_CORRELATION_KEYS: Final = (
    "request_id",
    "organization_id",
    "contract_id",
    "revision_id",
    "envelope_id",
    "task_id",
)
# Chaves de entidades que podem vir de parâmetros de rota ou de payloads de eventos.
_ENTITY_KEYS: Final = ("contract_id", "revision_id", "envelope_id")
# Pares imutáveis: cada bloco cria uma nova tupla e restaura a anterior ao sair.
_log_context: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("pactor_log_context", default=())

_REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

HTTP_LATENCY = Histogram(
    "pactor_http_request_duration_seconds",
    "Latência das requisições HTTP por rota resolvida.",
    labelnames=("method", "route", "status_class"),
)
HTTP_DB_QUERIES = Histogram(
    "pactor_http_db_queries",
    "Consultas ao banco por requisição, por rota resolvida (detecta N+1).",
    labelnames=("route",),
    buckets=(1, 2, 5, 10, 20, 50, 100, 200),
)
CONSUMER_FAILURES = Counter(
    "pactor_consumer_failures_total",
    "Falhas de consumidores de eventos (nomes fixos de consumidores).",
    labelnames=("consumer",),
)
HTTP_ERRORS = Counter(
    "pactor_http_errors_total",
    "Respostas HTTP 5xx por rota resolvida.",
    labelnames=("method", "route"),
)


def current_log_context() -> dict[str, str]:
    return dict(_log_context.get())


@contextmanager
def bind_log_context(**values: str | None) -> Iterator[None]:
    """Acrescenta chaves de correlação durante o bloco e restaura ao sair."""
    unknown = set(values) - set(_CORRELATION_KEYS)
    if unknown:
        raise ValueError(f"Chaves de correlação desconhecidas: {sorted(unknown)}")
    merged = {**dict(_log_context.get()), **{k: v for k, v in values.items() if v is not None}}
    token: Token[tuple[tuple[str, str], ...]] = _log_context.set(tuple(merged.items()))
    try:
        yield
    finally:
        _log_context.reset(token)


def extend_log_context(**values: str) -> None:
    """Acrescenta chaves ao bloco ``bind_log_context`` já aberto.

    Serve para dados conhecidos só no meio do bloco (ex.: a organização,
    resolvida após a autenticação). A saída do bloco externo restaura o
    contexto anterior, descartando também estas chaves.
    """
    unknown = set(values) - set(_CORRELATION_KEYS)
    if unknown:
        raise ValueError(f"Chaves de correlação desconhecidas: {sorted(unknown)}")
    _log_context.set(tuple({**dict(_log_context.get()), **values}.items()))


def entity_correlation(values: Mapping[str, object]) -> dict[str, str]:
    """Extrai contrato, revisão e envelope de parâmetros de rota ou payload de evento."""
    return {
        key: str(values[key])
        for key in _ENTITY_KEYS
        if isinstance(values.get(key), str | uuid.UUID) and str(values[key])
    }


class LogContextFilter(logging.Filter):
    """Copia as chaves de correlação ativas para cada registro de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _log_context.get():
            setattr(record, key, value)
        return True


_SENSITIVE_KEY: Final = re.compile(
    r"pass(word)?|secret|token|authorization|cookie|api[_-]?key|session|hmac|signature",
    re.IGNORECASE,
)
_INLINE_SECRET: Final = re.compile(
    r"(?i)(bearer\s+|password=|token=|secret=|apikey=)([^\s&\"']+)",
)
REDACTED: Final = "[REDACTED]"
_SERVER_ERROR: Final = 500


def redact(value: object) -> object:
    """Remove segredos de estruturas aninhadas destinadas a logs ou auditoria."""
    if isinstance(value, Mapping):
        return {
            k: REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _INLINE_SECRET.sub(lambda m: m.group(1) + REDACTED, value)
    return value


class RedactionFilter(logging.Filter):
    """Aplica redação à mensagem formatada e a atributos extras do registro."""

    _STANDARD_ATTRS: Final = frozenset(vars(logging.makeLogRecord({})))

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = redact(message)
        record.args = None
        for key in list(vars(record)):
            if key in self._STANDARD_ATTRS or key in _CORRELATION_KEYS:
                continue
            if _SENSITIVE_KEY.search(key):
                setattr(record, key, REDACTED)
            else:
                setattr(record, key, redact(getattr(record, key)))
        return True


def _request_id_from(request: HttpRequest) -> str:
    candidate = request.headers.get("X-Request-ID", "")
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


class _QueryCounter:
    """Wrapper de execução do Django que conta consultas da requisição."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(
        self,
        execute: Callable[[str, object, bool, dict[str, object]], object],
        sql: str,
        params: object,
        many: bool,  # noqa: FBT001 - posição e tipo definidos pela interface execute_wrapper do Django
        context: dict[str, object],
    ) -> object:
        self.count += 1
        return execute(sql, params, many, context)


class RequestContextMiddleware:
    """Define o identificador de correlação e registra métricas da requisição."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = _request_id_from(request)
        started = time.perf_counter()
        counter = _QueryCounter()
        with bind_log_context(request_id=request_id), connection.execute_wrapper(counter):
            response = self.get_response(request)
        response["X-Request-ID"] = request_id
        match = getattr(request, "resolver_match", None)
        # A rota resolvida (padrão de URL) mantém a cardinalidade das métricas
        # limitada; caminhos brutos com identificadores nunca viram labels.
        route = match.route if match is not None and match.route else "unresolved"
        status_class = f"{response.status_code // 100}xx"
        HTTP_LATENCY.labels(request.method or "", route, status_class).observe(
            time.perf_counter() - started,
        )
        HTTP_DB_QUERIES.labels(route).observe(counter.count)
        if response.status_code >= _SERVER_ERROR:
            HTTP_ERRORS.labels(request.method or "", route).inc()
        return response
