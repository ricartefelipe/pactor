"""API de valores, parcelas, confirmações, índices e reajustes."""

from __future__ import annotations

import uuid
from typing import ClassVar

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantAPIView
from pactor.access.policies import Permission
from pactor.deadlines.calendar import Frequency
from pactor.finance import services
from pactor.finance.models import AdjustmentRecord, IndexValue, Installment, PaymentConfirmation


class DecimalTextField(serializers.CharField):
    """Valor decimal enviado como texto.

    Números JSON chegam ao Python como ``float`` e podem perder precisão; por
    isso são recusados em campos monetários e de taxas.
    """

    def to_internal_value(self, data: object) -> str:
        if not isinstance(data, str):
            raise serializers.ValidationError("Envie o valor como texto, ex.: \"1500.00\".")
        return str(super().to_internal_value(data))


class ConfirmationSerializer(serializers.ModelSerializer[PaymentConfirmation]):
    class Meta:
        model = PaymentConfirmation
        fields = ("amount", "confirmed_on", "reference", "confirmed_by_id", "created_at")
        read_only_fields = fields


class InstallmentSerializer(serializers.ModelSerializer[Installment]):
    confirmation = ConfirmationSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Installment
        fields = (
            "id",
            "sequence",
            "kind",
            "description",
            "due_date",
            "amount",
            "currency",
            "status",
            "version",
            "confirmation",
        )
        read_only_fields = fields


class SummarySerializer(serializers.Serializer[services.FinancialSummary]):
    contracted = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    projected = serializers.DecimalField(max_digits=14, decimal_places=2)
    confirmed = serializers.DecimalField(max_digits=14, decimal_places=2)
    overdue = serializers.DecimalField(max_digits=14, decimal_places=2)
    currency = serializers.CharField()
    installments = InstallmentSerializer(many=True)


class ScheduleSerializer(serializers.Serializer[None]):
    total = DecimalTextField(max_length=20)
    parts = serializers.IntegerField(min_value=1, max_value=services.MAX_INSTALLMENTS)
    first_due = serializers.DateField()
    frequency = serializers.ChoiceField(choices=[(f.value, f.value) for f in Frequency])
    description = serializers.CharField(
        max_length=150, required=False, allow_blank=True, default="Parcela"
    )


class MilestoneSerializer(serializers.Serializer[None]):
    description = serializers.CharField(max_length=200)
    due_date = serializers.DateField()
    amount = DecimalTextField(max_length=20)


class ConfirmSerializer(serializers.Serializer[None]):
    amount = DecimalTextField(max_length=20)
    confirmed_on = serializers.DateField()
    reference = serializers.CharField(max_length=120)


class IndexValueSerializer(serializers.ModelSerializer[IndexValue]):
    series_name = serializers.CharField(source="series.name", read_only=True)
    index_source = serializers.CharField(source="series.source", read_only=True)
    is_demonstrative = serializers.BooleanField(source="series.is_demonstrative", read_only=True)

    class Meta:
        model = IndexValue
        fields = (
            "id",
            "series_name",
            "index_source",
            "is_demonstrative",
            "period",
            "rate_percent",
            "source_note",
            "created_at",
        )
        read_only_fields = fields


class IndexValueCreateSerializer(serializers.Serializer[None]):
    series_name = serializers.CharField(max_length=80)
    index_source = serializers.CharField(max_length=200)
    period = serializers.DateField()
    rate_percent = DecimalTextField(max_length=20)
    source_note = serializers.CharField(max_length=300)
    is_demonstrative = serializers.BooleanField(default=False)


class AdjustmentSerializer(serializers.ModelSerializer[AdjustmentRecord]):
    class Meta:
        model = AdjustmentRecord
        fields = (
            "id",
            "index_value_id",
            "rate_percent",
            "applies_from",
            "affected",
            "applied_by_id",
            "created_at",
        )
        read_only_fields = fields


class AdjustmentCreateSerializer(serializers.Serializer[None]):
    index_value_id = serializers.UUIDField()
    applies_from = serializers.DateField()


class FinanceSummaryView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": Permission.FINANCE_READ}

    @extend_schema(responses=SummarySerializer)
    def get(self, _request: Request, contract_id: uuid.UUID) -> Response:
        result = services.summary(self.principal, contract_id)
        return Response(SummarySerializer(result).data)


class InstallmentsView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"post": Permission.FINANCE_WRITE}

    @extend_schema(request=ScheduleSerializer, responses={201: InstallmentSerializer(many=True)})
    def post(self, request: Request, contract_id: uuid.UUID) -> Response:
        serializer = ScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        created = services.schedule_installments(
            self.principal,
            contract_id,
            total=data["total"],
            parts=data["parts"],
            first_due=data["first_due"],
            frequency=Frequency(data["frequency"]),
            description=data["description"],
        )
        return Response(
            InstallmentSerializer(created, many=True).data, status=status.HTTP_201_CREATED
        )


class MilestonesView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"post": Permission.FINANCE_WRITE}

    @extend_schema(request=MilestoneSerializer, responses={201: InstallmentSerializer})
    def post(self, request: Request, contract_id: uuid.UUID) -> Response:
        serializer = MilestoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        milestone = services.add_milestone(self.principal, contract_id, **serializer.validated_data)
        return Response(InstallmentSerializer(milestone).data, status=status.HTTP_201_CREATED)


class ConfirmView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"post": Permission.FINANCE_WRITE}

    @extend_schema(request=ConfirmSerializer, responses={201: ConfirmationSerializer})
    def post(self, request: Request, installment_id: uuid.UUID) -> Response:
        serializer = ConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        confirmation = services.confirm(self.principal, installment_id, **serializer.validated_data)
        return Response(ConfirmationSerializer(confirmation).data, status=status.HTTP_201_CREATED)


class AdjustmentsView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "get": Permission.FINANCE_READ,
        "post": Permission.FINANCE_WRITE,
    }

    @extend_schema(responses=AdjustmentSerializer(many=True))
    def get(self, _request: Request, contract_id: uuid.UUID) -> Response:
        services.summary(self.principal, contract_id)
        records = AdjustmentRecord.objects.filter(
            organization_id=self.principal.organization_id, contract_id=contract_id
        )
        return Response(AdjustmentSerializer(records.order_by("created_at", "id"), many=True).data)

    @extend_schema(request=AdjustmentCreateSerializer, responses={201: AdjustmentSerializer})
    def post(self, request: Request, contract_id: uuid.UUID) -> Response:
        serializer = AdjustmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = services.apply_adjustment(self.principal, contract_id, **serializer.validated_data)
        return Response(AdjustmentSerializer(record).data, status=status.HTTP_201_CREATED)


class IndexValuesView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "get": Permission.FINANCE_READ,
        "post": Permission.FINANCE_WRITE,
    }

    @extend_schema(responses=IndexValueSerializer(many=True))
    def get(self, _request: Request) -> Response:
        values = IndexValue.objects.filter(
            organization_id=self.principal.organization_id
        ).select_related("series")
        return Response(
            IndexValueSerializer(values.order_by("series__name", "period"), many=True).data
        )

    @extend_schema(request=IndexValueCreateSerializer, responses={201: IndexValueSerializer})
    def post(self, request: Request) -> Response:
        serializer = IndexValueCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        value = services.register_index_value(
            self.principal,
            series_name=data["series_name"],
            source=data["index_source"],
            period=data["period"],
            rate=data["rate_percent"],
            source_note=data["source_note"],
            demonstrative=data["is_demonstrative"],
        )
        return Response(IndexValueSerializer(value).data, status=status.HTTP_201_CREATED)
