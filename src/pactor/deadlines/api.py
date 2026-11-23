"""API de calendários, prazos, renovação, alertas e calendário consolidado."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import ClassVar

from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantAPIView
from pactor.access.policies import Permission
from pactor.contracts import lifecycle_services as lifecycle
from pactor.contracts.selectors import get_visible_contract, visible_contracts
from pactor.deadlines import services
from pactor.deadlines.calendar import DayCount, Roll
from pactor.deadlines.models import (
    Alert,
    ContractDeadline,
    DeadlineAnchor,
    DeadlineKind,
    HolidayCalendar,
    RenewalMode,
    RenewalPolicy,
    RenewalRecord,
)
from pactor.identity.models import Organization
from pactor.obligations import services as obligations

MAX_CALENDAR_DAYS = 400


class HolidaySerializer(serializers.Serializer[None]):
    day = serializers.DateField()
    name = serializers.CharField(max_length=120)


class HolidayCalendarSerializer(serializers.ModelSerializer[HolidayCalendar]):
    holidays = serializers.SerializerMethodField()

    class Meta:
        model = HolidayCalendar
        fields = ("id", "name", "is_default", "holidays")
        read_only_fields = ("id", "holidays")

    @extend_schema_field(HolidaySerializer(many=True))
    def get_holidays(self, obj: HolidayCalendar) -> list[dict[str, str]]:
        return [{"day": h.day.isoformat(), "name": h.name} for h in obj.holidays.order_by("day")]


class HolidayCalendarCreateSerializer(serializers.Serializer[HolidayCalendar]):
    name = serializers.CharField(max_length=120)
    is_default = serializers.BooleanField(default=False)
    holidays = HolidaySerializer(many=True)


class DeadlineSerializer(serializers.ModelSerializer[ContractDeadline]):
    class Meta:
        model = ContractDeadline
        fields = (
            "id",
            "contract_id",
            "kind",
            "title",
            "anchor",
            "fixed_date",
            "offset_days",
            "day_count",
            "roll",
            "holiday_calendar_id",
            "notify_days_before",
            "due_date",
            "status",
            "done_at",
        )
        read_only_fields = ("id", "contract_id", "due_date", "status", "done_at")


class DeadlineCreateSerializer(serializers.Serializer[ContractDeadline]):
    kind = serializers.ChoiceField(choices=DeadlineKind.choices)
    title = serializers.CharField(max_length=200)
    anchor = serializers.ChoiceField(choices=DeadlineAnchor.choices)
    offset_days = serializers.IntegerField(default=0)
    day_count = serializers.ChoiceField(
        choices=[(d.value, d.value) for d in DayCount], default=DayCount.CALENDAR.value
    )
    roll = serializers.ChoiceField(
        choices=[(r.value, r.value) for r in Roll], default=Roll.NONE.value
    )
    fixed_date = serializers.DateField(required=False, allow_null=True)
    holiday_calendar_id = serializers.UUIDField(required=False, allow_null=True)
    notify_days_before = serializers.IntegerField(min_value=0, max_value=365, default=0)


class RenewalPolicySerializer(serializers.ModelSerializer[RenewalPolicy]):
    class Meta:
        model = RenewalPolicy
        fields = (
            "mode",
            "term_months",
            "max_renewals",
            "authorized_by_id",
            "authorized_at",
            "non_renewal_registered_at",
            "non_renewal_reason",
        )
        read_only_fields = (
            "authorized_by_id",
            "authorized_at",
            "non_renewal_registered_at",
            "non_renewal_reason",
        )


class RenewalRecordSerializer(serializers.ModelSerializer[RenewalRecord]):
    class Meta:
        model = RenewalRecord
        fields = ("sequence", "previous_end", "new_end", "rule", "created_at")
        read_only_fields = fields


class NonRenewalReasonSerializer(serializers.Serializer[None]):
    reason = serializers.CharField(max_length=500)


class AlertSerializer(serializers.ModelSerializer[Alert]):
    class Meta:
        model = Alert
        fields = (
            "id",
            "target_type",
            "target_id",
            "contract_id",
            "kind",
            "fire_on",
            "fire_at",
            "subject",
            "status",
            "sent_at",
        )
        read_only_fields = fields


class RangeSerializer(serializers.Serializer[None]):
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        start, end = attrs.get("start"), attrs.get("end")
        if (
            isinstance(start, date)
            and isinstance(end, date)
            and (end - start).days > MAX_CALENDAR_DAYS
        ):
            raise serializers.ValidationError(f"Intervalo máximo de {MAX_CALENDAR_DAYS} dias.")
        return attrs


class HolidayCalendarsView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "get": None,
        "post": Permission.OBLIGATIONS_MANAGE,
    }

    @extend_schema(responses=HolidayCalendarSerializer(many=True))
    def get(self, _request: Request) -> Response:
        calendars = HolidayCalendar.objects.filter(
            organization_id=self.principal.organization_id
        ).order_by("name", "id")
        return Response(HolidayCalendarSerializer(calendars, many=True).data)

    @extend_schema(
        request=HolidayCalendarCreateSerializer, responses={201: HolidayCalendarSerializer}
    )
    def post(self, request: Request) -> Response:
        serializer = HolidayCalendarCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        created = services.create_holiday_calendar(
            self.principal,
            name=data["name"],
            is_default=data["is_default"],
            holidays=[(h["day"], h["name"]) for h in data["holidays"]],
        )
        return Response(HolidayCalendarSerializer(created).data, status=status.HTTP_201_CREATED)


class ContractDeadlinesView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "get": None,
        "post": Permission.OBLIGATIONS_MANAGE,
    }

    @extend_schema(responses=DeadlineSerializer(many=True))
    def get(self, _request: Request, contract_id: uuid.UUID) -> Response:
        contract = get_visible_contract(self.principal, contract_id)
        deadlines = ContractDeadline.objects.filter(
            organization_id=contract.organization_id, contract=contract
        )
        return Response(DeadlineSerializer(deadlines.order_by("due_date", "id"), many=True).data)

    @extend_schema(request=DeadlineCreateSerializer, responses={201: DeadlineSerializer})
    def post(self, request: Request, contract_id: uuid.UUID) -> Response:
        serializer = DeadlineCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        deadline = services.create_deadline(
            self.principal,
            contract_id,
            services.DeadlineInput(
                kind=DeadlineKind(data["kind"]),
                title=data["title"],
                anchor=DeadlineAnchor(data["anchor"]),
                offset_days=data["offset_days"],
                day_count=DayCount(data["day_count"]),
                roll=Roll(data["roll"]),
                fixed_date=data.get("fixed_date"),
                holiday_calendar_id=data.get("holiday_calendar_id"),
                notify_days_before=data["notify_days_before"],
            ),
        )
        return Response(DeadlineSerializer(deadline).data, status=status.HTTP_201_CREATED)


class DeadlineCompleteView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "post": Permission.OBLIGATIONS_MANAGE
    }

    @extend_schema(request=None, responses=DeadlineSerializer)
    def post(self, _request: Request, deadline_id: uuid.UUID) -> Response:
        return Response(
            DeadlineSerializer(services.complete_deadline(self.principal, deadline_id)).data
        )


class RenewalView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "get": None,
        "put": Permission.CONTRACTS_LIFECYCLE,
    }

    @extend_schema(responses=RenewalPolicySerializer)
    def get(self, _request: Request, contract_id: uuid.UUID) -> Response:
        contract = get_visible_contract(self.principal, contract_id)
        policy = RenewalPolicy.objects.filter(
            organization_id=contract.organization_id, contract=contract
        ).first()
        records = RenewalRecord.objects.filter(
            organization_id=contract.organization_id, contract=contract
        ).order_by("sequence")
        return Response(
            {
                "policy": RenewalPolicySerializer(policy).data if policy else None,
                "renewals": RenewalRecordSerializer(records, many=True).data,
            }
        )

    @extend_schema(request=RenewalPolicySerializer, responses=RenewalPolicySerializer)
    def put(self, request: Request, contract_id: uuid.UUID) -> Response:
        serializer = RenewalPolicySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        policy = services.configure_renewal(
            self.principal,
            contract_id,
            mode=RenewalMode(data["mode"]),
            term_months=data.get("term_months", 12),
            max_renewals=data.get("max_renewals"),
        )
        return Response(RenewalPolicySerializer(policy).data)


class NonRenewalView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "post": Permission.CONTRACTS_LIFECYCLE
    }

    @extend_schema(request=NonRenewalReasonSerializer, responses=RenewalPolicySerializer)
    def post(self, request: Request, contract_id: uuid.UUID) -> Response:
        serializer = NonRenewalReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        policy = services.register_non_renewal(
            self.principal, contract_id, reason=serializer.validated_data["reason"]
        )
        return Response(RenewalPolicySerializer(policy).data)


class AlertsView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(responses=AlertSerializer(many=True))
    def get(self, _request: Request) -> Response:
        alerts = Alert.objects.filter(
            organization_id=self.principal.organization_id,
            recipient_ids__contains=[self.principal.actor_id],
            contract__in=visible_contracts(self.principal).values("id"),
        ).order_by("-fire_at", "-id")[:200]
        return Response(AlertSerializer(alerts, many=True).data)


class CalendarEventSerializer(serializers.Serializer[None]):
    type = serializers.ChoiceField(choices=[("deadline", "Prazo"), ("obligation", "Obrigação")])
    id = serializers.UUIDField()
    date = serializers.DateField()
    title = serializers.CharField()
    kind = serializers.CharField()
    status = serializers.CharField()
    contract_id = serializers.UUIDField()
    contract_reference = serializers.CharField()
    overdue = serializers.BooleanField()


class CalendarSerializer(serializers.Serializer[None]):
    start = serializers.DateField()
    end = serializers.DateField()
    today = serializers.DateField()
    events = CalendarEventSerializer(many=True)


class CalendarView(TenantAPIView):
    """Prazos e ocorrências de obrigações dos contratos visíveis, em um intervalo."""

    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(parameters=[RangeSerializer], responses=CalendarSerializer)
    def get(self, request: Request) -> Response:
        query = RangeSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        today = lifecycle.local_today(Organization.objects.get(id=self.principal.organization_id))
        start = query.validated_data.get("start") or today - timedelta(days=30)
        end = query.validated_data.get("end") or today + timedelta(days=90)
        deadlines = ContractDeadline.objects.filter(
            organization_id=self.principal.organization_id,
            contract__in=visible_contracts(self.principal).values("id"),
            due_date__range=(start, end),
        ).select_related("contract")
        events = [
            {
                "type": "deadline",
                "id": str(d.id),
                "date": d.due_date.isoformat(),
                "title": d.title,
                "kind": d.kind,
                "status": d.status,
                "contract_id": str(d.contract_id),
                "contract_reference": d.contract.reference,
                "overdue": d.status == "pending" and d.due_date < today,
            }
            for d in deadlines
            if d.due_date is not None
        ]
        events += [
            {
                "type": "obligation",
                "id": str(o.id),
                "date": o.due_date.isoformat(),
                "title": o.obligation.title,
                "kind": o.obligation.category,
                "status": o.status,
                "contract_id": str(o.obligation.contract_id),
                "contract_reference": o.obligation.contract.reference,
                "overdue": o.status == "pending" and o.due_date < today,
            }
            for o in obligations.occurrences_for(self.principal, start=start, end=end)
        ]
        events.sort(key=lambda e: (str(e["date"]), str(e["type"]), str(e["id"])))
        return Response(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "today": today.isoformat(),
                "events": events,
            }
        )
