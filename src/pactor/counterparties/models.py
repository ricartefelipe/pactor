"""Contrapartes (pessoas físicas ou jurídicas) e seus contatos."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.utils import timezone

from pactor.core.models import TenantModel
from pactor.counterparties.domain import PartyKind

PARTY_KIND_CHOICES = [
    (PartyKind.INDIVIDUAL.value, "Pessoa física"),
    (PartyKind.COMPANY.value, "Pessoa jurídica"),
]


class Counterparty(TenantModel):
    kind = models.CharField(max_length=12, choices=PARTY_KIND_CHOICES)
    name = models.CharField(max_length=200)
    trade_name = models.CharField("nome fantasia", max_length=200, blank=True)
    document = models.CharField("CPF/CNPJ (somente dígitos)", max_length=14)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["organization", "document"], name="counterparty_document_unique"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organization", "name"], name="counterparty_name_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class Contact(TenantModel):
    counterparty = models.ForeignKey(
        Counterparty, on_delete=models.CASCADE, related_name="contacts"
    )
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    position = models.CharField("cargo/área", max_length=120, blank=True)

    def __str__(self) -> str:
        return self.name


class Address(TenantModel):
    counterparty = models.ForeignKey(
        Counterparty, on_delete=models.CASCADE, related_name="addresses"
    )
    label = models.CharField(max_length=60, default="Principal")
    street = models.CharField(max_length=200)
    number = models.CharField(max_length=20, blank=True)
    complement = models.CharField(max_length=120, blank=True)
    district = models.CharField(max_length=120, blank=True)
    city = models.CharField(max_length=120)
    state = models.CharField("UF", max_length=2)
    postal_code = models.CharField("CEP", max_length=8)

    def __str__(self) -> str:
        return f"{self.street}, {self.city}/{self.state}"


class Representative(TenantModel):
    """Representante legal ou procurador da contraparte."""

    counterparty = models.ForeignKey(
        Counterparty, on_delete=models.CASCADE, related_name="representatives"
    )
    name = models.CharField(max_length=200)
    document = models.CharField("CPF (somente dígitos)", max_length=11)
    email = models.EmailField(blank=True)
    capacity = models.CharField("qualificação", max_length=120, blank=True)

    def __str__(self) -> str:
        return self.name
