"""Autenticação de integrações por token Bearer no cabeçalho.

Delega a validação ao Django OAuth Toolkit, mas só quando há
``Authorization: Bearer``. Tokens no corpo ou na query string não são
aceitos, e requisições de sessão não têm o corpo lido pelo validador OAuth.
"""

from __future__ import annotations

from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from oauth2_provider.models import AccessToken
from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request

from pactor.identity.models import User

_BEARER = "bearer "


class BearerTokenAuthentication(BaseAuthentication):
    def __init__(self) -> None:
        self._delegate = OAuth2Authentication()

    def authenticate(self, request: Request) -> tuple[User | None, AccessToken] | None:
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.lower().startswith(_BEARER):
            return None
        result = self._delegate.authenticate(request)
        if result is None:
            return None
        user, token = result
        if not isinstance(token, AccessToken):
            return None
        return (user if isinstance(user, User) else None), token

    def authenticate_header(self, request: Request) -> str:
        return str(self._delegate.authenticate_header(request))
