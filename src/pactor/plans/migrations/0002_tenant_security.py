"""Defesas de isolamento no banco: RLS forçada e FKs compostas por organização."""

from django.db import migrations

from pactor.core.db import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("plans", "0001_initial")]

    operations = [
        EnableTenantRLS("subscription"),
    ]
