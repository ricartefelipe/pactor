"""Defesas de isolamento no banco para envelopes, participantes e documentos.

Tabelas globais (sem RLS): roteamento de webhooks, inbox de eventos e estado
do simulador; contêm apenas identificadores e situações do provedor.
"""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS, MakeAppendOnly


class Migration(migrations.Migration):
    dependencies = [("signatures", "0001_initial"), ("contracts", "0003_tenant_security")]

    operations = [
        EnableTenantRLS("envelope"),
        EnableTenantRLS("recipient"),
        EnableTenantRLS("envelopedocument"),
        AddTenantForeignKey("envelope", "contract", to="contracts.Contract"),
        AddTenantForeignKey("envelope", "revision", to="contracts.Revision"),
        AddTenantForeignKey("recipient", "envelope", to="signatures.Envelope"),
        AddTenantForeignKey("envelopedocument", "envelope", to="signatures.Envelope"),
        MakeAppendOnly("envelopedocument"),
    ]
