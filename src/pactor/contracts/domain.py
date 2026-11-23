"""Regras puras de contratos: situações de elaboração e identidade do conteúdo.

Elaboração (este módulo), assinatura e vigência são situações separadas: um
contrato assinado pode ter início de vigência futuro, e o andamento da
assinatura não depende da vigência.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class ContractType(StrEnum):
    SERVICES = "services"
    SUPPLY = "supply"
    NDA = "nda"
    LICENSING = "licensing"
    AMENDMENT = "amendment"


CONTRACT_TYPE_LABELS: Mapping[ContractType, str] = MappingProxyType(
    {
        ContractType.SERVICES: "Prestação de serviços",
        ContractType.SUPPLY: "Fornecimento",
        ContractType.NDA: "Confidencialidade",
        ContractType.LICENSING: "Licenciamento",
        ContractType.AMENDMENT: "Aditivo",
    }
)


class PartyRole(StrEnum):
    CONTRACTOR = "contractor"  # contratante
    CONTRACTED = "contracted"  # contratada
    LICENSOR = "licensor"
    LICENSEE = "licensee"
    DISCLOSING = "disclosing"  # parte reveladora
    RECEIVING = "receiving"  # parte receptora
    MUTUAL = "mutual"


class DraftingStatus(StrEnum):
    DRAFT = "draft"
    IN_APPROVAL = "in_approval"
    APPROVED = "approved"
    SIGNING = "signing"
    SIGNED = "signed"
    CANCELLED = "cancelled"


_DRAFTING_TRANSITIONS: Mapping[DraftingStatus, frozenset[DraftingStatus]] = MappingProxyType(
    {
        DraftingStatus.DRAFT: frozenset({DraftingStatus.IN_APPROVAL, DraftingStatus.CANCELLED}),
        DraftingStatus.IN_APPROVAL: frozenset(
            {DraftingStatus.DRAFT, DraftingStatus.APPROVED, DraftingStatus.CANCELLED}
        ),
        DraftingStatus.APPROVED: frozenset(
            {DraftingStatus.DRAFT, DraftingStatus.SIGNING, DraftingStatus.CANCELLED}
        ),
        # Envelope anulado ou recusado devolve o contrato a "aprovado": a mesma
        # revisão aprovada pode seguir em novo envio.
        DraftingStatus.SIGNING: frozenset(
            {DraftingStatus.SIGNED, DraftingStatus.APPROVED, DraftingStatus.CANCELLED}
        ),
        DraftingStatus.SIGNED: frozenset(),
        DraftingStatus.CANCELLED: frozenset(),
    }
)


def can_transition(current: DraftingStatus, target: DraftingStatus) -> bool:
    return target in _DRAFTING_TRANSITIONS[current]


def allows_new_revision(status: DraftingStatus) -> bool:
    """Revisões novas só antes da assinatura; depois, alterações exigem aditivo."""
    return status in {DraftingStatus.DRAFT, DraftingStatus.IN_APPROVAL, DraftingStatus.APPROVED}


type Canonical = str | int | bool | list[Canonical] | dict[str, Canonical] | None


def canonical_json(value: Canonical) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AttachmentDigest:
    file_id: str
    name: str
    sha256: str


@dataclass(frozen=True, slots=True)
class SubmissionSnapshot:
    """Conjunto exato submetido à aprovação: conteúdo, metadados e anexos.

    O fingerprint identifica esse conjunto. Qualquer mudança relevante produz
    outro fingerprint e, portanto, não é coberta por aprovações anteriores.
    """

    content_sha256: str
    metadata: Mapping[str, Canonical]
    attachments: Sequence[AttachmentDigest]

    def fingerprint(self) -> str:
        document: dict[str, Canonical] = {
            "content_sha256": self.content_sha256,
            "metadata": dict(self.metadata),
            "attachments": [
                {"file_id": a.file_id, "name": a.name, "sha256": a.sha256}
                for a in sorted(self.attachments, key=lambda a: (a.file_id, a.sha256))
            ],
        }
        return sha256_text(canonical_json(document))
