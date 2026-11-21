"""Django Admin restrito a dados globais da plataforma.

Tabelas de tenant não são registradas: sem contexto de organização, o RLS
as esconde, e o suporte operacional não deve navegar por dados de clientes.
O Admin não substitui a interface do produto.
"""

from __future__ import annotations

from typing import ClassVar

from django.contrib import admin

from pactor.identity.models import Organization, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin[User]):
    list_display = ("email", "full_name", "is_active", "is_staff", "date_joined")
    search_fields = ("email", "full_name")
    readonly_fields = ("id", "date_joined", "last_login", "password", "session_generation")
    fields = (
        "id",
        "email",
        "full_name",
        "is_active",
        "is_staff",
        "date_joined",
        "last_login",
        "password",
        "session_generation",
    )
    ordering: ClassVar[list[str]] = ["email"]


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin[Organization]):
    list_display = ("name", "slug", "status", "created_at")
    search_fields = ("name", "slug")
    readonly_fields = ("id", "created_at")
