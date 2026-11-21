"""Defesas de isolamento no banco: RLS forçada e FKs compostas por organização."""

from django.db import migrations

from pactor.core.db import EnableTenantRLS, MakeAppendOnly


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [
        EnableTenantRLS("auditevent"),
        EnableTenantRLS("retentionhold"),
        MakeAppendOnly("auditevent"),
    ]
