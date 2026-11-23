"""Armazenamento privado de documentos de envelopes, com verificação de hash.

Caminhos são gerados pelo servidor; nenhum nome enviado por usuário vira
caminho de arquivo. Leituras recalculam o SHA-256 e recusam bytes alterados.
"""

from __future__ import annotations

import hashlib
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from pactor.signatures.models import DocumentKind, Envelope, EnvelopeDocument

_EXTENSIONS = {"application/pdf": "pdf", "application/json": "json"}


class DocumentIntegrityError(RuntimeError):
    """Bytes armazenados não correspondem ao hash registrado."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def store(
    envelope: Envelope, kind: DocumentKind, name: str, content_type: str, content: bytes
) -> EnvelopeDocument:
    extension = _EXTENSIONS.get(content_type, "bin")
    folder = f"signatures/{envelope.organization_id}/{envelope.id}"
    path = f"{folder}/{kind.value}-{uuid.uuid4().hex}.{extension}"
    saved = default_storage.save(path, ContentFile(content))
    return EnvelopeDocument.objects.create(
        organization_id=envelope.organization_id,
        envelope=envelope,
        kind=kind.value,
        name=name[:200],
        content_type=content_type,
        storage_path=saved,
        sha256=sha256_bytes(content),
        size=len(content),
    )


def read_verified(document: EnvelopeDocument) -> bytes:
    with default_storage.open(document.storage_path, "rb") as handle:
        content = bytes(handle.read())
    if sha256_bytes(content) != document.sha256:
        raise DocumentIntegrityError(f"hash divergente para o documento {document.id}")
    return content
