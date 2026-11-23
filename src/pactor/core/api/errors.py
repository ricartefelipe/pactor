"""Formato único de erro da API.

Todas as respostas de erro seguem ``{"error": {"code", "message", "details",
"request_id"}}``. Exceções inesperadas não são tratadas aqui: o Django as
registra e devolve 500 sem expor detalhes internos.
"""

from __future__ import annotations

from collections.abc import Mapping

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback

from pactor.core.errors import PactorError
from pactor.core.observability import current_log_context

_DRF_CODES: Mapping[type[drf_exceptions.APIException], str] = {
    drf_exceptions.NotAuthenticated: "not_authenticated",
    drf_exceptions.AuthenticationFailed: "authentication_failed",
    drf_exceptions.PermissionDenied: "permission_denied",
    drf_exceptions.NotFound: "not_found",
    drf_exceptions.MethodNotAllowed: "method_not_allowed",
    drf_exceptions.NotAcceptable: "not_acceptable",
    drf_exceptions.UnsupportedMediaType: "unsupported_media_type",
    drf_exceptions.Throttled: "rate_limited",
    drf_exceptions.ParseError: "malformed_request",
    drf_exceptions.ValidationError: "validation_failed",
}


def error_body(code: str, message: str, details: object = None) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details if details is not None else {},
            "request_id": current_log_context().get("request_id"),
        },
    }


def _drf_code(exc: drf_exceptions.APIException) -> str:
    for exc_type, code in _DRF_CODES.items():
        if isinstance(exc, exc_type):
            return code
    return "error"


def exception_handler(exc: Exception, context: Mapping[str, object]) -> Response | None:
    if isinstance(exc, PactorError):
        # Erro de negócio encerra a operação inteira: com ATOMIC_REQUESTS, a
        # transação da requisição é desfeita em vez de confirmar efeitos parciais.
        set_rollback()
        return Response(error_body(exc.code, exc.message, exc.details), status=exc.http_status)
    if isinstance(exc, Http404):
        exc = drf_exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = drf_exceptions.PermissionDenied()
    response = drf_exception_handler(exc, dict(context))
    if response is None or not isinstance(exc, drf_exceptions.APIException):
        return response
    if isinstance(exc, drf_exceptions.ValidationError):
        body = error_body("validation_failed", "Dados inválidos.", exc.detail)
    else:
        body = error_body(_drf_code(exc), str(exc.detail))
    response.data = body
    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        response["WWW-Authenticate"] = 'Session realm="pactor"'
    return response
