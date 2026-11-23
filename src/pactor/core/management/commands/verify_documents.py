"""Confere os documentos armazenados contra os hashes do banco (somente leitura)."""

from __future__ import annotations

import uuid
from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError

from pactor.integrity import verify_documents


class Command(BaseCommand):
    help = (
        "Relê arquivos, prévias e documentos de assinatura e compara SHA-256 e tamanho com o "
        "banco. Termina com erro se houver divergência."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--organizacao",
            action="append",
            type=uuid.UUID,
            dest="organizations",
            help="Restringe a uma organização (pode repetir).",
        )

    def handle(self, *args: object, **options: object) -> None:
        selected = options.get("organizations")
        report = verify_documents(selected if isinstance(selected, list) else None)
        for finding in report.findings:
            self.stderr.write(
                f"{finding.problem}: {finding.source} {finding.record_id} "
                f"(organização {finding.organization_id}) em {finding.path}"
            )
        summary = f"{report.checked} documentos conferidos, {len(report.findings)} divergências."
        if not report.ok:
            raise CommandError(summary)
        self.stdout.write(summary)
