"""Gestão de clientes de integração e resolução do principal a partir do token."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from django.utils import timezone
from oauth2_provider.generators import generate_client_id, generate_client_secret
from oauth2_provider.models import AccessToken, Application

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal, PrincipalKind, Role
from pactor.audit import services as audit
from pactor.core.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from pactor.core.tenancy import bind_tenant, require_tenant
from pactor.integrations.models import IntegrationClient, IntegrationRoute
from pactor.plans import services as plans
from pactor.plans.models import Module

# Escopos oferecidos a integrações: subconjunto das permissões do produto.
INTEGRATION_SCOPES: dict[str, str] = {
    Permission.CONTRACTS_READ.value: "Ler contratos visíveis ao cliente",
    Permission.COUNTERPARTIES_READ.value: "Ler contrapartes",
    Permission.COUNTERPARTIES_WRITE.value: "Cadastrar e alterar contrapartes",
    Permission.REQUESTS_CREATE.value: "Criar solicitações de contrato",
    Permission.FINANCE_READ.value: "Ler valores e compromissos",
    Permission.REPORTS_READ.value: "Ler relatórios e exportações",
}
# Papéis de gestão nunca são atribuídos a integrações.
FORBIDDEN_ROLES = frozenset({Role.OWNER, Role.ADMIN})


@dataclass(frozen=True, slots=True)
class IssuedCredentials:
    client: IntegrationClient
    client_id: str
    client_secret: str


def create_client(
    principal: Principal, *, name: str, roles: Iterable[str], scopes: Iterable[str]
) -> IssuedCredentials:
    """Cria o cliente e devolve o segredo uma única vez (armazenado apenas como hash)."""
    authorize(principal, Permission.INTEGRATIONS_MANAGE)
    plans.ensure_module(Module.INTEGRATIONS)
    if not name.strip():
        raise DomainValidationError("Informe o nome da integração.")
    try:
        parsed_roles = sorted({Role(r) for r in roles})
    except ValueError as exc:
        raise DomainValidationError("Papel desconhecido.") from exc
    if not parsed_roles or set(parsed_roles) & FORBIDDEN_ROLES:
        raise DomainValidationError(
            "Integrações recebem ao menos um papel operacional, nunca de gestão."
        )
    wanted = sorted(set(scopes))
    unknown = [s for s in wanted if s not in INTEGRATION_SCOPES]
    if not wanted or unknown:
        raise DomainValidationError("Escopos inválidos.", details={"unknown": unknown})
    organization_id = require_tenant()
    client_id, secret = generate_client_id(), generate_client_secret()
    application = Application.objects.create(
        name=f"pactor:{organization_id}:{name.strip()[:60]}",
        client_id=client_id,
        client_secret=secret,
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
        skip_authorization=True,
    )
    client = IntegrationClient.objects.create(
        organization_id=organization_id,
        application_id=application.id,
        oauth_client_id=client_id,
        name=name.strip()[:120],
        roles=[r.value for r in parsed_roles],
        scopes=wanted,
        created_by_id=principal.actor_id,
    )
    IntegrationRoute.objects.create(
        application_id=application.id,
        organization_id=organization_id,
        client_id=client.id,
        allowed_scopes=wanted,
    )
    audit.record(
        principal,
        action="integration.created",
        resource_type="integration",
        resource_id=client.id,
        changes={"name": client.name, "roles": client.roles, "scopes": wanted},
    )
    return IssuedCredentials(client=client, client_id=client_id, client_secret=secret)


def revoke_client(principal: Principal, client_id: uuid.UUID) -> IntegrationClient:
    authorize(principal, Permission.INTEGRATIONS_MANAGE)
    client = (
        IntegrationClient.objects.select_for_update()
        .filter(organization_id=require_tenant(), id=client_id)
        .first()
    )
    if client is None:
        raise NotFoundError("Integração não encontrada.")
    if client.revoked_at is not None:
        raise ConflictError("Integração já revogada.")
    client.revoked_at = timezone.now()
    client.save(update_fields=["revoked_at"])
    AccessToken.objects.filter(application_id=client.application_id).delete()
    audit.record(
        principal, action="integration.revoked", resource_type="integration", resource_id=client.id
    )
    return client


def principal_for_token(token: AccessToken) -> Principal:
    """Resolve o principal da integração e vincula a transação ao tenant do cliente."""
    route = IntegrationRoute.objects.filter(application_id=token.application_id).first()
    if route is None:
        raise AccessDeniedError("Token sem integração vinculada.")
    bind_tenant(route.organization_id)
    client = IntegrationClient.objects.filter(id=route.client_id, revoked_at__isnull=True).first()
    if client is None:
        raise AccessDeniedError("Integração revogada.")
    granted = set(token.scope.split()) & set(client.scopes)
    return Principal(
        organization_id=client.organization_id,
        actor_id=client.id,
        kind=PrincipalKind.INTEGRATION,
        roles=frozenset(Role(r) for r in client.roles),
        scopes=frozenset(granted),
    )
