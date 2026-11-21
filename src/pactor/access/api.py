"""Base das views de API que operam dentro de uma organização.

Cada requisição já roda em transação (``ATOMIC_REQUESTS``). Depois da
autenticação, :meth:`TenantAPIView.initial` relê a associação do usuário e
vincula a transação à organização ativa via ``set_config`` local: todas as
consultas da requisição passam pelas policies de RLS e expiram com ela.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.access.principal import activate_request_principal
from pactor.core import idempotency
from pactor.identity.models import User

if TYPE_CHECKING:
    _GenericViewSet = GenericViewSet[Any]
else:
    # As classes do DRF não são genéricas em tempo de execução.
    _GenericViewSet = GenericViewSet


class TenantScopedMixin(APIView):
    """Resolve o principal e exige a permissão declarada para cada ação."""

    #: Permissão por ação (nome do handler/ação do viewset). ``"*"`` vale para as demais.
    action_permissions: ClassVar[dict[str, Permission | None]] = {}
    principal: Principal

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        user = request.user
        if not isinstance(user, User):
            # IsAuthenticated já rejeitou anônimos; integrações usam outra base.
            raise TypeError("TenantScopedMixin exige usuário autenticado por sessão.")
        self.principal = activate_request_principal(request, user)
        permission = self._required_permission(request)
        if permission is not None:
            authorize(self.principal, permission)

    def _required_permission(self, request: Request) -> Permission | None:
        action = getattr(self, "action", None) or (request.method or "").lower()
        if action in self.action_permissions:
            return self.action_permissions[action]
        if "*" in self.action_permissions:
            return self.action_permissions["*"]
        raise LookupError(f"Ação sem permissão declarada: {type(self).__name__}.{action}")

    def idempotent(
        self, request: Request, scope: str, operation: Callable[[], Response]
    ) -> Response:
        """Executa ``operation`` sob a chave do cabeçalho ``Idempotency-Key``."""
        key = idempotency.validate_key(request.headers.get("Idempotency-Key"))
        fingerprint = idempotency.request_fingerprint(
            request.method or "", request.path, request.data
        )
        stored = idempotency.begin(
            actor_id=self.principal.actor_id, scope=scope, key=key, fingerprint=fingerprint
        )
        if stored is not None:
            replay = Response(stored.body, status=stored.status)
            replay["Idempotent-Replayed"] = "true"
            return replay
        response = operation()
        body = response.data if isinstance(response.data, dict | list) else None
        idempotency.complete(
            actor_id=self.principal.actor_id,
            scope=scope,
            key=key,
            status=response.status_code,
            body=body,
        )
        return response


class TenantAPIView(TenantScopedMixin):
    """APIView vinculada à organização ativa."""


class TenantViewSet(TenantScopedMixin, _GenericViewSet):
    """ViewSet vinculado à organização ativa."""
