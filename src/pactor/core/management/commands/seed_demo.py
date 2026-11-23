"""Carrega as duas organizações fictícias de demonstração (idempotente)."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from pactor import demo


class Command(BaseCommand):
    help = "Cria ou completa os dados fictícios de demonstração. Recusado em produção."

    def handle(self, *args: object, **options: object) -> None:
        try:
            organizations = demo.seed()
        except demo.DemoNotAllowedError as exc:
            raise CommandError(str(exc)) from exc
        for organization in organizations:
            self.stdout.write(f"{organization.name} ({organization.slug}) pronta.")
        self.stdout.write(
            f"Usuários: <papel>.<a|b>@{demo.DEMO_DOMAIN}; senha e TOTP em docs/DEMONSTRACAO.md."
        )
