"""Integração real com a conta de demonstração Docusign.

Executado somente por ``make test-external`` com as variáveis DOCUSIGN_*
preenchidas. Sem credenciais o teste falha explicitamente: a ausência de
execução é registrada em docs/STATUS.md, nunca convertida em sucesso.
"""

from __future__ import annotations

import hashlib
import uuid

from pactor.contracts.pdf import PdfDocument, render_pdf
from pactor.signatures.docusign import DocusignProvider
from pactor.signatures.ports import EnvelopeRequest, EnvelopeStatus, Signer
from pactor.signatures.providers import docusign_settings


def test_create_find_by_reference_and_void_in_demo_account() -> None:
    provider = DocusignProvider(docusign_settings())
    document = render_pdf(
        PdfDocument(
            title="Pactor — teste de integração",
            header_lines=["Documento de teste"],
            body="Teste.",
            footer_lines=[],
        )
    )
    reference = f"pactor-teste-{uuid.uuid4()}"
    request = EnvelopeRequest(
        reference=reference,
        subject="Pactor: teste de integração (descartável)",
        document_name="teste.pdf",
        document=document,
        document_sha256=hashlib.sha256(document).hexdigest(),
        signers=[Signer("1", "Signatário de Teste", "signatario.teste@example.com", 1)],
    )
    handle = provider.create_envelope(request)
    assert handle.status is EnvelopeStatus.SENT
    found = provider.find_by_reference(reference)
    assert found is not None
    assert found.provider_envelope_id == handle.provider_envelope_id
    provider.void(handle.provider_envelope_id, "Teste de integração concluído")
    assert provider.get_state(handle.provider_envelope_id).status is EnvelopeStatus.VOIDED
