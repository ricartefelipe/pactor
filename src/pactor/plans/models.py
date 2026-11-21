"""Planos comerciais e assinatura de cada organização no Pactor.

Este módulo trata exclusivamente da relação comercial entre a organização e a
plataforma. Contratos administrados pela organização ficam em ``contracts`` e
não têm relação com estes registros.
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from pactor.core.models import TenantModel


class Module(models.TextChoices):
    APPROVALS = "approvals", "Workflows de aprovação"
    SIGNATURES = "signatures", "Assinaturas eletrônicas"
    OBLIGATIONS = "obligations", "Obrigações e prazos"
    FINANCE = "finance", "Valores e compromissos"
    SEARCH = "search", "Pesquisa de conteúdo"
    INTEGRATIONS = "integrations", "Integrações via API"


class Plan(models.Model):
    """Plano comercial. Preços são hipóteses de produto, não tabela oficial."""

    code = models.SlugField(max_length=40, primary_key=True)
    name = models.CharField(max_length=80)
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2)
    max_users = models.PositiveIntegerField()
    max_active_contracts = models.PositiveIntegerField()
    max_storage_bytes = models.PositiveBigIntegerField()
    modules = ArrayField(models.CharField(max_length=20, choices=Module.choices), default=list)
    is_public = models.BooleanField(default=True)

    class Meta:
        verbose_name = "plano"

    def __str__(self) -> str:
        return self.name


class SubscriptionStatus(models.TextChoices):
    TRIALING = "trialing", "Em avaliação"
    ACTIVE = "active", "Ativa"
    PAST_DUE = "past_due", "Pagamento pendente"
    CANCELED = "canceled", "Cancelada"


class Subscription(TenantModel):
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=10, choices=SubscriptionStatus.choices)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["organization"], name="subscription_per_organization"),
            models.CheckConstraint(
                condition=~models.Q(status="trialing") | models.Q(trial_ends_at__isnull=False),
                name="subscription_trial_has_end",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.plan_id}:{self.status}"
