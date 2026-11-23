"""Provedor de assinatura simulado, determinístico e restrito a ambientes de teste.

Identificado como simulador em todos os documentos que produz. Mantém o
estado do "lado do provedor" em ``SimulatedEnvelope`` e emite webhooks
assinados com HMAC, percorrendo o mesmo caminho de verificação e
processamento dos provedores reais. Injeção de falhas (indisponibilidade e
timeout após criação) existe apenas por parâmetro de construção.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from pactor.contracts.pdf import PdfDocument, render_pdf
from pactor.core.errors import ConflictError, NotFoundError
from pactor.signatures.models import SimulatedEnvelope
from pactor.signatures.ports import (
    EnvelopeHandle,
    EnvelopeRequest,
    EnvelopeState,
    EnvelopeStatus,
    InvalidWebhookError,
    ProviderEvent,
    ProviderOutcomeUnknownError,
    ProviderUnavailableError,
    RecipientState,
    RecipientStatus,
    ReturnedDocument,
)

SIGNATURE_HEADER = "X-Pactor-Simulator-Signature"
SIMULATOR_NOTICE = (
    "SIMULADOR DE ASSINATURA — documento de teste, sem efeito de assinatura eletrônica."
)


class SimulatorDisabledError(RuntimeError):
    """Uso do simulador fora dos ambientes permitidos."""


@dataclass(frozen=True, slots=True)
class SignedWebhook:
    headers: Mapping[str, str]
    body: bytes


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class SimulatorProvider:
    name = "simulator"

    def __init__(
        self,
        *,
        account_id: str = "simulador",
        webhook_secret: str | None = None,
        unavailable: bool = False,
        timeout_after_create: bool = False,
    ) -> None:
        if settings.PACTOR_ENVIRONMENT == "production":
            raise SimulatorDisabledError(
                "O simulador de assinatura não está disponível em produção."
            )
        self._account_id = account_id
        self._secret = webhook_secret or settings.PACTOR_SIMULATOR_WEBHOOK_SECRET
        self._unavailable = unavailable
        self._timeout_after_create = timeout_after_create

    @property
    def account_id(self) -> str:
        return self._account_id

    # -- porta ---------------------------------------------------------------
    def create_envelope(self, request: EnvelopeRequest) -> EnvelopeHandle:
        if self._unavailable:
            raise ProviderUnavailableError("simulador indisponível")
        provider_id = (
            "SIM-"
            + hashlib.sha256(f"{self._account_id}:{request.reference}".encode()).hexdigest()[:24]
        )
        with transaction.atomic():
            envelope, _ = SimulatedEnvelope.objects.get_or_create(
                account_id=self._account_id,
                reference=request.reference,
                defaults={
                    "provider_envelope_id": provider_id,
                    "subject": request.subject,
                    "document": request.document,
                    "document_sha256": request.document_sha256,
                    "status": EnvelopeStatus.SENT.value,
                    "recipients": self._initial_recipients(request),
                },
            )
        if self._timeout_after_create:
            # O envelope já existe no "provedor", mas a resposta não chega.
            raise ProviderOutcomeUnknownError("timeout simulado após criação")
        return EnvelopeHandle(envelope.provider_envelope_id, EnvelopeStatus(envelope.status))

    def find_by_reference(self, reference: str) -> EnvelopeHandle | None:
        if self._unavailable:
            raise ProviderUnavailableError("simulador indisponível")
        envelope = SimulatedEnvelope.objects.filter(
            account_id=self._account_id, reference=reference
        ).first()
        return (
            None
            if envelope is None
            else EnvelopeHandle(envelope.provider_envelope_id, EnvelopeStatus(envelope.status))
        )

    def get_state(self, provider_envelope_id: str) -> EnvelopeState:
        return self._state(self._get(provider_envelope_id))

    def void(self, provider_envelope_id: str, reason: str) -> None:
        with transaction.atomic():
            envelope = self._get(provider_envelope_id, for_update=True)
            if envelope.status == EnvelopeStatus.COMPLETED:
                raise ConflictError("Envelope concluído não pode ser cancelado.")
            envelope.status = EnvelopeStatus.VOIDED.value
            del reason  # o simulador não armazena o motivo; o Pactor o registra no envelope
            self._touch(envelope)

    def download_documents(self, provider_envelope_id: str) -> Sequence[ReturnedDocument]:
        envelope = self._get(provider_envelope_id)
        if envelope.status != EnvelopeStatus.COMPLETED:
            raise ConflictError("Documentos assinados só existem para envelopes concluídos.")
        signers = [
            f"{r['name']} <{r['email']}> assinou em {r['changed_at']}" for r in envelope.recipients
        ]
        signed = render_pdf(
            PdfDocument(
                title=f"{envelope.subject} — concluído",
                header_lines=[SIMULATOR_NOTICE, f"Envelope {envelope.provider_envelope_id}"],
                body="\n".join(signers),
                footer_lines=[f"SHA-256 do documento enviado: {envelope.document_sha256}"],
            )
        )
        evidence = json.dumps(
            {
                "provider": self.name,
                "notice": SIMULATOR_NOTICE,
                "envelope_id": envelope.provider_envelope_id,
                "document_sha256": envelope.document_sha256,
                "recipients": envelope.recipients,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
        return [
            ReturnedDocument("signed", f"{envelope.provider_envelope_id}-assinado.pdf", signed),
            ReturnedDocument(
                "evidence", f"{envelope.provider_envelope_id}-evidencias.json", evidence
            ),
        ]

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent:
        given = headers.get(SIGNATURE_HEADER, "")
        if not given or not hmac.compare_digest(given, _sign(self._secret, body)):
            raise InvalidWebhookError("assinatura HMAC inválida")
        try:
            payload = json.loads(body)
            recipients = [
                RecipientState(
                    recipient_key=str(r["recipient_key"]),
                    status=RecipientStatus(r["status"]),
                    changed_at=datetime.fromisoformat(r["changed_at"])
                    if r.get("changed_at")
                    else None,
                )
                for r in payload["recipients"]
            ]
            occurred_at = datetime.fromisoformat(payload["occurred_at"])
            state = EnvelopeState(
                provider_envelope_id=str(payload["envelope_id"]),
                status=EnvelopeStatus(payload["status"]),
                recipients=recipients,
                changed_at=occurred_at,
            )
            return ProviderEvent(
                dedup_key=f"{payload['envelope_id']}:{int(payload['sequence'])}",
                provider_envelope_id=state.provider_envelope_id,
                state=state,
                occurred_at=occurred_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidWebhookError("corpo do webhook malformado") from exc

    # -- ações do "lado do provedor" ----------------------------------------------
    def sign(self, provider_envelope_id: str, recipient_key: str) -> SignedWebhook:
        """Registra a assinatura do participante da vez e devolve o webhook resultante."""
        with transaction.atomic():
            envelope = self._get(provider_envelope_id, for_update=True)
            if envelope.status != EnvelopeStatus.SENT:
                raise ConflictError("Envelope não está aguardando assinaturas.")
            pending = [r for r in envelope.recipients if r["status"] != RecipientStatus.SIGNED]
            current_order = min(r["routing_order"] for r in pending)
            target = next((r for r in pending if r["recipient_key"] == recipient_key), None)
            if target is None:
                raise NotFoundError("Participante inexistente ou já assinou.")
            if target["routing_order"] != current_order:
                raise ConflictError("Fora da ordem de assinatura.")
            now = timezone.now().isoformat()
            target.update(status=RecipientStatus.SIGNED.value, changed_at=now)
            remaining = [r for r in pending if r is not target]
            if remaining:
                next_order = min(r["routing_order"] for r in remaining)
                for r in remaining:
                    if r["routing_order"] == next_order and r["status"] == RecipientStatus.CREATED:
                        r.update(status=RecipientStatus.SENT.value, changed_at=now)
            else:
                envelope.status = EnvelopeStatus.COMPLETED.value
            self._touch(envelope)
        return self.webhook_for(envelope)

    def decline(self, provider_envelope_id: str, recipient_key: str) -> SignedWebhook:
        with transaction.atomic():
            envelope = self._get(provider_envelope_id, for_update=True)
            if envelope.status != EnvelopeStatus.SENT:
                raise ConflictError("Envelope não está aguardando assinaturas.")
            target = next(
                (r for r in envelope.recipients if r["recipient_key"] == recipient_key), None
            )
            if target is None:
                raise NotFoundError("Participante inexistente.")
            target.update(
                status=RecipientStatus.DECLINED.value, changed_at=timezone.now().isoformat()
            )
            envelope.status = EnvelopeStatus.DECLINED.value
            self._touch(envelope)
        return self.webhook_for(envelope)

    def stored(self, provider_envelope_id: str) -> SimulatedEnvelope:
        """Estado atual do envelope no simulador (usado para reenviar o último evento)."""
        return self._get(provider_envelope_id)

    def webhook_for(self, envelope: SimulatedEnvelope) -> SignedWebhook:
        body = json.dumps(
            {
                "envelope_id": envelope.provider_envelope_id,
                "sequence": envelope.sequence,
                "status": envelope.status,
                "occurred_at": envelope.changed_at.isoformat(),
                "recipients": [
                    {
                        "recipient_key": r["recipient_key"],
                        "status": r["status"],
                        "changed_at": r.get("changed_at"),
                    }
                    for r in envelope.recipients
                ],
            },
            sort_keys=True,
        ).encode()
        return SignedWebhook(headers={SIGNATURE_HEADER: _sign(self._secret, body)}, body=body)

    # -- auxiliares ----------------------------------------------------------
    def _get(self, provider_envelope_id: str, *, for_update: bool = False) -> SimulatedEnvelope:
        queryset = SimulatedEnvelope.objects.filter(account_id=self._account_id)
        if for_update:
            queryset = queryset.select_for_update()
        envelope = queryset.filter(provider_envelope_id=provider_envelope_id).first()
        if envelope is None:
            raise NotFoundError("Envelope inexistente no simulador.")
        return envelope

    @staticmethod
    def _initial_recipients(request: EnvelopeRequest) -> list[dict[str, object]]:
        first = min(s.routing_order for s in request.signers)
        return [
            {
                "recipient_key": s.recipient_key,
                "name": s.name,
                "email": s.email,
                "routing_order": s.routing_order,
                "status": (
                    RecipientStatus.SENT if s.routing_order == first else RecipientStatus.CREATED
                ).value,
                "changed_at": None,
            }
            for s in sorted(request.signers, key=lambda s: (s.routing_order, s.recipient_key))
        ]

    @staticmethod
    def _touch(envelope: SimulatedEnvelope) -> None:
        envelope.sequence += 1
        envelope.changed_at = timezone.now()
        envelope.save(update_fields=["status", "recipients", "sequence", "changed_at"])

    def _state(self, envelope: SimulatedEnvelope) -> EnvelopeState:
        return EnvelopeState(
            provider_envelope_id=envelope.provider_envelope_id,
            status=EnvelopeStatus(envelope.status),
            recipients=[
                RecipientState(
                    recipient_key=r["recipient_key"],
                    status=RecipientStatus(r["status"]),
                    changed_at=datetime.fromisoformat(r["changed_at"])
                    if r.get("changed_at")
                    else None,
                )
                for r in envelope.recipients
            ],
            changed_at=envelope.changed_at,
        )
