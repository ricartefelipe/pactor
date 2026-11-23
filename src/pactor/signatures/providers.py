"""Construção do adaptador de assinatura configurado."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings

from pactor.config.env import ConfigurationError
from pactor.signatures.docusign import DocusignProvider, DocusignSettings
from pactor.signatures.ports import SignatureProvider
from pactor.signatures.simulator import SimulatorProvider


def docusign_settings() -> DocusignSettings:
    required = {
        "DOCUSIGN_INTEGRATION_KEY": settings.DOCUSIGN_INTEGRATION_KEY,
        "DOCUSIGN_USER_ID": settings.DOCUSIGN_USER_ID,
        "DOCUSIGN_ACCOUNT_ID": settings.DOCUSIGN_ACCOUNT_ID,
        "DOCUSIGN_PRIVATE_KEY_PATH": settings.DOCUSIGN_PRIVATE_KEY_PATH,
        "DOCUSIGN_CONNECT_HMAC_KEY": settings.DOCUSIGN_CONNECT_HMAC_KEY,
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        raise ConfigurationError(f"Credenciais Docusign ausentes: {', '.join(missing)}")
    return DocusignSettings(
        integration_key=settings.DOCUSIGN_INTEGRATION_KEY,
        user_id=settings.DOCUSIGN_USER_ID,
        account_id=settings.DOCUSIGN_ACCOUNT_ID,
        private_key_pem=Path(settings.DOCUSIGN_PRIVATE_KEY_PATH).read_text(encoding="utf-8"),
        connect_hmac_key=settings.DOCUSIGN_CONNECT_HMAC_KEY,
        auth_server=settings.DOCUSIGN_AUTH_SERVER,
        base_uri=settings.DOCUSIGN_BASE_URI,
    )


def get_provider(name: str | None = None) -> SignatureProvider:
    selected = name or settings.PACTOR_SIGNATURE_PROVIDER
    if selected == "simulator":
        return SimulatorProvider()
    if selected == "docusign":
        return DocusignProvider(docusign_settings())
    raise ConfigurationError(f"Provedor de assinatura desconhecido: {selected!r}")
