"""API de modelos de contrato e geração de revisões."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import IDEMPOTENCY_KEY, TenantViewSet
from pactor.access.policies import Permission
from pactor.contracts.api import RevisionDetailSerializer
from pactor.contracts.domain import ContractType
from pactor.contracts.models import CONTRACT_TYPE_CHOICES
from pactor.drafting import services
from pactor.drafting.models import ContractTemplate, TemplateVersion


class TemplateVersionSerializer(serializers.ModelSerializer[TemplateVersion]):
    class Meta:
        model = TemplateVersion
        fields = ("id", "number", "definition", "change_note", "published_by_id", "created_at")
        read_only_fields = fields


class TemplateSerializer(serializers.ModelSerializer[ContractTemplate]):
    versions = TemplateVersionSerializer(many=True, read_only=True)

    class Meta:
        model = ContractTemplate
        fields = (
            "id",
            "name",
            "contract_type",
            "description",
            "draft_definition",
            "draft_version",
            "is_active",
            "updated_at",
            "versions",
        )
        read_only_fields = ("id", "draft_version", "is_active", "updated_at", "versions")


class TemplateCreateSerializer(serializers.Serializer[ContractTemplate]):
    name = serializers.CharField(max_length=160)
    contract_type = serializers.ChoiceField(choices=CONTRACT_TYPE_CHOICES)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    definition = serializers.JSONField()


class TemplateDraftSerializer(serializers.Serializer[ContractTemplate]):
    definition = serializers.JSONField()
    expected_version = serializers.IntegerField(min_value=1)


class TemplatePublishSerializer(serializers.Serializer[TemplateVersion]):
    expected_version = serializers.IntegerField(min_value=1)
    change_note = serializers.CharField(
        max_length=300, required=False, allow_blank=True, default=""
    )


class GenerateSerializer(serializers.Serializer[None]):
    contract_id = serializers.UUIDField()
    template_version_id = serializers.UUIDField()
    values = serializers.DictField(help_text="Valores dos campos declarados no modelo")
    expected_draft_version = serializers.IntegerField(min_value=1)


class TemplateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, TenantViewSet):
    serializer_class = TemplateSerializer
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": Permission.CONTRACTS_READ,
        "retrieve": Permission.CONTRACTS_READ,
        "create": Permission.TEMPLATES_MANAGE,
        "draft": Permission.TEMPLATES_MANAGE,
        "publish": Permission.TEMPLATES_MANAGE,
        "generate": Permission.CONTRACTS_EDIT,
    }

    def get_queryset(self) -> QuerySet[ContractTemplate]:
        return (
            services.templates_for(self.principal)
            .prefetch_related("versions")
            .order_by("name", "id")
        )

    @extend_schema(request=TemplateCreateSerializer, responses={201: TemplateSerializer})
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = TemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        template = services.create_template(
            self.principal,
            name=data["name"],
            contract_type=ContractType(data["contract_type"]),
            description=data["description"],
            definition=data["definition"],
        )
        return Response(TemplateSerializer(template).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=TemplateDraftSerializer, responses=TemplateSerializer)
    @action(detail=True, methods=["put"])
    def draft(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = TemplateDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = services.save_draft(
            self.principal,
            pk,
            definition=serializer.validated_data["definition"],
            expected_version=serializer.validated_data["expected_version"],
        )
        return Response(TemplateSerializer(template).data)

    @extend_schema(request=TemplatePublishSerializer, responses={201: TemplateVersionSerializer})
    @action(detail=True, methods=["post"])
    def publish(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = TemplatePublishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = services.publish(self.principal, pk, **serializer.validated_data)
        return Response(TemplateVersionSerializer(version).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=GenerateSerializer,
        responses={201: RevisionDetailSerializer},
        parameters=[IDEMPOTENCY_KEY],
    )
    @action(detail=False, methods=["post"])
    def generate(self, request: Request) -> Response:
        def operation() -> Response:
            serializer = GenerateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            revision = services.generate_revision(
                self.principal,
                data["contract_id"],
                template_version_id=data["template_version_id"],
                data=data["values"],
                expected_draft_version=data["expected_draft_version"],
            )
            return Response(RevisionDetailSerializer(revision).data, status=status.HTTP_201_CREATED)

        return self.idempotent(request, "templates.generate", operation)
