"""Usuário de serviço por integração, autor dos registros que ela cria.

Clientes existentes recebem o usuário organização por organização: com RLS
forçada, a tabela só mostra as linhas do tenant vinculado à transação.
"""

import uuid

import django.db.models.deletion
from django.apps.registry import Apps
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def create_service_users(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    organization_model = apps.get_model("identity", "Organization")
    client_model = apps.get_model("integrations", "IntegrationClient")
    user_model = apps.get_model("identity", "User")
    with schema_editor.connection.cursor() as cursor:
        for organization_id in organization_model.objects.values_list("id", flat=True):
            cursor.execute(
                "SELECT set_config('pactor.tenant_id', %s, true)", [str(organization_id)]
            )
            for client in client_model.objects.filter(service_user__isnull=True):
                user = user_model.objects.create(
                    id=uuid.uuid4(),
                    email=f"integracao.{client.id}@integracoes.invalid",
                    full_name=f"{client.name} (integração)",
                    password=make_password(None),
                    is_active=False,
                )
                client.service_user_id = user.id
                client.save(update_fields=["service_user"])
        cursor.execute("SELECT set_config('pactor.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0002_tenant_security"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="integrationclient",
            name="service_user",
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="integration_client",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(create_service_users, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="integrationclient",
            name="service_user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="integration_client",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
