"""Defesas de isolamento e integridade documental no banco.

Além de RLS forçada e FKs compostas, revisões publicadas tornam-se
somente-inserção: nenhuma atualização ou exclusão é aceita pelo banco.
"""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS, MakeAppendOnly


class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0002_relations"),
        ("counterparties", "0002_tenant_security"),
        ("identity", "0003_tenant_security"),
    ]

    operations = [
        EnableTenantRLS("contractsequence"),
        EnableTenantRLS("contract"),
        EnableTenantRLS("contractdraft"),
        EnableTenantRLS("revision"),
        EnableTenantRLS("comment"),
        EnableTenantRLS("contractgrant"),
        AddTenantForeignKey("contract", "counterparty", to="counterparties.Counterparty"),
        AddTenantForeignKey("contract", "department", to="identity.Department"),
        AddTenantForeignKey("contract", "current_revision", to="contracts.Revision"),
        AddTenantForeignKey("contract", "approved_revision", to="contracts.Revision"),
        AddTenantForeignKey("contract", "parent", to="contracts.Contract"),
        AddTenantForeignKey("contractdraft", "contract", to="contracts.Contract"),
        AddTenantForeignKey("revision", "contract", to="contracts.Contract"),
        AddTenantForeignKey("revision", "previous", to="contracts.Revision"),
        AddTenantForeignKey("comment", "revision", to="contracts.Revision"),
        AddTenantForeignKey("contractgrant", "contract", to="contracts.Contract"),
        AddTenantForeignKey("contractgrant", "team", to="identity.Team"),
        MakeAppendOnly("revision"),
    ]
