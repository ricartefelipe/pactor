"""Conferência dos documentos armazenados contra os hashes registrados no banco.

Usada depois de restaurar banco e volume de mídia (``RUNBOOKS.md``) e em
verificações periódicas: cada arquivo é relido inteiro, em blocos, e o SHA-256
e o tamanho são comparados com o registro. Nada é alterado.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from django.core.files.storage import default_storage

from pactor.core.tenancy import tenant_context
from pactor.files.models import FileStatus, StoredFile
from pactor.identity.models import Organization
from pactor.signatures.models import EnvelopeDocument


class UnknownOrganizationsError(LookupError):
    """Organizações pedidas que não existem (evita uma conferência vazia dada como ok)."""

    def __init__(self, missing: Iterable[uuid.UUID]) -> None:
        self.missing = sorted(missing, key=str)
        super().__init__(", ".join(str(m) for m in self.missing))


# Arquivos recusados no recebimento porque os bytes mudaram durante a quarentena: a
# divergência já foi detectada e registrada, e eles nunca são servidos.
_KNOWN_ALTERED = "hash_mismatch"


class Problem(StrEnum):
    MISSING = "ausente"
    SIZE_MISMATCH = "tamanho divergente"
    HASH_MISMATCH = "hash divergente"


class Source(StrEnum):
    FILE = "arquivo"
    PREVIEW = "prévia"
    SIGNATURE_DOCUMENT = "documento de assinatura"


@dataclass(frozen=True, slots=True)
class Finding:
    organization_id: uuid.UUID
    source: Source
    record_id: uuid.UUID
    path: str
    problem: Problem


@dataclass(slots=True)
class Report:
    checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


@dataclass(frozen=True, slots=True)
class _Expected:
    source: Source
    record_id: uuid.UUID
    path: str
    sha256: str | None
    size: int | None


def _digest(path: str) -> tuple[str, int] | None:
    """SHA-256 e tamanho do arquivo armazenado, ou ``None`` se não existir."""
    if not default_storage.exists(path):
        return None
    digest, size = hashlib.sha256(), 0
    with default_storage.open(path, "rb") as handle:
        for chunk in handle.chunks():
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _expected_for(organization_id: uuid.UUID) -> Iterator[_Expected]:
    for stored in StoredFile.objects.filter(organization_id=organization_id).order_by("id"):
        if stored.status == FileStatus.REJECTED and stored.rejection_reason == _KNOWN_ALTERED:
            # Só a presença é conferida: o hash diverge por definição.
            yield _Expected(Source.FILE, stored.id, stored.storage_path, None, None)
            continue
        yield _Expected(Source.FILE, stored.id, stored.storage_path, stored.sha256, stored.size)
        if stored.preview_path:
            yield _Expected(Source.PREVIEW, stored.id, stored.preview_path, None, None)
    for document in EnvelopeDocument.objects.filter(organization_id=organization_id).order_by("id"):
        yield _Expected(
            Source.SIGNATURE_DOCUMENT,
            document.id,
            document.storage_path,
            document.sha256,
            document.size,
        )


def _problem(expected: _Expected) -> Problem | None:
    actual = _digest(expected.path)
    if actual is None:
        return Problem.MISSING
    sha256, size = actual
    if expected.size is not None and size != expected.size:
        return Problem.SIZE_MISMATCH
    if expected.sha256 is not None and sha256 != expected.sha256:
        return Problem.HASH_MISMATCH
    return None


def verify_documents(organization_ids: Iterable[uuid.UUID] | None = None) -> Report:
    """Confere arquivos, prévias e documentos de assinatura de cada organização.

    Cada organização é lida no próprio contexto de tenant (RLS continua valendo).
    """
    organizations = Organization.objects.order_by("id").values_list("id", flat=True)
    if organization_ids is not None:
        requested = set(organization_ids)
        organizations = organizations.filter(id__in=requested)
        missing = requested - set(organizations)
        if missing:
            raise UnknownOrganizationsError(missing)
    report = Report()
    for organization_id in organizations:
        with tenant_context(organization_id):
            expected = list(_expected_for(organization_id))
        for item in expected:
            report.checked += 1
            problem = _problem(item)
            if problem is not None:
                report.findings.append(
                    Finding(organization_id, item.source, item.record_id, item.path, problem)
                )
    return report
