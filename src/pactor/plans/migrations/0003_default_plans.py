"""Planos iniciais. Preços e limites são hipóteses de produto, ajustáveis."""

from decimal import Decimal

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

ALL_MODULES = ["approvals", "signatures", "obligations", "finance", "search", "integrations"]

PLANS = [
    {
        "code": "essencial",
        "name": "Essencial",
        "monthly_price": Decimal("490.00"),
        "max_users": 5,
        "max_active_contracts": 100,
        "max_storage_bytes": 5 * 1024**3,
        "modules": ["approvals", "obligations", "search"],
    },
    {
        "code": "profissional",
        "name": "Profissional",
        "monthly_price": Decimal("1490.00"),
        "max_users": 25,
        "max_active_contracts": 1000,
        "max_storage_bytes": 50 * 1024**3,
        "modules": ALL_MODULES,
    },
    {
        "code": "corporativo",
        "name": "Corporativo",
        "monthly_price": Decimal("4900.00"),
        "max_users": 200,
        "max_active_contracts": 20000,
        "max_storage_bytes": 500 * 1024**3,
        "modules": ALL_MODULES,
    },
]


def create_plans(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    plan_model = apps.get_model("plans", "Plan")
    for plan in PLANS:
        plan_model.objects.update_or_create(code=plan["code"], defaults=plan)


class Migration(migrations.Migration):
    dependencies = [("plans", "0002_tenant_security")]

    operations = [migrations.RunPython(create_plans, migrations.RunPython.noop)]
