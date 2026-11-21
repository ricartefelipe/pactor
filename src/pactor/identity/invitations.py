"""Tokens de convite derivados por HMAC.

Formato: ``<organization_id>.<invitation_id>.<assinatura>``. A organização no
token permite abrir o contexto de tenant antes de ler o convite (que é
protegido por RLS); a assinatura impede forjar ou alterar qualquer parte.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from hashlib import sha256

from django.conf import settings

from pactor.core.errors import DomainValidationError

_SALT = b"pactor.identity.invitation.v1"
_TOKEN_PARTS = 3  # organização, convite e assinatura


@dataclass(frozen=True, slots=True)
class InvitationToken:
    organization_id: uuid.UUID
    invitation_id: uuid.UUID


def _signature(organization_id: uuid.UUID, invitation_id: uuid.UUID) -> str:
    key = sha256(_SALT + settings.SECRET_KEY.encode()).digest()
    message = f"{organization_id}.{invitation_id}".encode()
    return hmac.new(key, message, sha256).hexdigest()


def make_token(organization_id: uuid.UUID, invitation_id: uuid.UUID) -> str:
    return f"{organization_id}.{invitation_id}.{_signature(organization_id, invitation_id)}"


def parse_token(token: str) -> InvitationToken:
    invalid = DomainValidationError("Convite inválido ou expirado.")
    parts = token.split(".")
    if len(parts) != _TOKEN_PARTS:
        raise invalid
    try:
        organization_id, invitation_id = uuid.UUID(parts[0]), uuid.UUID(parts[1])
    except ValueError as exc:
        raise invalid from exc
    if not hmac.compare_digest(parts[2], _signature(organization_id, invitation_id)):
        raise invalid
    return InvitationToken(organization_id=organization_id, invitation_id=invitation_id)
