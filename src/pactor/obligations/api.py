"""API de obrigações e ocorrências."""

from __future__ import annotations

import uuid
from typing import ClassVar

from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantAPIView
from pactor.access.policies import Permission
from pactor.contracts.selectors import get_visible_contract
from pactor.deadlines.calendar import Frequency
from pactor.obligations import services
from pactor.obligations.models import (
    Obligation,
    ObligationCategory,
    ObligationOccurrence,
    ResponsibleParty,
)

FREQUENCY_CHOICES = [(f.value, f.value) for f in Frequency]


class OccurrenceSerializer(serializers.ModelSerializer[ObligationOccurrence]):
    class Meta:
        model = ObligationOccurrence
        fields = (
            "id",
            "obligation_id",
            "schedule_version",
            "sequence",
            "due_date",
            "status",
            "evidence_id",
            "note",
            "submitted_by_id",
            "submitted_at",
            "completed_by_id",
            "completed_at",
        )
        read_only_fields = fields


class ObligationSerializer(serializers.ModelSerializer[Obligation]):
    occurrences = serializers.SerializerMethodField()

    class Meta:
        model = Obligation
        fields = (
            "id",
            "contract_id",
            "title",
            "description",
            "category",
            "responsible_id",
            "responsible_party",
            "requires_evidence",
            "requires_approval",
            "frequency",
            "interval",
            "first_due",
            "until",
            "count",
            "reminder_days_before",
            "escalation_days_after",
            "depends_on_id",
            "schedule_version",
            "is_active",
            "version",
            "occurrences",
        )
        read_only_fields = fields

    @extend_schema_field(OccurrenceSerializer(many=True))
    def get_occurrences(self, obj: Obligation) -> list[dict[str, object]]:
        items = obj.occurrences.order_by("due_date", "schedule_version", "sequence")[:60]
        return [dict(item) for item in OccurrenceSerializer(items, many=True).data]


class ObligationCreateSerializer(serializers.Serializer[Obligation]):
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    category = serializers.ChoiceField(choices=ObligationCategory.choices)
    responsible_id = serializers.UUIDField()
    responsible_party = serializers.ChoiceField(choices=ResponsibleParty.choices)
    frequency = serializers.ChoiceField(choices=FREQUENCY_CHOICES)
    interval = serializers.IntegerField(min_value=1, max_value=60, default=1)
    first_due = serializers.DateField()
    until = serializers.DateField(required=False, allow_null=True)
    count = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=1000)
    requires_evidence = serializers.BooleanField(default=False)
    requires_approval = serializers.BooleanField(default=False)
    reminder_days_before = serializers.IntegerField(min_value=0, max_value=365, default=7)
    escalation_days_after = serializers.IntegerField(min_value=0, max_value=365, default=3)
    depends_on_id = serializers.UUIDField(required=False, allow_null=True)


class RescheduleSerializer(serializers.Serializer[Obligation]):
    expected_version = serializers.IntegerField(min_value=1)
    frequency = serializers.ChoiceField(choices=FREQUENCY_CHOICES)
    first_due = serializers.DateField()
    interval = serializers.IntegerField(min_value=1, max_value=60, default=1)
    until = serializers.DateField(required=False, allow_null=True)
    count = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=1000)


class CompleteSerializer(serializers.Serializer[ObligationOccurrence]):
    evidence_id = serializers.UUIDField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=1000)


class CompletionReviewSerializer(serializers.Serializer[ObligationOccurrence]):
    approve = serializers.BooleanField()
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=1000)


class ContractObligationsView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "get": None,
        "post": Permission.OBLIGATIONS_MANAGE,
    }

    @extend_schema(responses=ObligationSerializer(many=True))
    def get(self, _request: Request, contract_id: uuid.UUID) -> Response:
        contract = get_visible_contract(self.principal, contract_id)
        items = Obligation.objects.filter(
            organization_id=contract.organization_id, contract=contract
        ).order_by("first_due", "id")
        return Response(ObligationSerializer(items.prefetch_related("occurrences"), many=True).data)

    @extend_schema(request=ObligationCreateSerializer, responses={201: ObligationSerializer})
    def post(self, request: Request, contract_id: uuid.UUID) -> Response:
        serializer = ObligationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        obligation = services.create_obligation(
            self.principal,
            contract_id,
            services.ObligationInput(
                **{
                    **data,
                    "category": ObligationCategory(data["category"]),
                    "responsible_party": ResponsibleParty(data["responsible_party"]),
                    "frequency": Frequency(data["frequency"]),
                }
            ),
        )
        return Response(ObligationSerializer(obligation).data, status=status.HTTP_201_CREATED)


class RescheduleView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "post": Permission.OBLIGATIONS_MANAGE
    }

    @extend_schema(request=RescheduleSerializer, responses=ObligationSerializer)
    def post(self, request: Request, obligation_id: uuid.UUID) -> Response:
        serializer = RescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        obligation = services.change_schedule(
            self.principal, obligation_id, **{**data, "frequency": Frequency(data["frequency"])}
        )
        return Response(ObligationSerializer(obligation).data)


class OccurrenceCompleteView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "post": Permission.OBLIGATIONS_COMPLETE
    }

    @extend_schema(request=CompleteSerializer, responses=OccurrenceSerializer)
    def post(self, request: Request, occurrence_id: uuid.UUID) -> Response:
        serializer = CompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        occurrence = services.submit_completion(
            self.principal,
            occurrence_id,
            evidence_id=serializer.validated_data.get("evidence_id"),
            note=serializer.validated_data["note"],
        )
        return Response(OccurrenceSerializer(occurrence).data)


class OccurrenceReviewView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "post": Permission.OBLIGATIONS_MANAGE
    }

    @extend_schema(request=CompletionReviewSerializer, responses=OccurrenceSerializer)
    def post(self, request: Request, occurrence_id: uuid.UUID) -> Response:
        serializer = CompletionReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        occurrence = services.review_completion(
            self.principal, occurrence_id, **serializer.validated_data
        )
        return Response(OccurrenceSerializer(occurrence).data)
