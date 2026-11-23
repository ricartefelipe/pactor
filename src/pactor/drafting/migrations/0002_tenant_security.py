"""RLS forçada e FKs compostas; versões publicadas de modelos são somente-inserção."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS, MakeAppendOnly


class Migration(migrations.Migration):
    dependencies = [("drafting", "0001_initial")]

    operations = [
        EnableTenantRLS("contracttemplate"),
        EnableTenantRLS("templateversion"),
        AddTenantForeignKey("templateversion", "template", to="drafting.ContractTemplate"),
        MakeAppendOnly("templateversion"),
    ]
