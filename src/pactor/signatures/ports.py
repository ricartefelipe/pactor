"""Porta de integração com provedores de assinatura eletrônica.

Os adaptadores traduzem estas estruturas para a API do provedor. O núcleo
nunca depende de formatos específicos de um provedor.

Situações de envelope e de participante são independentes: a assinatura de
um participante não conclui o envelope.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol


class EnvelopeStatus(StrEnum):
    SENT = "sent"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    DECLINED = "declined"
    VOIDED = "voided"


class RecipientStatus(StrEnum):
    CREATED = "created"
    SENT = "sent"
    DELIVERED = "delivered"
    SIGNED = "signed"
    DECLINED = "declined"


# Ordem de progressão. Situações terminais têm o maior valor: um evento
# anterior que chegue depois (fora de ordem) nunca regride a situação.
ENVELOPE_RANK: Mapping[EnvelopeStatus, int] = MappingProxyType(
    {
        EnvelopeStatus.SENT: 1,
        EnvelopeStatus.DELIVERED: 2,
        EnvelopeStatus.COMPLETED: 9,
        EnvelopeStatus.DECLINED: 9,
        EnvelopeStatus.VOIDED: 9,
    }
)
RECIPIENT_RANK: Mapping[RecipientStatus, int] = MappingProxyType(
    {
        RecipientStatus.CREATED: 0,
        RecipientStatus.SENT: 1,
        RecipientStatus.DELIVERED: 2,
        RecipientStatus.SIGNED: 9,
        RecipientStatus.DECLINED: 9,
    }
)
TERMINAL_ENVELOPE = frozenset(
    {EnvelopeStatus.COMPLETED, EnvelopeStatus.DECLINED, EnvelopeStatus.VOIDED}
)


@dataclass(frozen=True, slots=True)
class Signer:
    recipient_key: str
    name: str
    email: str
    routing_order: int


@dataclass(frozen=True, slots=True)
class EnvelopeRequest:
    """Pedido de envelope. ``reference`` é estável e identifica o envio no provedor."""

    reference: str
    subject: str
    document_name: str
    document: bytes
    document_sha256: str
    signers: Sequence[Signer]


@dataclass(frozen=True, slots=True)
class EnvelopeHandle:
    provider_envelope_id: str
    status: EnvelopeStatus


@dataclass(frozen=True, slots=True)
class RecipientState:
    recipient_key: str
    status: RecipientStatus
    changed_at: datetime | None


@dataclass(frozen=True, slots=True)
class EnvelopeState:
    provider_envelope_id: str
    status: EnvelopeStatus
    recipients: Sequence[RecipientState]
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """Evento normalizado recebido por webhook ou obtido por reconciliação."""

    dedup_key: str
    provider_envelope_id: str
    state: EnvelopeState
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ReturnedDocument:
    kind: str  # "signed" (documento concluído) ou "evidence" (certificado/evidências)
    name: str
    content: bytes


class SignatureProviderError(Exception):
    """Falha de integração com o provedor."""


class ProviderUnavailableError(SignatureProviderError):
    """A requisição não chegou ao provedor (conexão recusada, DNS, 5xx antes do processamento)."""


class ProviderOutcomeUnknownError(SignatureProviderError):
    """Timeout ou queda após o envio: o envelope pode ter sido criado no provedor."""


class ProviderRejectedError(SignatureProviderError):
    """O provedor recusou a requisição (dados inválidos, credenciais, permissões)."""


class InvalidWebhookError(SignatureProviderError):
    """Assinatura do webhook ausente ou inválida, ou corpo malformado."""


class SignatureProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def account_id(self) -> str: ...

    def create_envelope(self, request: EnvelopeRequest) -> EnvelopeHandle: ...

    def find_by_reference(self, reference: str) -> EnvelopeHandle | None: ...

    def get_state(self, provider_envelope_id: str) -> EnvelopeState: ...

    def void(self, provider_envelope_id: str, reason: str) -> None: ...

    def download_documents(self, provider_envelope_id: str) -> Sequence[ReturnedDocument]: ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent: ...
