"""API de contrapartes."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.db.models import Q, QuerySet
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantViewSet
from pactor.access.policies import Permission
from pactor.core.api.filters import parse_filters
from pactor.core.api.pagination import StablePagination
from pactor.counterparties import services
from pactor.counterparties.domain import PartyKind
from pactor.counterparties.models import (
    PARTY_KIND_CHOICES,
    Address,
    Contact,
    Counterparty,
    Representative,
)


class ContactSerializer(serializers.ModelSerializer[Contact]):
    class Meta:
        model = Contact
        fields = ("id", "name", "email", "phone", "position")
        read_only_fields = ("id",)


class AddressSerializer(serializers.ModelSerializer[Address]):
    # Aceita CEP formatado (00000-000); a normalização ocorre no caso de uso.
    postal_code = serializers.CharField(max_length=9)

    class Meta:
        model = Address
        fields = (
            "id",
            "label",
            "street",
            "number",
            "complement",
            "district",
            "city",
            "state",
            "postal_code",
        )
        read_only_fields = ("id",)


class RepresentativeSerializer(serializers.ModelSerializer[Representative]):
    # Aceita CPF formatado; dígitos verificadores são validados no caso de uso.
    document = serializers.CharField(max_length=14)

    class Meta:
        model = Representative
        fields = ("id", "name", "document", "email", "capacity")
        read_only_fields = ("id",)


class CounterpartySerializer(serializers.ModelSerializer[Counterparty]):
    # Aceita CPF/CNPJ formatado; a validação e a unicidade ficam no caso de uso.
    document = serializers.CharField(max_length=18)
    contacts = ContactSerializer(many=True, required=False)
    addresses = AddressSerializer(many=True, required=False)
    representatives = RepresentativeSerializer(many=True, required=False)

    class Meta:
        model = Counterparty
        fields = (
            "id",
            "kind",
            "name",
            "trade_name",
            "document",
            "email",
            "phone",
            "notes",
            "is_active",
            "version",
            "created_at",
            "updated_at",
            "contacts",
            "addresses",
            "representatives",
        )
        read_only_fields = ("id", "is_active", "version", "created_at", "updated_at")


class CounterpartyUpdateSerializer(serializers.Serializer[Counterparty]):
    expected_version = serializers.IntegerField(min_value=1)
    name = serializers.CharField(max_length=200, required=False)
    trade_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)


class CounterpartyFilterSerializer(serializers.Serializer[None]):
    q = serializers.CharField(required=False, max_length=100)
    kind = serializers.ChoiceField(choices=PARTY_KIND_CHOICES, required=False)
    is_active = serializers.BooleanField(required=False, allow_null=True, default=None)


class CounterpartyPagination(StablePagination):
    ordering = ("name", "id")


class CounterpartyViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, TenantViewSet
):
    serializer_class = CounterpartySerializer
    pagination_class = CounterpartyPagination
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": Permission.COUNTERPARTIES_READ,
        "retrieve": Permission.COUNTERPARTIES_READ,
        "create": Permission.COUNTERPARTIES_WRITE,
        "partial_update": Permission.COUNTERPARTIES_WRITE,
    }

    def get_queryset(self) -> QuerySet[Counterparty]:
        queryset = services.counterparties_for(self.principal)
        if self.action != "list":
            return queryset
        filters = parse_filters(self.request, CounterpartyFilterSerializer)
        if "q" in filters:
            term = filters["q"]
            queryset = queryset.filter(
                Q(name__icontains=term) | Q(trade_name__icontains=term) | Q(document=term)
            )
        if "kind" in filters:
            queryset = queryset.filter(kind=filters["kind"])
        if "is_active" in filters:
            queryset = queryset.filter(is_active=filters["is_active"])
        return queryset

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = CounterpartySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        counterparty = services.create_counterparty(
            self.principal,
            services.CounterpartyData(
                kind=PartyKind(data["kind"]),
                name=data["name"],
                document=data["document"],
                trade_name=data.get("trade_name", ""),
                email=data.get("email", ""),
                phone=data.get("phone", ""),
                notes=data.get("notes", ""),
                contacts=[services.ContactData(**c) for c in data.get("contacts", [])],
                addresses=[services.AddressData(**a) for a in data.get("addresses", [])],
                representatives=[
                    services.RepresentativeData(**r) for r in data.get("representatives", [])
                ],
            ),
        )
        fresh = services.get_counterparty(self.principal, counterparty.id)
        return Response(CounterpartySerializer(fresh).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=CounterpartyUpdateSerializer, responses=CounterpartySerializer)
    def partial_update(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = CounterpartyUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        expected = changes.pop("expected_version")
        counterparty = services.update_counterparty(
            self.principal, pk, expected_version=expected, changes=changes
        )
        return Response(CounterpartySerializer(counterparty).data)
