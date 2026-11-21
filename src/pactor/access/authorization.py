"""Aplicação das decisões de política, traduzidas em erros de domínio."""

from __future__ import annotations

from pactor.access.policies import Decision, Permission, Principal, decide
from pactor.core.errors import AccessDeniedError, MfaRequiredError


def authorize(principal: Principal, permission: Permission) -> None:
    match decide(principal, permission):
        case Decision.ALLOW:
            return
        case Decision.MFA_REQUIRED:
            raise MfaRequiredError(
                "Conclua a autenticação multifator para esta operação.",
                details={"permission": permission.value},
            )
        case Decision.DENY:
            raise AccessDeniedError(
                "Você não tem permissão para esta operação.",
                details={"permission": permission.value},
            )
