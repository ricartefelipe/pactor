"""RLS forçada e FKs compostas; histórico de solicitações é somente-inserção."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS, MakeAppendOnly


class Migration(migrations.Migration):
    dependencies = [
        ("intake", "0001_initial"),
        ("contracts", "0003_tenant_security"),
        ("counterparties", "0002_tenant_security"),
    ]

    operations = [
        EnableTenantRLS("contractrequest"),
        EnableTenantRLS("requestevent"),
        EnableTenantRLS("requestsequence"),
        AddTenantForeignKey("contractrequest", "department", to="identity.Department"),
        AddTenantForeignKey("contractrequest", "counterparty", to="counterparties.Counterparty"),
        AddTenantForeignKey("contractrequest", "contract", to="contracts.Contract"),
        AddTenantForeignKey("requestevent", "request", to="intake.ContractRequest"),
        MakeAppendOnly("requestevent"),
    ]
