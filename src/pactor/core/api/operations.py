"""API de operação: falhas de consumidores e eventos esgotados da organização."""

from __future__ import annotations

import uuid
from typing import ClassVar

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantAPIView
from pactor.access.policies import Permission
from pactor.core import operations
from pactor.core.models import ConsumerFailure


class FailureSerializer(serializers.ModelSerializer[ConsumerFailure]):
    topic = serializers.CharField(source="event.topic", read_only=True)

    class Meta:
        model = ConsumerFailure
        fields = (
            "id",
            "consumer",
            "event_id",
            "topic",
            "attempts",
            "status",
            "last_error",
            "updated_at",
        )
        read_only_fields = fields


class FailuresView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "get": Permission.OPERATIONS_REPROCESS
    }

    @extend_schema(responses=FailureSerializer(many=True))
    def get(self, _request: Request) -> Response:
        return Response(
            FailureSerializer(operations.failures_for(self.principal)[:200], many=True).data
        )


class RequeueFailureView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "post": Permission.OPERATIONS_REPROCESS
    }

    @extend_schema(request=None, responses=FailureSerializer)
    def post(self, _request: Request, failure_id: uuid.UUID) -> Response:
        return Response(
            FailureSerializer(operations.requeue_failure(self.principal, failure_id)).data
        )


class RequeueDeadEventsView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "post": Permission.OPERATIONS_REPROCESS
    }

    @extend_schema(request=None, responses={200: dict})
    def post(self, _request: Request) -> Response:
        return Response({"requeued": operations.requeue_dead_events(self.principal)})
