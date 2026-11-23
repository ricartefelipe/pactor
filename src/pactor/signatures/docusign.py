"""Adaptador Docusign eSignature REST API v2.1 (ambiente de demonstração por padrão).

Autenticação: OAuth JWT Grant (``signature impersonation``) com chave RSA da
integração. Idempotência: cada envelope leva o campo customizado
``pactor_reference`` com o identificador do envelope no Pactor; após um
timeout, :meth:`find_by_reference` consulta ``listStatusChanges`` filtrando
por esse campo antes de qualquer novo envio. Webhooks: Docusign Connect em
JSON com HMAC (``X-DocuSign-Signature-1``, Base64 de HMAC-SHA256 do corpo).

Sem credenciais configuradas o adaptador não é construído; os testes de
contrato usam transporte HTTP simulado e não comprovam a integração real.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import httpx
import jwt

from pactor.signatures.ports import (
    EnvelopeHandle,
    EnvelopeRequest,
    EnvelopeState,
    EnvelopeStatus,
    InvalidWebhookError,
    ProviderEvent,
    ProviderOutcomeUnknownError,
    ProviderRejectedError,
    ProviderUnavailableError,
    RecipientState,
    RecipientStatus,
    ReturnedDocument,
)

REFERENCE_FIELD = "pactor_reference"
HMAC_HEADER = "X-DocuSign-Signature-1"
_TOKEN_LIFETIME_SECONDS = 3600
_TOKEN_REFRESH_MARGIN_SECONDS = 300
_SERVER_ERROR = 500
_CLIENT_ERROR = 400
_LOOKBACK_DAYS = 30

_ENVELOPE_STATUS: Mapping[str, EnvelopeStatus] = MappingProxyType(
    {
        "sent": EnvelopeStatus.SENT,
        "delivered": EnvelopeStatus.DELIVERED,
        "completed": EnvelopeStatus.COMPLETED,
        "declined": EnvelopeStatus.DECLINED,
        "voided": EnvelopeStatus.VOIDED,
    }
)
_RECIPIENT_STATUS: Mapping[str, RecipientStatus] = MappingProxyType(
    {
        "created": RecipientStatus.CREATED,
        "sent": RecipientStatus.SENT,
        "delivered": RecipientStatus.DELIVERED,
        "signed": RecipientStatus.SIGNED,
        "completed": RecipientStatus.SIGNED,
        "declined": RecipientStatus.DECLINED,
    }
)


@dataclass(frozen=True, slots=True)
class DocusignSettings:
    integration_key: str
    user_id: str
    account_id: str
    private_key_pem: str
    connect_hmac_key: str
    auth_server: str = "account-d.docusign.com"
    base_uri: str = "https://demo.docusign.net/restapi"


def _parse_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _envelope_status(raw: object) -> EnvelopeStatus:
    status = _ENVELOPE_STATUS.get(str(raw).lower())
    if status is None:
        raise ProviderRejectedError(f"situação de envelope não suportada: {raw!r}")
    return status


def _recipients(raw: object) -> list[RecipientState]:
    signers = raw.get("signers", []) if isinstance(raw, Mapping) else []
    states: list[RecipientState] = []
    for signer in signers if isinstance(signers, list) else []:
        status = _RECIPIENT_STATUS.get(str(signer.get("status", "")).lower())
        if status is None:
            continue
        changed = _parse_time(
            signer.get("signedDateTime")
            or signer.get("deliveredDateTime")
            or signer.get("sentDateTime")
        )
        states.append(RecipientState(str(signer.get("recipientId")), status, changed))
    return states


class DocusignProvider:
    name = "docusign"

    def __init__(self, config: DocusignSettings, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))
        self._token: str | None = None
        self._token_expires_at = 0.0

    @property
    def account_id(self) -> str:
        return self._config.account_id

    # -- autenticação ----------------------------------------------------------
    def _access_token(self) -> str:
        now = time.time()
        if self._token is not None and now < self._token_expires_at - _TOKEN_REFRESH_MARGIN_SECONDS:
            return self._token
        assertion = jwt.encode(
            {
                "iss": self._config.integration_key,
                "sub": self._config.user_id,
                "aud": self._config.auth_server,
                "iat": int(now),
                "exp": int(now) + _TOKEN_LIFETIME_SECONDS,
                "scope": "signature impersonation",
            },
            self._config.private_key_pem,
            algorithm="RS256",
        )
        response = self._send(
            "POST",
            f"https://{self._config.auth_server}/oauth/token",
            form={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            authenticated=False,
        )
        payload = response.json()
        self._token = str(payload["access_token"])
        self._token_expires_at = now + float(payload.get("expires_in", _TOKEN_LIFETIME_SECONDS))
        return self._token

    def _url(self, path: str) -> str:
        return f"{self._config.base_uri}/v2.1/accounts/{self._config.account_id}{path}"

    def _send(
        self,
        method: str,
        url: str,
        *,
        authenticated: bool = True,
        outcome_unknown_on_failure: bool = False,
        json_body: Mapping[str, object] | None = None,
        form: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._access_token()}"} if authenticated else {}
        try:
            response = self._client.request(
                method, url, headers=headers, json=json_body, data=form, params=params
            )
        except httpx.TimeoutException as exc:
            if outcome_unknown_on_failure:
                raise ProviderOutcomeUnknownError(f"timeout em {method} {url}") from exc
            raise ProviderUnavailableError(f"timeout em {method} {url}") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(f"falha de conexão em {method} {url}") from exc
        if response.status_code >= _SERVER_ERROR:
            if outcome_unknown_on_failure:
                raise ProviderOutcomeUnknownError(f"{response.status_code} em {method} {url}")
            raise ProviderUnavailableError(f"{response.status_code} em {method} {url}")
        if response.status_code >= _CLIENT_ERROR:
            raise ProviderRejectedError(
                f"{response.status_code} em {method} {url}: {response.text[:300]}"
            )
        return response

    # -- porta ---------------------------------------------------------------
    def create_envelope(self, request: EnvelopeRequest) -> EnvelopeHandle:
        body = {
            "emailSubject": request.subject[:100],
            "status": "sent",
            "documents": [
                {
                    "documentId": "1",
                    "name": request.document_name,
                    "fileExtension": "pdf",
                    "documentBase64": base64.b64encode(request.document).decode(),
                }
            ],
            "recipients": {
                "signers": [
                    {
                        "recipientId": s.recipient_key,
                        "routingOrder": str(s.routing_order),
                        "name": s.name,
                        "email": s.email,
                    }
                    for s in request.signers
                ]
            },
            "customFields": {
                "textCustomFields": [
                    {
                        "name": REFERENCE_FIELD,
                        "value": request.reference,
                        "show": "false",
                        "required": "false",
                    }
                ]
            },
        }
        # Timeout ou 5xx na criação não permitem saber se o envelope existe:
        # o chamador deve consultar por referência antes de tentar de novo.
        response = self._send(
            "POST", self._url("/envelopes"), json_body=body, outcome_unknown_on_failure=True
        )
        payload = response.json()
        return EnvelopeHandle(
            str(payload["envelopeId"]), _envelope_status(payload.get("status", "sent"))
        )

    def find_by_reference(self, reference: str) -> EnvelopeHandle | None:
        since = (datetime.now(UTC) - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        response = self._send(
            "GET",
            self._url("/envelopes"),
            params={"from_date": since, "custom_field": f"{REFERENCE_FIELD}={reference}"},
        )
        envelopes = response.json().get("envelopes") or []
        if not envelopes:
            return None
        found = envelopes[0]
        return EnvelopeHandle(
            str(found["envelopeId"]), _envelope_status(found.get("status", "sent"))
        )

    def get_state(self, provider_envelope_id: str) -> EnvelopeState:
        response = self._send(
            "GET", self._url(f"/envelopes/{provider_envelope_id}"), params={"include": "recipients"}
        )
        payload = response.json()
        changed = _parse_time(payload.get("statusChangedDateTime")) or datetime.now(UTC)
        return EnvelopeState(
            provider_envelope_id=provider_envelope_id,
            status=_envelope_status(payload.get("status")),
            recipients=_recipients(payload.get("recipients")),
            changed_at=changed,
        )

    def void(self, provider_envelope_id: str, reason: str) -> None:
        self._send(
            "PUT",
            self._url(f"/envelopes/{provider_envelope_id}"),
            json_body={"status": "voided", "voidedReason": reason[:200]},
        )

    def download_documents(self, provider_envelope_id: str) -> Sequence[ReturnedDocument]:
        combined = self._send(
            "GET", self._url(f"/envelopes/{provider_envelope_id}/documents/combined")
        )
        certificate = self._send(
            "GET", self._url(f"/envelopes/{provider_envelope_id}/documents/certificate")
        )
        return [
            ReturnedDocument("signed", f"{provider_envelope_id}-assinado.pdf", combined.content),
            ReturnedDocument(
                "evidence", f"{provider_envelope_id}-certificado.pdf", certificate.content
            ),
        ]

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent:
        given = headers.get(HMAC_HEADER, "")
        expected = base64.b64encode(
            hmac.new(self._config.connect_hmac_key.encode(), body, hashlib.sha256).digest()
        ).decode()
        if not given or not hmac.compare_digest(given, expected):
            raise InvalidWebhookError("assinatura HMAC do Connect inválida")
        try:
            payload = json.loads(body)
            data = payload["data"]
            summary = data.get("envelopeSummary") or {}
            envelope_id = str(data["envelopeId"])
            occurred = _parse_time(payload.get("generatedDateTime"))
            if occurred is None:
                raise InvalidWebhookError("evento sem generatedDateTime")
            state = EnvelopeState(
                provider_envelope_id=envelope_id,
                status=_envelope_status(
                    summary.get("status") or str(payload["event"]).removeprefix("envelope-")
                ),
                recipients=_recipients(summary.get("recipients")),
                changed_at=_parse_time(summary.get("statusChangedDateTime")) or occurred,
            )
        except (KeyError, TypeError, ValueError, ProviderRejectedError) as exc:
            raise InvalidWebhookError("corpo do Connect malformado ou não suportado") from exc
        identity = "|".join(
            (
                envelope_id,
                str(payload.get("event")),
                str(payload.get("generatedDateTime")),
                str(data.get("recipientId", "")),
            )
        )
        dedup = hashlib.sha256(identity.encode()).hexdigest()
        return ProviderEvent(
            dedup_key=dedup, provider_envelope_id=envelope_id, state=state, occurred_at=occurred
        )
