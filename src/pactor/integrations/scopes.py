"""Backend de escopos do servidor OAuth: cada aplicação só recebe os escopos do seu cliente."""

from __future__ import annotations

from pactor.integrations.models import IntegrationRoute
from pactor.integrations.services import INTEGRATION_SCOPES


class IntegrationScopes:
    """Implementa a interface de ``oauth2_provider.scopes.BaseScopes`` (tipagem estrutural).

    O Django OAuth Toolkit instancia a classe configurada e chama os métodos por
    palavra-chave; não há verificação de herança.
    """

    def get_all_scopes(self) -> dict[str, str]:
        return dict(INTEGRATION_SCOPES)

    def get_available_scopes(
        self, application: object = None, request: object = None, *args: object, **kwargs: object
    ) -> list[str]:
        del request, args, kwargs
        application_id = getattr(application, "id", None)
        if not isinstance(application_id, int):
            return []
        route = IntegrationRoute.objects.filter(application_id=application_id).first()
        return list(route.allowed_scopes) if route else []

    def get_default_scopes(
        self, application: object = None, request: object = None, *args: object, **kwargs: object
    ) -> list[str]:
        return self.get_available_scopes(application, request, *args, **kwargs)
