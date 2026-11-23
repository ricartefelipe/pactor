"""Tradução de exceções para o formato único de erro da API."""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions as drf
from rest_framework.response import Response

from pactor.core import errors
from pactor.core.api.errors import exception_handler
from pactor.core.api.pagination import StablePagination
from pactor.core.observability import bind_log_context


def _handle(exc: Exception) -> Response:
    response = exception_handler(exc, {})
    assert response is not None
    return response


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (errors.DomainValidationError("x"), 400, "validation_failed"),
        (errors.AccessDeniedError("x"), 403, "permission_denied"),
        (errors.NotFoundError("x"), 404, "not_found"),
        (errors.ConflictError("x"), 409, "conflict"),
        (errors.StaleVersionError("x"), 409, "stale_version"),
        (errors.InvalidTransitionError("x"), 409, "invalid_transition"),
        (errors.PlanLimitExceededError("x"), 402, "plan_limit_exceeded"),
        (errors.TenantContextMissingError("x"), 403, "tenant_context_missing"),
    ],
)
def test_domain_errors_keep_stable_code_and_status(
    exc: errors.PactorError, status: int, code: str
) -> None:
    response = _handle(exc)
    assert response.status_code == status
    assert response.data["error"]["code"] == code


def test_domain_error_details_and_request_id_are_included() -> None:
    with bind_log_context(request_id="req-12345678"):
        response = _handle(errors.ConflictError("mudou", details={"version": 3}))
    assert response.data == {
        "error": {
            "code": "conflict",
            "message": "mudou",
            "details": {"version": 3},
            "request_id": "req-12345678",
        },
    }


def test_validation_error_exposes_field_details() -> None:
    response = _handle(drf.ValidationError({"email": ["Obrigatório."]}))
    assert response.status_code == 400
    assert response.data["error"]["code"] == "validation_failed"
    assert response.data["error"]["details"] == {"email": ["Obrigatório."]}


def test_django_404_and_permission_denied_are_normalized() -> None:
    assert _handle(Http404()).data["error"]["code"] == "not_found"
    assert _handle(DjangoPermissionDenied()).data["error"]["code"] == "permission_denied"


def test_throttled_keeps_retry_after_rounded_up() -> None:
    response = _handle(drf.Throttled(wait=12.4))
    assert response.status_code == 429
    assert response.data["error"]["code"] == "rate_limited"
    assert response["Retry-After"] == "13"


def test_not_authenticated_keeps_drf_status_and_code() -> None:
    response = _handle(drf.NotAuthenticated())
    assert response.status_code == 401
    assert response.data["error"]["code"] == "not_authenticated"


def test_unmapped_api_exception_uses_generic_code() -> None:
    class Teapot(drf.APIException):
        status_code = 418
        default_detail = "chaleira"

    assert _handle(Teapot()).data["error"]["code"] == "error"


def test_unexpected_exceptions_are_not_swallowed() -> None:
    assert exception_handler(RuntimeError("boom"), {}) is None


def test_pagination_is_bounded_and_deterministic() -> None:
    assert StablePagination.max_page_size == 100
    assert StablePagination.ordering[-1] == "-id"
