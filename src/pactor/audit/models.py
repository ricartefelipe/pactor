"""Trilha de auditoria somente-inserção."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.utils import timezone

from pactor.core.models import TenantModel


class AuditEvent(TenantModel):
    """Operação relevante: quem, onde, o quê, quando e quais alterações.

    ``changes`` passa por redação antes da gravação e nunca contém senhas,
    tokens, cookies ou o conteúdo integral de documentos.
    """

    actor_id = models.UUIDField(null=True, blank=True)
    actor_label = models.CharField(max_length=200)
    action = models.CharField(max_length=80)
    resource_type = models.CharField(max_length=60)
    resource_id = models.CharField(max_length=64)
    changes = models.JSONField(default=dict)
    request_id = models.CharField(max_length=64, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organization", "-occurred_at"], name="audit_org_time_idx"),
            models.Index(
                fields=["organization", "resource_type", "resource_id"], name="audit_res_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action}:{self.resource_type}:{self.resource_id}"


class RetentionHold(TenantModel):
    """Bloqueio de descarte: enquanto ativo, registros do recurso são preservados."""

    resource_type = models.CharField(max_length=60)
    resource_id = models.CharField(max_length=64)
    reason = models.CharField(max_length=300)
    placed_by = models.UUIDField()
    released_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"hold:{self.resource_type}:{self.resource_id}"
