from django.contrib import admin

from pactor.plans.models import Plan


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin[Plan]):
    list_display = (
        "code",
        "name",
        "monthly_price",
        "max_users",
        "max_active_contracts",
        "is_public",
    )
