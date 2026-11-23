"""API de envelopes de assinatura, downloads autorizados e ações do simulador."""

from __future__ import annotations

import uuid
from typing import ClassVar

from django.conf import settings
from django.http import FileResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantViewSet
from pactor.access.policies import Permission
from pactor.contracts.selectors import get_visible_contract
from pactor.core.errors import AccessDeniedError, NotFoundError
from pactor.signatures import services, storage
from pactor.signatures.models import Envelope, EnvelopeDocument, Recipient
from pactor.signatures.simulator import SimulatorProvider


class RecipientSerializer(serializers.ModelSerializer[Recipient]):
    class Meta:
        model = Recipient
        fields = ("recipient_key", "name", "email", "routing_order", "status", "changed_at")
        read_only_fields = fields


class EnvelopeDocumentSerializer(serializers.ModelSerializer[EnvelopeDocument]):
    class Meta:
        model = EnvelopeDocument
        fields = ("id", "kind", "name", "content_type", "sha256", "size", "created_at")
        read_only_fields = fields


class EnvelopeSerializer(serializers.ModelSerializer[Envelope]):
    recipients = RecipientSerializer(many=True, read_only=True)
    documents = EnvelopeDocumentSerializer(many=True, read_only=True)
    revision_number = serializers.IntegerField(source="revision.number", read_only=True)

    class Meta:
        model = Envelope
        fields = (
            "id",
            "contract_id",
            "revision_id",
            "revision_number",
            "fingerprint",
            "provider",
            "provider_account_id",
            "provider_envelope_id",
            "state",
            "subject",
            "dispatch_attempts",
            "last_error",
            "completed_at",
            "void_reason",
            "created_at",
            "updated_at",
            "recipients",
            "documents",
        )
        read_only_fields = fields


class SignerSerializer(serializers.Serializer[None]):
    name = serializers.CharField(max_length=200)
    email = serializers.EmailField()
    routing_order = serializers.IntegerField(min_value=1, max_value=20)


class SendSerializer(serializers.Serializer[Envelope]):
    contract_id = serializers.UUIDField()
    expected_fingerprint = serializers.CharField(min_length=64, max_length=64)
    signers = SignerSerializer(many=True)


class VoidSerializer(serializers.Serializer[Envelope]):
    reason = serializers.CharField(max_length=300)


class SimulatorActionSerializer(serializers.Serializer[None]):
    recipient_key = serializers.CharField(max_length=40)
    action = serializers.ChoiceField(
        choices=[("sign", "Assinar"), ("decline", "Recusar"), ("replay", "Reenviar último evento")]
    )


class EnvelopeFilterSerializer(serializers.Serializer[None]):
    contract_id = serializers.UUIDField()


class EnvelopeViewSet(TenantViewSet):
    serializer_class = EnvelopeSerializer
    pk_log_key = "envelope_id"
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": None,
        "retrieve": None,
        "send": Permission.SIGNATURES_SEND,
        "void": Permission.SIGNATURES_SEND,
        "document": None,
        "simulate": Permission.SIGNATURES_SEND,
    }

    def _visible_envelope(self, envelope_id: uuid.UUID) -> Envelope:
        envelope = (
            Envelope.objects.filter(organization_id=self.principal.organization_id, id=envelope_id)
            .select_related("revision")
            .prefetch_related("recipients", "documents")
            .first()
        )
        if envelope is None:
            raise NotFoundError("Envelope não encontrado.")
        # Visibilidade do envelope segue a do contrato.
        get_visible_contract(self.principal, envelope.contract_id)
        return envelope

    @extend_schema(parameters=[EnvelopeFilterSerializer], responses=EnvelopeSerializer(many=True))
    def list(self, request: Request) -> Response:
        params = EnvelopeFilterSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        contract = get_visible_contract(self.principal, params.validated_data["contract_id"])
        envelopes = (
            Envelope.objects.filter(organization_id=contract.organization_id, contract=contract)
            .select_related("revision")
            .prefetch_related("recipients", "documents")
            .order_by("-created_at", "-id")
        )
        return Response(EnvelopeSerializer(envelopes, many=True).data)

    @extend_schema(responses=EnvelopeSerializer)
    def retrieve(self, _request: Request, pk: uuid.UUID) -> Response:
        return Response(EnvelopeSerializer(self._visible_envelope(pk)).data)

    @extend_schema(request=SendSerializer, responses={201: EnvelopeSerializer})
    @action(detail=False, methods=["post"])
    def send(self, request: Request) -> Response:
        def operation() -> Response:
            serializer = SendSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            envelope = services.send_for_signature(
                self.principal,
                data["contract_id"],
                signers=[services.SignerInput(**s) for s in data["signers"]],
                expected_fingerprint=data["expected_fingerprint"],
            )
            return Response(
                EnvelopeSerializer(self._visible_envelope(envelope.id)).data,
                status=status.HTTP_201_CREATED,
            )

        return self.idempotent(request, "signatures.send", operation)

    @extend_schema(request=VoidSerializer, responses=EnvelopeSerializer)
    @action(detail=True, methods=["post"])
    def void(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = VoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_void(self.principal, pk, reason=serializer.validated_data["reason"])
        return Response(EnvelopeSerializer(self._visible_envelope(pk)).data)

    @extend_schema(
        responses={(200, "application/octet-stream"): OpenApiResponse(description="Documento")}
    )
    @action(detail=True, methods=["get"], url_path="documents/<uuid:document_id>")
    def document(self, _request: Request, pk: uuid.UUID, document_id: uuid.UUID) -> FileResponse:
        envelope = self._visible_envelope(pk)
        document = next((d for d in envelope.documents.all() if d.id == document_id), None)
        if document is None:
            raise NotFoundError("Documento não encontrado.")
        content = storage.read_verified(document)
        response = FileResponse(iter([content]), content_type=document.content_type)
        response["Content-Disposition"] = f'attachment; filename="{document.id}"'
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Pactor-SHA256"] = document.sha256
        return response

    @extend_schema(request=SimulatorActionSerializer, responses={200: dict})
    @action(detail=True, methods=["post"])
    def simulate(self, request: Request, pk: uuid.UUID) -> Response:
        """Ação no simulador (assinar, recusar, reenviar evento). Indisponível fora do simulador."""
        if settings.PACTOR_SIGNATURE_PROVIDER != "simulator":
            raise AccessDeniedError("Simulador de assinatura desabilitado neste ambiente.")
        serializer = SimulatorActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        envelope = self._visible_envelope(pk)
        if envelope.provider != "simulator" or not envelope.provider_envelope_id:
            raise NotFoundError("Envelope ainda não registrado no simulador.")
        simulator = SimulatorProvider(account_id=envelope.provider_account_id)
        match serializer.validated_data["action"]:
            case "sign":
                webhook = simulator.sign(
                    envelope.provider_envelope_id, serializer.validated_data["recipient_key"]
                )
            case "decline":
                webhook = simulator.decline(
                    envelope.provider_envelope_id, serializer.validated_data["recipient_key"]
                )
            case _:
                webhook = simulator.webhook_for(simulator.stored(envelope.provider_envelope_id))
        return Response({"headers": dict(webhook.headers), "body": webhook.body.decode()})
