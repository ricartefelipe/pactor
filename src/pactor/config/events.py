"""Roteamento explícito de tópicos da outbox para consumidores e filas.

Mapeamento imutável e declarado em um único lugar: ler este arquivo responde
"quem reage a este evento", sem depender de registros feitos por import.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class Route:
    consumer: str
    queue: str


ROUTES: Mapping[str, tuple[Route, ...]] = MappingProxyType(
    {
        "identity.invitation_created": (
            Route("pactor.notifications.invitation_email", "notifications"),
        ),
        "identity.membership_revoked": (),
        "signatures.envelope_requested": (
            Route("pactor.signatures.dispatch_envelope", "integrations"),
        ),
        "signatures.envelope_completed": (
            Route("pactor.signatures.fetch_documents", "integrations"),
        ),
        "signatures.void_requested": (Route("pactor.signatures.void_envelope", "integrations"),),
    }
)
