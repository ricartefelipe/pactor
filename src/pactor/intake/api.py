"""API de solicitações de contrato."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantViewSet
from pactor.access.policies import Confidentiality, Permission
from pactor.contracts.api import ContractSerializer
from pactor.contracts.domain import ContractType, PartyRole
from pactor.contracts.models import (
    CONFIDENTIALITY_CHOICES,
    CONTRACT_TYPE_CHOICES,
    PARTY_ROLE_CHOICES,
)
from pactor.core.api.filters import parse_filters
from pactor.core.api.pagination import StablePagination
from pactor.intake import services
from pactor.intake.models import ContractRequest, Priority, RequestEvent, RequestStatus


class RequestEventSerializer(serializers.ModelSerializer[RequestEvent]):
    class Meta:
        model = RequestEvent
        fields = ("id", "actor_id", "action", "from_status", "to_status", "note", "created_at")
        read_only_fields = fields


class RequestSerializer(serializers.ModelSerializer[ContractRequest]):
    requester_name = serializers.CharField(source="requester.full_name", read_only=True)
    counterparty_name = serializers.CharField(
        source="counterparty.name", read_only=True, default=None
    )
    events = RequestEventSerializer(many=True, read_only=True)

    class Meta:
        model = ContractRequest
        fields = (
            "id",
            "reference",
            "requester_id",
            "requester_name",
            "department_id",
            "contract_type",
            "counterparty_id",
            "counterparty_name",
            "title",
            "objective",
            "estimated_value",
            "desired_start",
            "desired_end",
            "assignee_id",
            "priority",
            "status",
            "contract_id",
            "version",
            "created_at",
            "updated_at",
            "events",
        )
        read_only_fields = fields


class RequestCreateSerializer(serializers.Serializer[ContractRequest]):
    title = serializers.CharField(max_length=200)
    contract_type = serializers.ChoiceField(choices=CONTRACT_TYPE_CHOICES)
    objective = serializers.CharField()
    priority = serializers.ChoiceField(choices=Priority.choices, default=Priority.NORMAL.value)
    department_id = serializers.UUIDField(required=False, allow_null=True)
    counterparty_id = serializers.UUIDField(required=False, allow_null=True)
    estimated_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True
    )
    desired_start = serializers.DateField(required=False, allow_null=True)
    desired_end = serializers.DateField(required=False, allow_null=True)


class ReasonSerializer(serializers.Serializer[None]):
    reason = serializers.CharField(max_length=500)


class ReviewSerializer(serializers.Serializer[None]):
    assignee_id = serializers.UUIDField(required=False, allow_null=True)


class ConvertSerializer(serializers.Serializer[None]):
    owner_id = serializers.UUIDField()
    organization_role = serializers.ChoiceField(choices=PARTY_ROLE_CHOICES)
    counterparty_role = serializers.ChoiceField(choices=PARTY_ROLE_CHOICES)
    confidentiality = serializers.ChoiceField(choices=CONFIDENTIALITY_CHOICES, default="internal")


class RequestFilterSerializer(serializers.Serializer[None]):
    status = serializers.ChoiceField(choices=RequestStatus.choices, required=False)


@extend_schema_view(list=extend_schema(parameters=[RequestFilterSerializer]))
class RequestViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, TenantViewSet):
    serializer_class = RequestSerializer
    pagination_class = StablePagination
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": None,
        "retrieve": None,
        "create": Permission.REQUESTS_CREATE,
        "review": Permission.REQUESTS_MANAGE,
        "reject": Permission.REQUESTS_MANAGE,
        "cancel": None,
        "convert": Permission.REQUESTS_MANAGE,
    }

    def get_queryset(self) -> QuerySet[ContractRequest]:
        queryset = services.requests_for(self.principal).prefetch_related("events")
        if self.action == "list":
            filters = parse_filters(self.request, RequestFilterSerializer)
            queryset = queryset.filter(**filters)
        return queryset

    def _detail(self, request_id: uuid.UUID, code: int = status.HTTP_200_OK) -> Response:
        return Response(RequestSerializer(self.get_queryset().get(id=request_id)).data, status=code)

    @extend_schema(request=RequestCreateSerializer, responses={201: RequestSerializer})
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = RequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        created = services.create_request(
            self.principal,
            services.RequestInput(
                **{
                    **data,
                    "contract_type": ContractType(data["contract_type"]),
                    "priority": Priority(data["priority"]),
                }
            ),
        )
        return self._detail(created.id, status.HTTP_201_CREATED)

    @extend_schema(request=ReviewSerializer, responses=RequestSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = ReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.start_review(
            self.principal, pk, assignee_id=serializer.validated_data.get("assignee_id")
        )
        return self._detail(pk)

    @extend_schema(request=ReasonSerializer, responses=RequestSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.reject(self.principal, pk, reason=serializer.validated_data["reason"])
        return self._detail(pk)

    @extend_schema(request=ReasonSerializer, responses=RequestSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.cancel(self.principal, pk, reason=serializer.validated_data["reason"])
        return self._detail(pk)

    @extend_schema(request=ConvertSerializer, responses={201: ContractSerializer})
    @action(detail=True, methods=["post"])
    def convert(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = ConvertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        contract = services.convert(
            self.principal,
            pk,
            owner_id=data["owner_id"],
            organization_role=PartyRole(data["organization_role"]),
            counterparty_role=PartyRole(data["counterparty_role"]),
            confidentiality=Confidentiality(data["confidentiality"]),
        )
        return Response(
            ContractSerializer(contract, context={"view": self}).data,
            status=status.HTTP_201_CREATED,
        )
