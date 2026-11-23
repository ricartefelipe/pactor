"""Defesas de isolamento no banco: RLS forçada e FKs compostas por organização."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("counterparties", "0001_initial")]

    operations = [
        EnableTenantRLS("counterparty"),
        EnableTenantRLS("contact"),
        EnableTenantRLS("address"),
        EnableTenantRLS("representative"),
        AddTenantForeignKey("contact", "counterparty", to="counterparties.Counterparty"),
        AddTenantForeignKey("address", "counterparty", to="counterparties.Counterparty"),
        AddTenantForeignKey("representative", "counterparty", to="counterparties.Counterparty"),
    ]
