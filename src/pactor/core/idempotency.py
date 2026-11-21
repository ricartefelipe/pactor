"""Idempotência persistida para operações sensíveis.

O registro é criado na mesma transação da operação. Uma segunda requisição
com a mesma chave espera o commit da primeira (índice único) e então:

* mesmo fingerprint e operação concluída: recebe a resposta original;
* fingerprint diferente: recebe ``idempotency_conflict``.

Se a primeira requisição falhar, a transação inteira é desfeita, inclusive o
registro, e a chave pode ser usada novamente.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from django.db import connection
from django.utils import timezone

from pactor.core.errors import DomainValidationError, IdempotencyConflictError
from pactor.core.models import IdempotencyRecord
from pactor.core.tenancy import require_tenant

KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status: int
    body: object


def validate_key(key: str | None) -> str:
    if key is None or not KEY_PATTERN.fullmatch(key):
        raise DomainValidationError(
            "Cabeçalho Idempotency-Key obrigatório (8 a 128 caracteres: letras, números, - e _)."
        )
    return key


def request_fingerprint(method: str, path: str, data: object) -> str:
    canonical = json.dumps(
        {"method": method.upper(), "path": path, "data": data},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def begin(*, actor_id: uuid.UUID, scope: str, key: str, fingerprint: str) -> StoredResponse | None:
    """Reserva a chave ou devolve a resposta já gravada para ela."""
    organization_id = require_tenant()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO core_idempotencyrecord "
            "(id, organization_id, created_at, actor_id, scope, key, fingerprint) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (organization_id, actor_id, scope, key) DO NOTHING RETURNING id",
            [uuid.uuid4(), organization_id, timezone.now(), actor_id, scope, key, fingerprint],
        )
        if cursor.fetchone() is not None:
            return None
    record = IdempotencyRecord.objects.get(
        organization_id=organization_id, actor_id=actor_id, scope=scope, key=key
    )
    if record.fingerprint != fingerprint:
        raise IdempotencyConflictError(
            "Chave de idempotência já utilizada com outro conteúdo.", details={"key": key}
        )
    if record.completed_at is None or record.response_status is None:
        raise IdempotencyConflictError(
            "Operação com esta chave ainda está em andamento.", details={"key": key}
        )
    return StoredResponse(status=record.response_status, body=record.response_body)


def complete(
    *,
    actor_id: uuid.UUID,
    scope: str,
    key: str,
    status: int,
    body: Mapping[str, object] | list[object] | None,
) -> None:
    updated = IdempotencyRecord.objects.filter(
        organization_id=require_tenant(), actor_id=actor_id, scope=scope, key=key
    ).update(response_status=status, response_body=body, completed_at=timezone.now())
    if updated != 1:
        raise RuntimeError("registro de idempotência ausente ao concluir a operação")
