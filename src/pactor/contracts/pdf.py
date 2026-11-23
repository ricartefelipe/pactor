"""Geração de PDF da revisão a partir de estrutura controlada (ReportLab).

O texto é sempre escapado antes de virar parágrafo: nenhum conteúdo do usuário
é interpretado como marcação. ``invariant=1`` torna a saída reprodutível
(sem data de criação nem identificador aleatório), de modo que o mesmo
conteúdo gere os mesmos bytes e o mesmo hash.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer

_STYLES = getSampleStyleSheet()
_BODY = ParagraphStyle("PactorBody", parent=_STYLES["BodyText"], fontSize=10, leading=14)
_SMALL = ParagraphStyle("PactorSmall", parent=_STYLES["BodyText"], fontSize=8, leading=10)


@dataclass(frozen=True, slots=True)
class PdfDocument:
    title: str
    header_lines: Sequence[str]
    body: str
    footer_lines: Sequence[str]


def render_pdf(document: PdfDocument) -> bytes:
    buffer = io.BytesIO()
    template = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=document.title,
        author="Pactor",
        invariant=1,
    )
    story: list[Flowable] = [Paragraph(escape(document.title), _STYLES["Title"])]
    story += [Paragraph(escape(line), _SMALL) for line in document.header_lines]
    story.append(Spacer(1, 6 * mm))
    for paragraph in document.body.split("\n"):
        story.append(Paragraph(escape(paragraph) or "&nbsp;", _BODY))
    story.append(Spacer(1, 8 * mm))
    story += [Paragraph(escape(line), _SMALL) for line in document.footer_lines]
    template.build(story)
    return buffer.getvalue()
