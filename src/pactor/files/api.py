"""API de upload, consulta, download autorizado e anexos de contrato."""

from __future__ import annotations

import uuid
from typing import ClassVar

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantAPIView
from pactor.access.policies import Permission
from pactor.contracts.selectors import get_visible_contract
from pactor.files import services
from pactor.files.models import ContractAttachment, FilePurpose, StoredFile


class StoredFileSerializer(serializers.ModelSerializer[StoredFile]):
    class Meta:
        model = StoredFile
        fields = (
            "id",
            "original_name",
            "kind",
            "purpose",
            "size",
            "sha256",
            "status",
            "rejection_reason",
            "rejection_detail",
            "scan_engine",
            "scan_attempts",
            "page_count",
            "created_at",
            "released_at",
        )
        read_only_fields = fields


class UploadSerializer(serializers.Serializer[StoredFile]):
    file = serializers.FileField()
    purpose = serializers.ChoiceField(
        choices=FilePurpose.choices, default=FilePurpose.CONTRACT_ATTACHMENT.value
    )


class AttachmentSerializer(serializers.ModelSerializer[ContractAttachment]):
    file = StoredFileSerializer(read_only=True)

    class Meta:
        model = ContractAttachment
        fields = ("id", "file", "include_in_submission", "added_by_id", "created_at")
        read_only_fields = fields


class AttachSerializer(serializers.Serializer[ContractAttachment]):
    file_id = serializers.UUIDField()
    include_in_submission = serializers.BooleanField(default=True)


def _file_response(download: services.Download) -> HttpResponse:
    response = HttpResponse(download.content, content_type=download.content_type)
    response["Content-Disposition"] = (
        f'attachment; filename="{uuid.uuid5(uuid.NAMESPACE_OID, download.sha256)}"'
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Pactor-SHA256"] = download.sha256
    response["Cache-Control"] = "no-store"
    return response


class FileUploadView(TenantAPIView):
    parser_classes = (MultiPartParser,)
    action_permissions: ClassVar[dict[str, Permission | None]] = {"post": None}

    @extend_schema(
        request={"multipart/form-data": UploadSerializer}, responses={202: StoredFileSerializer}
    )
    def post(self, request: Request) -> Response:
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        stored = services.receive(
            self.principal,
            name=upload.name or "arquivo",
            content=upload.read(),
            purpose=FilePurpose(serializer.validated_data["purpose"]),
        )
        return Response(StoredFileSerializer(stored).data, status=status.HTTP_202_ACCEPTED)


class FileDetailView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(responses=StoredFileSerializer)
    def get(self, _request: Request, file_id: uuid.UUID) -> Response:
        return Response(StoredFileSerializer(services.get_file(self.principal, file_id)).data)


class FileContentView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(
        responses={
            (200, "application/octet-stream"): OpenApiResponse(description="Conteúdo liberado")
        }
    )
    def get(self, _request: Request, file_id: uuid.UUID) -> HttpResponse:
        return _file_response(services.download(self.principal, file_id))


class FilePreviewView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(
        responses={(200, "application/pdf"): OpenApiResponse(description="Pré-visualização em PDF")}
    )
    def get(self, _request: Request, file_id: uuid.UUID) -> HttpResponse:
        return _file_response(services.download(self.principal, file_id, preview=True))


class ContractAttachmentsView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "get": None,
        "post": Permission.CONTRACTS_EDIT,
    }

    @extend_schema(responses=AttachmentSerializer(many=True))
    def get(self, _request: Request, contract_id: uuid.UUID) -> Response:
        contract = get_visible_contract(self.principal, contract_id)
        attachments = (
            ContractAttachment.objects.filter(
                organization_id=contract.organization_id, contract=contract, removed_at__isnull=True
            )
            .select_related("file")
            .order_by("created_at", "id")
        )
        return Response(AttachmentSerializer(attachments, many=True).data)

    @extend_schema(request=AttachSerializer, responses={201: AttachmentSerializer})
    def post(self, request: Request, contract_id: uuid.UUID) -> Response:
        serializer = AttachSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attachment = services.attach(
            self.principal,
            contract_id,
            serializer.validated_data["file_id"],
            include_in_submission=serializer.validated_data["include_in_submission"],
        )
        return Response(AttachmentSerializer(attachment).data, status=status.HTTP_201_CREATED)


class ContractAttachmentDetailView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "delete": Permission.CONTRACTS_EDIT
    }

    @extend_schema(responses={204: None})
    def delete(
        self, _request: Request, contract_id: uuid.UUID, attachment_id: uuid.UUID
    ) -> Response:
        services.detach(self.principal, contract_id, attachment_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
