"""RLS forçada e FKs compostas; confirmações, índices e reajustes são somente-inserção."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS, MakeAppendOnly


class Migration(migrations.Migration):
    dependencies = [("finance", "0001_initial"), ("contracts", "0006_lifecycle_constraints")]

    operations = [
        EnableTenantRLS("indexseries"),
        EnableTenantRLS("indexvalue"),
        EnableTenantRLS("installment"),
        EnableTenantRLS("paymentconfirmation"),
        EnableTenantRLS("adjustmentrecord"),
        AddTenantForeignKey("indexvalue", "series", to="finance.IndexSeries"),
        AddTenantForeignKey("installment", "contract", to="contracts.Contract"),
        AddTenantForeignKey("paymentconfirmation", "installment", to="finance.Installment"),
        AddTenantForeignKey("adjustmentrecord", "contract", to="contracts.Contract"),
        AddTenantForeignKey("adjustmentrecord", "index_value", to="finance.IndexValue"),
        MakeAppendOnly("paymentconfirmation"),
        MakeAppendOnly("indexvalue"),
        MakeAppendOnly("adjustmentrecord"),
    ]
