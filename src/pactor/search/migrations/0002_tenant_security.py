"""RLS forçada e FK composta para documentos de pesquisa."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("search", "0001_initial"), ("contracts", "0006_lifecycle_constraints")]

    operations = [
        EnableTenantRLS("searchdocument"),
        AddTenantForeignKey("searchdocument", "contract", to="contracts.Contract"),
    ]
