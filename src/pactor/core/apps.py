from importlib import import_module

from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "pactor.core"
    label = "core"
    verbose_name = "Núcleo"

    def ready(self) -> None:
        # Registra a descrição OpenAPI da autenticação de integrações (efeito do
        # import: a extensão se registra ao ser definida).
        import_module("pactor.access.schema")
