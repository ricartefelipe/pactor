"""Permissão base da API: sessão autenticada ou token de integração válido."""

from __future__ import annotations

from typing import TYPE_CHECKING

from oauth2_provider.models import AccessToken
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

if TYPE_CHECKING:
    # Importar APIView em tempo de execução cria ciclo: rest_framework.views carrega
    # esta classe a partir das configurações durante a própria importação.
    from rest_framework.views import APIView


class IsAuthenticatedOrIntegration(BasePermission):
    """Sessão de usuário autenticado ou token OAuth válido (client credentials).

    Em tokens de client credentials não há usuário: o principal da integração
    é resolvido depois, a partir do cliente vinculado à aplicação.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        del view
        if isinstance(request.auth, AccessToken):
            return not request.auth.is_expired()
        return bool(request.user.is_authenticated)
