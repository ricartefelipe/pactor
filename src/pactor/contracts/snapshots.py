"""O que uma revisão aprova: conteúdo, metadados do contrato e anexos.

Fica fora de ``services`` para que aprovações e assinaturas possam conferir a
impressão digital sem depender dos casos de uso de contratos.
"""

from __future__ import annotations

from pactor.contracts.domain import Canonical, SubmissionSnapshot
from pactor.contracts.models import AmendmentChange, Contract, Revision
from pactor.files.selectors import submission_attachments


def metadata_snapshot(contract: Contract) -> dict[str, Canonical]:
    """Metadados relevantes cobertos pela aprovação."""
    return {
        "title": contract.title,
        "contract_type": contract.contract_type,
        "counterparty_id": str(contract.counterparty_id),
        "counterparty_document": contract.counterparty.document,
        "organization_role": contract.organization_role,
        "counterparty_role": contract.counterparty_role,
        "department_id": str(contract.department_id) if contract.department_id else None,
        "confidentiality": contract.confidentiality,
        "classification": contract.classification,
        "total_value": str(contract.total_value) if contract.total_value is not None else None,
        "currency": contract.currency,
        "planned_start": contract.planned_start.isoformat() if contract.planned_start else None,
        "planned_end": contract.planned_end.isoformat() if contract.planned_end else None,
        "parent_id": str(contract.parent_id) if contract.parent_id else None,
        # Alterações de condição de um aditivo fazem parte do que é aprovado e assinado.
        "amendment_changes": [
            {"field": c.field, "value": c.value, "effective_from": c.effective_from.isoformat()}
            for c in AmendmentChange.objects.filter(
                organization_id=contract.organization_id, amendment_id=contract.id
            ).order_by("created_at", "id")
        ],
    }


def current_fingerprint(contract: Contract, revision: Revision) -> str:
    """Impressão digital da revisão combinada com os dados e anexos atuais do contrato.

    Difere da impressão digital gravada na revisão quando metadados ou anexos
    mudaram depois da publicação: nesse caso, a revisão já não representa o
    contrato e não pode ser enviada para aprovação.
    """
    return SubmissionSnapshot(
        revision.content_sha256, metadata_snapshot(contract), submission_attachments(contract)
    ).fingerprint()
