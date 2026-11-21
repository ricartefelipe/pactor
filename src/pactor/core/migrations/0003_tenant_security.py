"""Defesas de isolamento no banco: RLS forçada e FKs compostas por organização."""

from django.db import migrations

from pactor.core.db import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("core", "0002_outbox_idempotency")]

    operations = [
        EnableTenantRLS("idempotencyrecord"),
    ]
