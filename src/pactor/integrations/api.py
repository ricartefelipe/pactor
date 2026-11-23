"""API de gestão de clientes de integração (somente administração com MFA)."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantViewSet
from pactor.access.policies import Permission
from pactor.identity.models import ROLE_CHOICES
from pactor.integrations import services
from pactor.integrations.models import IntegrationClient


class IntegrationSerializer(serializers.ModelSerializer[IntegrationClient]):
    client_id = serializers.CharField(source="oauth_client_id", read_only=True)

    class Meta:
        model = IntegrationClient
        fields: tuple[str, ...] = (
            "id",
            "name",
            "client_id",
            "roles",
            "scopes",
            "created_by_id",
            "created_at",
            "revoked_at",
        )
        read_only_fields: tuple[str, ...] = fields


class IntegrationCreateSerializer(serializers.Serializer[IntegrationClient]):
    name = serializers.CharField(max_length=120)
    roles = serializers.ListField(child=serializers.ChoiceField(choices=ROLE_CHOICES), min_length=1)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=list(services.INTEGRATION_SCOPES.items())),
        min_length=1,
    )


class IssuedSerializer(IntegrationSerializer):
    client_secret = serializers.CharField(read_only=True)

    class Meta(IntegrationSerializer.Meta):
        fields = (*IntegrationSerializer.Meta.fields, "client_secret")
        read_only_fields = fields


class IntegrationViewSet(mixins.ListModelMixin, TenantViewSet):
    serializer_class = IntegrationSerializer
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": Permission.INTEGRATIONS_MANAGE,
        "create": Permission.INTEGRATIONS_MANAGE,
        "revoke": Permission.INTEGRATIONS_MANAGE,
    }

    def get_queryset(self) -> QuerySet[IntegrationClient]:
        return IntegrationClient.objects.filter(
            organization_id=self.principal.organization_id
        ).order_by("name", "id")

    @extend_schema(request=IntegrationCreateSerializer, responses={201: IssuedSerializer})
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = IntegrationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issued = services.create_client(self.principal, **serializer.validated_data)
        payload = dict(IntegrationSerializer(issued.client).data)
        # O segredo é exibido somente nesta resposta; o banco guarda apenas o hash.
        payload["client_secret"] = issued.client_secret
        return Response(payload, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=IntegrationSerializer)
    @action(detail=True, methods=["post"])
    def revoke(self, _request: Request, pk: uuid.UUID) -> Response:
        return Response(IntegrationSerializer(services.revoke_client(self.principal, pk)).data)
