"""RLS forçada e FKs compostas para arquivos e anexos."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("files", "0001_initial"), ("contracts", "0006_lifecycle_constraints")]

    operations = [
        EnableTenantRLS("storedfile"),
        EnableTenantRLS("contractattachment"),
        AddTenantForeignKey("contractattachment", "contract", to="contracts.Contract"),
        AddTenantForeignKey("contractattachment", "file", to="files.StoredFile"),
    ]
