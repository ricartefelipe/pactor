"""Modelos de contrato e versões publicadas (imutáveis)."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.utils import timezone

from pactor.contracts.models import CONTRACT_TYPE_CHOICES
from pactor.core.models import TenantModel


class ContractTemplate(TenantModel):
    """Modelo editável: o rascunho da definição vive aqui até ser publicado."""

    name = models.CharField(max_length=160)
    contract_type = models.CharField(max_length=12, choices=CONTRACT_TYPE_CHOICES)
    description = models.TextField(blank=True)
    draft_definition = models.JSONField(default=dict)
    draft_version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["organization", "name"], name="template_name_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class TemplateVersion(TenantModel):
    """Versão publicada. Somente-inserção: alterações geram nova versão."""

    template = models.ForeignKey(
        ContractTemplate, on_delete=models.PROTECT, related_name="versions"
    )
    number = models.PositiveIntegerField()
    definition = models.JSONField()
    change_note = models.CharField(max_length=300, blank=True)
    published_by = models.ForeignKey("identity.User", on_delete=models.PROTECT, related_name="+")

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["template", "number"], name="template_version_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.template_id} v{self.number}"
