"""Indexação de contratos para pesquisa.

Fontes: referência e título (peso A), contraparte, documento, etiquetas e
classificação (peso B), conteúdo da revisão e texto extraído de anexos
liberados (peso C). Arquivos em quarentena ou rejeitados nunca entram.
"""

from __future__ import annotations

import uuid

from django.db import connection

from pactor.contracts.models import Contract, Revision
from pactor.files.models import ContractAttachment, FileStatus

MAX_BODY_CHARS = 1_000_000

_UPSERT = """
INSERT INTO search_searchdocument
    (id, organization_id, created_at, contract_id, revision_number, body, vector, updated_at)
VALUES (
    %(id)s, %(organization_id)s, now(), %(contract_id)s, %(revision_number)s, %(body)s,
    setweight(to_tsvector('portuguese', %(a)s), 'A')
    || setweight(to_tsvector('portuguese', %(b)s), 'B')
    || setweight(to_tsvector('portuguese', %(c)s), 'C'),
    now()
)
ON CONFLICT (contract_id) DO UPDATE
SET revision_number = EXCLUDED.revision_number,
    body = EXCLUDED.body,
    vector = EXCLUDED.vector,
    updated_at = EXCLUDED.updated_at
WHERE search_searchdocument.revision_number <= EXCLUDED.revision_number
RETURNING id
"""


def index_contract(contract_id: uuid.UUID, *, revision_number: int) -> bool:
    """Indexa o contrato com o conteúdo da revisão indicada. Devolve se gravou.

    Executar com transação vinculada ao tenant do contrato.
    """
    contract = Contract.objects.select_related("counterparty").filter(id=contract_id).first()
    if contract is None:
        return False
    revision = (
        Revision.objects.filter(contract=contract, number=revision_number).first()
        if revision_number
        else None
    )
    if revision_number and revision is None:
        return False
    attachments = ContractAttachment.objects.filter(
        contract=contract, removed_at__isnull=True, file__status=FileStatus.RELEASED
    ).values_list("file__extracted_text", flat=True)
    primary = f"{contract.reference} {contract.title}"
    secondary = " ".join(
        [
            contract.counterparty.name,
            contract.counterparty.trade_name,
            contract.counterparty.document,
            " ".join(contract.tags),
            contract.classification,
        ]
    )
    content = "\n".join([revision.content if revision else "", *attachments])[:MAX_BODY_CHARS]
    with connection.cursor() as cursor:
        cursor.execute(
            _UPSERT,
            {
                "id": uuid.uuid4(),
                "organization_id": contract.organization_id,
                "contract_id": contract.id,
                "revision_number": revision_number,
                "body": content,
                "a": primary,
                "b": secondary,
                "c": content,
            },
        )
        return cursor.fetchone() is not None
