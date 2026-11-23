"""Clientes de integração máquina a máquina vinculados a uma organização."""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models

from pactor.core.models import TenantModel
from pactor.identity.models import ROLE_CHOICES


class IntegrationClient(TenantModel):
    """Cliente OAuth (client credentials) com papéis e escopos explícitos.

    O cliente age como principal do tipo integração: os papéis definem o que
    ele poderia fazer, os escopos do token restringem esse conjunto, e a
    visibilidade de cada contrato continua valendo.
    """

    # Identificador da aplicação do servidor OAuth (tabela global do Django OAuth Toolkit).
    application_id = models.BigIntegerField(unique=True)
    oauth_client_id = models.CharField(max_length=100)
    name = models.CharField(max_length=120)
    roles = ArrayField(models.CharField(max_length=20, choices=ROLE_CHOICES), default=list)
    scopes = ArrayField(models.CharField(max_length=40), default=list)
    created_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    # Usuário de serviço (inativo, sem senha e sem associação) que identifica a
    # integração como autora de solicitações, eventos e registros de auditoria.
    service_user = models.OneToOneField(
        "identity.User", on_delete=models.PROTECT, related_name="integration_client"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name


class IntegrationRoute(models.Model):
    """Aplicação OAuth → organização. Global (sem RLS): o token chega sem tenant."""

    application_id = models.BigIntegerField(unique=True)
    organization_id = models.UUIDField()
    client_id = models.UUIDField()
    # Escopos que o servidor de autorização pode conceder a esta aplicação.
    allowed_scopes = ArrayField(models.CharField(max_length=40), default=list)

    def __str__(self) -> str:
        return f"{self.application_id}->{self.organization_id}"
