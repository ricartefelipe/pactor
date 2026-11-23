"""Consulta da trilha de auditoria da organização ativa."""

from __future__ import annotations

from typing import ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, serializers

from pactor.access.api import TenantViewSet
from pactor.access.policies import Permission
from pactor.audit.models import AuditEvent
from pactor.core.api.filters import parse_filters
from pactor.core.api.pagination import StablePagination


class AuditEventSerializer(serializers.ModelSerializer[AuditEvent]):
    class Meta:
        model = AuditEvent
        fields = (
            "id",
            "actor_id",
            "actor_label",
            "action",
            "resource_type",
            "resource_id",
            "changes",
            "request_id",
            "occurred_at",
        )
        read_only_fields = fields


class AuditFilterSerializer(serializers.Serializer[None]):
    action = serializers.CharField(required=False, max_length=80)
    resource_type = serializers.CharField(required=False, max_length=60)
    resource_id = serializers.CharField(required=False, max_length=64)
    actor_id = serializers.UUIDField(required=False)
    occurred_after = serializers.DateTimeField(required=False)
    occurred_before = serializers.DateTimeField(required=False)


class AuditPagination(StablePagination):
    ordering = ("-occurred_at", "-id")


@extend_schema_view(list=extend_schema(parameters=[AuditFilterSerializer]))
class AuditEventViewSet(mixins.ListModelMixin, TenantViewSet):
    serializer_class = AuditEventSerializer
    pagination_class = AuditPagination
    action_permissions: ClassVar[dict[str, Permission | None]] = {"list": Permission.AUDIT_READ}

    def get_queryset(self) -> QuerySet[AuditEvent]:
        filters = parse_filters(self.request, AuditFilterSerializer)
        queryset = AuditEvent.objects.filter(organization_id=self.principal.organization_id)
        if "occurred_after" in filters:
            queryset = queryset.filter(occurred_at__gte=filters.pop("occurred_after"))
        if "occurred_before" in filters:
            queryset = queryset.filter(occurred_at__lt=filters.pop("occurred_before"))
        return queryset.filter(**filters)
