"""Testes de contrato do adaptador Docusign com transporte HTTP simulado.

Verificam o formato das requisições, o mapeamento das respostas e a
classificação de falhas. Não exercitam a conta de demonstração real: isso
exige credenciais (ver tests/external e docs/STATUS.md).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from pactor.signatures.docusign import (
    HMAC_HEADER,
    REFERENCE_FIELD,
    DocusignProvider,
    DocusignSettings,
)
from pactor.signatures.ports import (
    EnvelopeRequest,
    EnvelopeStatus,
    InvalidWebhookError,
    ProviderOutcomeUnknownError,
    ProviderRejectedError,
    ProviderUnavailableError,
    RecipientStatus,
    Signer,
)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
CONFIG = DocusignSettings(
    integration_key="chave-integracao",
    user_id="usuario-api",
    account_id="conta-demo",
    private_key_pem=PRIVATE_PEM,
    connect_hmac_key="segredo-connect",
)
REQUEST = EnvelopeRequest(
    reference="ref-123",
    subject="Assinatura: CT-2026-00001",
    document_name="CT-2026-00001.pdf",
    document=b"%PDF-1.4 teste",
    document_sha256=hashlib.sha256(b"%PDF-1.4 teste").hexdigest(),
    signers=[
        Signer("1", "Pessoa A", "a@exemplo.test", 1),
        Signer("2", "Pessoa B", "b@exemplo.test", 2),
    ],
)

type Handler = Callable[[httpx.Request], httpx.Response]


def _provider(handler: Handler, calls: list[httpx.Request]) -> DocusignProvider:
    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
        return handler(request)

    return DocusignProvider(CONFIG, client=httpx.Client(transport=httpx.MockTransport(recording)))


def test_create_envelope_sends_reference_order_and_document() -> None:
    calls: list[httpx.Request] = []
    provider = _provider(
        lambda _r: httpx.Response(201, json={"envelopeId": "env-1", "status": "sent"}), calls
    )
    handle = provider.create_envelope(REQUEST)
    assert (handle.provider_envelope_id, handle.status) == ("env-1", EnvelopeStatus.SENT)
    token_call, create_call = calls
    assertion = dict(x.split("=", 1) for x in token_call.content.decode().split("&"))["assertion"]
    claims = jwt.decode(
        assertion, _KEY.public_key(), algorithms=["RS256"], audience="account-d.docusign.com"
    )
    assert (claims["iss"], claims["sub"], claims["scope"]) == (
        "chave-integracao",
        "usuario-api",
        "signature impersonation",
    )
    assert (
        str(create_call.url)
        == "https://demo.docusign.net/restapi/v2.1/accounts/conta-demo/envelopes"
    )
    assert create_call.headers["Authorization"] == "Bearer token-1"
    body = json.loads(create_call.content)
    assert body["status"] == "sent"
    assert base64.b64decode(body["documents"][0]["documentBase64"]) == REQUEST.document
    assert [(s["recipientId"], s["routingOrder"]) for s in body["recipients"]["signers"]] == [
        ("1", "1"),
        ("2", "2"),
    ]
    assert body["customFields"]["textCustomFields"] == [
        {"name": REFERENCE_FIELD, "value": "ref-123", "show": "false", "required": "false"}
    ]


def test_token_is_reused_between_calls() -> None:
    calls: list[httpx.Request] = []
    provider = _provider(
        lambda _r: httpx.Response(200, json={"status": "sent", "recipients": {}}), calls
    )
    provider.get_state("env-1")
    provider.get_state("env-1")
    assert sum(1 for c in calls if c.url.path == "/oauth/token") == 1


def test_find_by_reference_filters_by_custom_field() -> None:
    calls: list[httpx.Request] = []
    provider = _provider(
        lambda _r: httpx.Response(
            200, json={"envelopes": [{"envelopeId": "env-9", "status": "delivered"}]}
        ),
        calls,
    )
    handle = provider.find_by_reference("ref-123")
    assert handle is not None
    assert (handle.provider_envelope_id, handle.status) == ("env-9", EnvelopeStatus.DELIVERED)
    assert calls[-1].url.params["custom_field"] == f"{REFERENCE_FIELD}=ref-123"
    assert "from_date" in calls[-1].url.params
    empty = _provider(lambda _r: httpx.Response(200, json={"resultSetSize": "0"}), [])
    assert empty.find_by_reference("ref-x") is None


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ReadTimeout("lento"), ProviderOutcomeUnknownError),
        (httpx.Response(503), ProviderOutcomeUnknownError),
        (httpx.ConnectError("recusado"), ProviderUnavailableError),
        (httpx.Response(400, json={"errorCode": "INVALID_REQUEST_BODY"}), ProviderRejectedError),
    ],
)
def test_create_failures_are_classified(
    failure: Exception | httpx.Response, expected: type[Exception]
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(failure, httpx.Response):
            return failure
        raise failure

    with pytest.raises(expected):
        _provider(handler, []).create_envelope(REQUEST)


def test_timeout_on_reads_is_unavailable_not_unknown() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento")

    with pytest.raises(ProviderUnavailableError):
        _provider(handler, []).get_state("env-1")


def test_get_state_maps_envelope_and_recipient_statuses() -> None:
    payload = {
        "status": "delivered",
        "statusChangedDateTime": "2026-11-22T10:00:00Z",
        "recipients": {
            "signers": [
                {
                    "recipientId": "1",
                    "status": "completed",
                    "signedDateTime": "2026-11-22T09:00:00Z",
                },
                {
                    "recipientId": "2",
                    "status": "delivered",
                    "deliveredDateTime": "2026-11-22T09:30:00Z",
                },
                {"recipientId": "3", "status": "autoresponded"},
            ]
        },
    }
    state = _provider(lambda _r: httpx.Response(200, json=payload), []).get_state("env-1")
    assert state.status is EnvelopeStatus.DELIVERED
    assert [(r.recipient_key, r.status) for r in state.recipients] == [
        ("1", RecipientStatus.SIGNED),
        ("2", RecipientStatus.DELIVERED),
    ]


def test_unsupported_envelope_status_is_rejected() -> None:
    with pytest.raises(ProviderRejectedError):
        _provider(lambda _r: httpx.Response(200, json={"status": "correct"}), []).get_state("env-1")


def test_void_and_download_use_documented_endpoints() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/documents/combined"):
            return httpx.Response(200, content=b"%PDF assinado")
        if request.url.path.endswith("/documents/certificate"):
            return httpx.Response(200, content=b"%PDF certificado")
        return httpx.Response(200, json={})

    provider = _provider(handler, calls)
    provider.void("env-1", "Signatário incorreto")
    assert calls[-1].method == "PUT"
    assert json.loads(calls[-1].content) == {
        "status": "voided",
        "voidedReason": "Signatário incorreto",
    }
    documents = provider.download_documents("env-1")
    assert [(d.kind, d.content) for d in documents] == [
        ("signed", b"%PDF assinado"),
        ("evidence", b"%PDF certificado"),
    ]


def _connect_body(event: str = "envelope-completed", status: str = "completed") -> bytes:
    return json.dumps(
        {
            "event": event,
            "generatedDateTime": "2026-11-22T10:00:00.123Z",
            "data": {
                "accountId": "conta-demo",
                "envelopeId": "env-1",
                "envelopeSummary": {
                    "status": status,
                    "statusChangedDateTime": "2026-11-22T10:00:00Z",
                    "recipients": {"signers": [{"recipientId": "1", "status": "completed"}]},
                },
            },
        }
    ).encode()


def _sign(body: bytes, key: str = "segredo-connect") -> dict[str, str]:
    digest = hmac.new(key.encode(), body, hashlib.sha256).digest()
    return {HMAC_HEADER: base64.b64encode(digest).decode()}


def test_connect_webhook_is_verified_and_normalized() -> None:
    provider = _provider(lambda _r: httpx.Response(200), [])
    body = _connect_body()
    event = provider.parse_webhook(_sign(body), body)
    assert event.provider_envelope_id == "env-1"
    assert event.state.status is EnvelopeStatus.COMPLETED
    assert event.state.recipients[0].status is RecipientStatus.SIGNED
    assert event.dedup_key == provider.parse_webhook(_sign(body), body).dedup_key
    other = _connect_body(event="envelope-delivered", status="delivered")
    assert provider.parse_webhook(_sign(other), other).dedup_key != event.dedup_key


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({}, _connect_body()),
        (_sign(_connect_body(), key="outra-chave"), _connect_body()),
        (_sign(b"{}"), b"{}"),
        (_sign(b"nao-json"), b"nao-json"),
        (_sign(_connect_body(status="correct")), _connect_body(status="correct")),
    ],
)
def test_invalid_connect_webhooks_are_rejected(headers: dict[str, str], body: bytes) -> None:
    with pytest.raises(InvalidWebhookError):
        _provider(lambda _r: httpx.Response(200), []).parse_webhook(headers, body)
