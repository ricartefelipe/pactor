"""API de vigência, encerramento, aditivos e condições vigentes."""

from __future__ import annotations

import uuid
from datetime import date
from typing import ClassVar, Protocol

from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantAPIView
from pactor.access.policies import Permission, Principal
from pactor.contracts import lifecycle_services as lifecycle
from pactor.contracts.lifecycle import ConditionField
from pactor.contracts.models import AmendmentChange, Contract, LifecycleEvent
from pactor.contracts.selectors import get_visible_contract
from pactor.core.errors import NotFoundError
from pactor.identity.models import Organization


class LifecycleEventSerializer(serializers.ModelSerializer[LifecycleEvent]):
    class Meta:
        model = LifecycleEvent
        fields = (
            "id",
            "action",
            "from_status",
            "to_status",
            "effective_date",
            "reason",
            "actor_id",
            "created_at",
        )
        read_only_fields = fields


class LifecycleSerializer(serializers.ModelSerializer[Contract]):
    events = serializers.SerializerMethodField()

    class Meta:
        model = Contract
        fields = (
            "id",
            "lifecycle_status",
            "signed_on",
            "effective_start",
            "effective_end",
            "events",
        )
        read_only_fields = fields

    @extend_schema_field(LifecycleEventSerializer(many=True))
    def get_events(self, obj: Contract) -> list[dict[str, object]]:
        events = LifecycleEvent.objects.filter(
            organization_id=obj.organization_id, contract=obj
        ).order_by("created_at", "id")
        return [dict(e) for e in LifecycleEventSerializer(events, many=True).data]


class LifecycleActionSerializer(serializers.Serializer[None]):
    reason = serializers.CharField(max_length=500)
    effective_date = serializers.DateField()


class AmendmentChangeSerializer(serializers.ModelSerializer[AmendmentChange]):
    class Meta:
        model = AmendmentChange
        fields = ("id", "field", "value", "effective_from", "parent_id", "created_at")
        read_only_fields = ("id", "parent_id", "created_at")


class ConditionsQuerySerializer(serializers.Serializer[None]):
    on = serializers.DateField(required=False)


class LifecycleOperation(Protocol):
    def __call__(
        self, principal: Principal, contract_id: uuid.UUID, *, reason: str, effective_date: date
    ) -> Contract: ...


_ACTIONS: dict[str, LifecycleOperation] = {
    "suspend": lifecycle.suspend,
    "resume": lifecycle.resume,
    "terminate": lifecycle.terminate,
    "archive": lifecycle.archive,
}


def _lifecycle_payload(principal: Principal, contract_id: uuid.UUID) -> dict[str, object]:
    return dict(LifecycleSerializer(get_visible_contract(principal, contract_id)).data)


class LifecycleView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(responses=LifecycleSerializer)
    def get(self, _request: Request, contract_id: uuid.UUID) -> Response:
        return Response(_lifecycle_payload(self.principal, contract_id))


class LifecycleActionView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "post": Permission.CONTRACTS_LIFECYCLE
    }

    @extend_schema(request=LifecycleActionSerializer, responses=LifecycleSerializer)
    def post(self, request: Request, contract_id: uuid.UUID, operation: str) -> Response:
        handler = _ACTIONS.get(operation)
        if handler is None:
            raise NotFoundError("Ação de vigência inexistente.")
        serializer = LifecycleActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        handler(self.principal, contract_id, **serializer.validated_data)
        return Response(_lifecycle_payload(self.principal, contract_id))


class AmendmentChangesView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "get": None,
        "post": Permission.CONTRACTS_EDIT,
    }

    @extend_schema(responses=AmendmentChangeSerializer(many=True))
    def get(self, _request: Request, contract_id: uuid.UUID) -> Response:
        contract = get_visible_contract(self.principal, contract_id)
        return Response(
            AmendmentChangeSerializer(lifecycle.amendment_changes(contract), many=True).data
        )

    @extend_schema(request=AmendmentChangeSerializer, responses={201: AmendmentChangeSerializer})
    def post(self, request: Request, contract_id: uuid.UUID) -> Response:
        serializer = AmendmentChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        change = lifecycle.add_amendment_change(
            self.principal,
            contract_id,
            field=ConditionField(data["field"]),
            value=data.get("value", ""),
            effective_from=data["effective_from"],
        )
        return Response(AmendmentChangeSerializer(change).data, status=status.HTTP_201_CREATED)


class ConditionsResponseSerializer(serializers.Serializer[None]):
    on = serializers.DateField()
    total_value = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    effective_end = serializers.DateField(allow_null=True)
    description = serializers.CharField(allow_blank=True)


class ConditionsView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(parameters=[ConditionsQuerySerializer], responses=ConditionsResponseSerializer)
    def get(self, request: Request, contract_id: uuid.UUID) -> Response:
        query = ConditionsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        organization = Organization.objects.get(id=self.principal.organization_id)
        on: date = query.validated_data.get("on") or lifecycle.local_today(organization)
        conditions = lifecycle.current_conditions(self.principal, contract_id, on=on)
        return Response(
            {
                "on": on.isoformat(),
                "total_value": None
                if conditions.total_value is None
                else f"{conditions.total_value:.2f}",
                "effective_end": None
                if conditions.effective_end is None
                else conditions.effective_end.isoformat(),
                "description": conditions.description,
            }
        )
