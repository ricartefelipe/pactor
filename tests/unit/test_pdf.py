"""PDF gerado a partir de estrutura controlada."""

from __future__ import annotations

from pactor.contracts.pdf import PdfDocument, render_pdf

HOSTILE = PdfDocument(
    title="Contrato <b>teste</b> & cia",
    header_lines=["<font size=90>grande</font>"],
    body='Cláusula 1. <img src="file:///etc/passwd"/>\n\nCláusula 2. <a href="http://x">link</a>',
    footer_lines=["SHA-256: abc"],
)


def test_user_markup_is_escaped_not_interpreted() -> None:
    content = render_pdf(HOSTILE)
    assert content.startswith(b"%PDF")
    # Marcação do usuário não vira link nem imagem no PDF.
    assert b"/URI" not in content
    assert b"/Subtype /Image" not in content


def test_output_is_reproducible_for_same_content() -> None:
    assert render_pdf(HOSTILE) == render_pdf(HOSTILE)
    changed = PdfDocument(
        HOSTILE.title, HOSTILE.header_lines, HOSTILE.body + "!", HOSTILE.footer_lines
    )
    assert render_pdf(changed) != render_pdf(HOSTILE)
