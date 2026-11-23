"""Recebimento de webhooks de provedores de assinatura.

Sem sessão, sem CSRF (a autenticidade vem do HMAC do provedor) e sem a
transação por requisição: o evento é gravado na inbox antes de ser aplicado,
para que uma falha no processamento não perca o evento.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from pactor.config.env import ConfigurationError
from pactor.signatures import services
from pactor.signatures.ports import InvalidWebhookError

logger = logging.getLogger(__name__)

MAX_WEBHOOK_BYTES = 1024 * 1024
SUPPORTED_PROVIDERS = frozenset({"simulator", "docusign"})


@csrf_exempt
@transaction.non_atomic_requests
@require_POST
def receive(request: HttpRequest, provider: str) -> JsonResponse:
    if provider not in SUPPORTED_PROVIDERS:
        return JsonResponse({"error": {"code": "not_found"}}, status=404)
    if len(request.body) > MAX_WEBHOOK_BYTES:
        return JsonResponse({"error": {"code": "payload_too_large"}}, status=413)
    try:
        event, outcome, duplicate = services.ingest_webhook(
            provider, dict(request.headers), request.body
        )
    except InvalidWebhookError:
        logger.warning("signature_webhook_rejected", extra={"provider": provider})
        return JsonResponse({"error": {"code": "invalid_signature"}}, status=401)
    except ConfigurationError:
        logger.exception("signature_webhook_provider_not_configured", extra={"provider": provider})
        return JsonResponse({"error": {"code": "provider_not_configured"}}, status=503)
    return JsonResponse({"event_id": event.id, "outcome": outcome.value, "duplicate": duplicate})
