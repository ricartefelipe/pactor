"""RLS forçada e FKs compostas; registros de renovação são somente-inserção."""

from django.db import migrations

from pactor.core.db import AddTenantForeignKey, EnableTenantRLS, MakeAppendOnly


class Migration(migrations.Migration):
    dependencies = [("deadlines", "0001_initial"), ("contracts", "0006_lifecycle_constraints")]

    operations = [
        EnableTenantRLS("holidaycalendar"),
        EnableTenantRLS("holiday"),
        EnableTenantRLS("contractdeadline"),
        EnableTenantRLS("renewalpolicy"),
        EnableTenantRLS("renewalrecord"),
        EnableTenantRLS("alert"),
        AddTenantForeignKey("holiday", "calendar", to="deadlines.HolidayCalendar"),
        AddTenantForeignKey("contractdeadline", "contract", to="contracts.Contract"),
        AddTenantForeignKey("contractdeadline", "holiday_calendar", to="deadlines.HolidayCalendar"),
        AddTenantForeignKey("renewalpolicy", "contract", to="contracts.Contract"),
        AddTenantForeignKey("renewalrecord", "contract", to="contracts.Contract"),
        AddTenantForeignKey("alert", "contract", to="contracts.Contract"),
        MakeAppendOnly("renewalrecord"),
    ]
