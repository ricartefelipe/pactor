"""Defesas de isolamento no banco: RLS forçada e FKs compostas por organização."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("identity", "0002_organizations_memberships")]

    operations = [
        EnableTenantRLS("department"),
        EnableTenantRLS("team"),
        EnableTenantRLS("membership", user_column="user_id"),
        EnableTenantRLS("departmentmembership"),
        EnableTenantRLS("teammembership"),
        EnableTenantRLS("invitation"),
        AddTenantForeignKey("departmentmembership", "membership", to="identity.Membership"),
        AddTenantForeignKey("departmentmembership", "department", to="identity.Department"),
        AddTenantForeignKey("teammembership", "membership", to="identity.Membership"),
        AddTenantForeignKey("teammembership", "team", to="identity.Team"),
    ]
