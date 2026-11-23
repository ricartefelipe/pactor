"""Documento de pesquisa por contrato (full-text do PostgreSQL)."""

from __future__ import annotations

from typing import ClassVar

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone

from pactor.core.models import TenantModel


class SearchDocument(TenantModel):
    """Índice de um contrato, sempre associado ao número da revisão indexada.

    A atualização só ocorre quando a revisão do job é igual ou mais nova que a
    indexada: um job atrasado nunca substitui o índice de uma revisão posterior.
    """

    contract = models.OneToOneField(
        "contracts.Contract", on_delete=models.CASCADE, related_name="search_document"
    )
    revision_number = models.PositiveIntegerField(default=0)
    body = models.TextField(blank=True)
    vector = SearchVectorField()
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        indexes: ClassVar[list[models.Index]] = [
            GinIndex(fields=["vector"], name="search_vector_gin")
        ]

    def __str__(self) -> str:
        return f"busca:{self.contract_id}:r{self.revision_number}"
