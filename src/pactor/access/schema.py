"""Descrição da autenticação Bearer de integrações no esquema OpenAPI."""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema


class BearerTokenScheme(OpenApiAuthenticationExtension):
    target_class = "pactor.access.authentication.BearerTokenAuthentication"
    name = "oauth2ClientCredentials"

    def get_security_definition(self, auto_schema: AutoSchema) -> dict[str, object]:
        del auto_schema
        return {
            "type": "oauth2",
            "flows": {"clientCredentials": {"tokenUrl": "/o/token/", "scopes": {}}},
            "description": "Token de integração no cabeçalho Authorization: Bearer.",
        }
