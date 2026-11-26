"""Base das views de API que operam dentro de uma organização.

Cada requisição já roda em transação (``ATOMIC_REQUESTS``). Depois da
autenticação, :meth:`TenantAPIView.initial` relê a associação do usuário e
vincula a transação à organização ativa via ``set_config`` local: todas as
consultas da requisição passam pelas policies de RLS e expiram com ela.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from oauth2_provider.models import AccessToken
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.access.principal import activate_request_principal
from pactor.core import idempotency
from pactor.core.errors import AccessDeniedError
from pactor.core.observability import bind_log_context, entity_correlation, extend_log_context
from pactor.identity.models import User
from pactor.integrations.services import principal_for_token

#: Cabeçalho exigido pelas operações idempotentes (documentado no esquema OpenAPI).
IDEMPOTENCY_KEY = OpenApiParameter(
    "Idempotency-Key",
    OpenApiTypes.STR,
    OpenApiParameter.HEADER,
    required=True,
    description="Chave única por operação (8 a 128 caracteres: letras, números, - e _). "
    "Repetir a mesma chave com o mesmo corpo devolve o resultado original.",
)

if TYPE_CHECKING:
    _GenericViewSet = GenericViewSet[Any]
else:
    # As classes do DRF não são genéricas em tempo de execução.
    _GenericViewSet = GenericViewSet


class TenantScopedMixin(APIView):
    """Resolve o principal e exige a permissão declarada para cada ação."""

    #: Permissão por ação (nome do handler/ação do viewset). ``"*"`` vale para as demais.
    action_permissions: ClassVar[dict[str, Permission | None]] = {}
    #: Chave de correlação de logs representada pelo ``pk`` da rota (ex.: ``"contract_id"``).
    pk_log_key: ClassVar[str | None] = None
    principal: Principal

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        route = dict(kwargs)
        if self.pk_log_key is not None and "pk" in route:
            route[self.pk_log_key] = route["pk"]
        with bind_log_context(**entity_correlation(route)):
            return super().dispatch(request, *args, **kwargs)

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        if isinstance(request.auth, AccessToken):
            # Integração máquina a máquina: tenant e papéis vêm do cliente OAuth.
            self.principal = principal_for_token(request.auth)
        else:
            user = request.user
            if not isinstance(user, User):
                raise AccessDeniedError("Autenticação obrigatória.")
            self.principal = activate_request_principal(request, user)
        extend_log_context(organization_id=str(self.principal.organization_id))
        permission = self._required_permission(request)
        if permission is not None:
            authorize(self.principal, permission)

    def _required_permission(self, request: Request) -> Permission | None:
        method = (request.method or "").lower()
        if method not in self.http_method_names or method == "options":
            # Método fora do HTTP (ex.: LIST, que coincidiria com um atributo da view) ou
            # OPTIONS, que a API não oferece: 405, como o DRF responderia no dispatch.
            raise MethodNotAllowed(request.method or "")
        if method == "head":
            # O Django atende HEAD com o handler de GET; a permissão é a mesma.
            method = "get"
        action = getattr(self, "action", None) or method
        if action in self.action_permissions:
            return self.action_permissions[action]
        if "*" in self.action_permissions:
            return self.action_permissions["*"]
        if getattr(self, method, None) is None:
            # Método sem handler na rota (ex.: PATCH numa ação só de leitura).
            raise MethodNotAllowed(request.method or "")
        # Handler existente sem permissão declarada é erro de programação: falha fechada.
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
        # Grava exatamente o JSON que o cliente recebe (UUIDs, datas e decimais
        # já convertidos), para que a repetição devolva a mesma representação.
        rendered = (
            json.loads(JSONRenderer().render(response.data)) if response.data is not None else None
        )
        body = rendered if isinstance(rendered, dict | list) else None
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
    """ViewSet vinculado à organização ativa. Identificadores de recurso são UUID."""

    lookup_value_converter = "uuid"
