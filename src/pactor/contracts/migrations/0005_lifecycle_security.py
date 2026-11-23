"""RLS forçada e FKs compostas para vigência e aditivos; histórico somente-inserção."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS, MakeAppendOnly


class Migration(migrations.Migration):
    dependencies = [("contracts", "0004_lifecycle")]

    operations = [
        EnableTenantRLS("lifecyclepolicy"),
        EnableTenantRLS("lifecycleevent"),
        EnableTenantRLS("amendmentchange"),
        AddTenantForeignKey("lifecycleevent", "contract", to="contracts.Contract"),
        AddTenantForeignKey("amendmentchange", "amendment", to="contracts.Contract"),
        AddTenantForeignKey("amendmentchange", "parent", to="contracts.Contract"),
        MakeAppendOnly("lifecycleevent"),
    ]
