"""RLS forçada e FKs compostas para obrigações e ocorrências."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("obligations", "0001_initial"), ("files", "0002_tenant_security")]

    operations = [
        EnableTenantRLS("obligation"),
        EnableTenantRLS("obligationoccurrence"),
        AddTenantForeignKey("obligation", "contract", to="contracts.Contract"),
        AddTenantForeignKey("obligation", "depends_on", to="obligations.Obligation"),
        AddTenantForeignKey("obligationoccurrence", "obligation", to="obligations.Obligation"),
        AddTenantForeignKey("obligationoccurrence", "evidence", to="files.StoredFile"),
    ]
