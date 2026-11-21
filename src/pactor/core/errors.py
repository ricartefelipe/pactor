"""Exceções de domínio com código estável.

As camadas de aplicação lançam estas exceções; a camada HTTP as traduz para
respostas estruturadas sem depender de detalhes do framework no domínio.
"""

from __future__ import annotations

from collections.abc import Mapping


class PactorError(Exception):
    """Erro de negócio esperado, com código estável para clientes da API."""

    code: str = "error"
    http_status: int = 400

    def __init__(self, message: str, *, details: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = dict(details or {})


class DomainValidationError(PactorError):
    code = "validation_failed"
    http_status = 400


class AccessDeniedError(PactorError):
    code = "permission_denied"
    http_status = 403


class NotFoundError(PactorError):
    """Recurso inexistente ou invisível para o solicitante.

    A mesma resposta é usada nos dois casos para não permitir inferir a
    existência de registros de outra organização ou confidenciais.
    """

    code = "not_found"
    http_status = 404


class ConflictError(PactorError):
    code = "conflict"
    http_status = 409


class StaleVersionError(ConflictError):
    """Controle de concorrência otimista: o registro mudou desde a leitura."""

    code = "stale_version"


class InvalidTransitionError(ConflictError):
    code = "invalid_transition"


class PlanLimitExceededError(PactorError):
    code = "plan_limit_exceeded"
    http_status = 402


class TenantContextMissingError(PactorError):
    """Operação dependente de tenant executada sem contexto definido."""

    code = "tenant_context_missing"
    http_status = 403
