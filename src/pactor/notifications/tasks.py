"""Consumidores de eventos que enviam notificações por e-mail."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import send_mail

from pactor.core.outbox import EventEnvelope
from pactor.core.tasks import SideEffect, side_effect_consumer
from pactor.identity.invitations import make_token
from pactor.identity.models import Invitation, InvitationStatus


@dataclass(frozen=True, slots=True)
class Email:
    to: str
    subject: str
    body: str


def prepare_invitation_email(envelope: EventEnvelope) -> Email | None:
    invitation = (
        Invitation.objects.select_related("organization")
        .filter(id=uuid.UUID(str(envelope.payload["invitation_id"])))
        .first()
    )
    if invitation is None or invitation.status != InvitationStatus.PENDING:
        return None
    token = make_token(invitation.organization_id, invitation.id)
    link = f"{settings.PACTOR_PUBLIC_URL}/convites/aceitar?token={token}"
    name = invitation.organization.name
    return Email(
        to=invitation.email,
        subject=f"Convite para {name} no Pactor",
        body=(
            f"Você foi convidado para participar de {name} no Pactor.\n\n"
            f"Para aceitar, acesse: {link}\n\n"
            f"O convite expira em {invitation.expires_at:%d/%m/%Y %H:%M} (UTC)."
        ),
    )


def deliver(email: Email) -> None:
    send_mail(email.subject, email.body, None, [email.to])


invitation_email = side_effect_consumer(
    "pactor.notifications.invitation_email",
    SideEffect(prepare=prepare_invitation_email, perform=deliver),
)
