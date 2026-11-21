"""Isolamento entre organizações no banco (RLS, FKs compostas, contexto local)."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from django.apps import apps
from django.db import connection, transaction

from pactor.core.errors import TenantContextMissingError
from pactor.core.models import TenantModel
from pactor.core.tenancy import (
    TenantContextConflictError,
    bind_tenant,
    current_tenant,
    require_tenant,
    tenant_context,
)
from pactor.identity.models import Department, DepartmentMembership, Membership
from tests import factories

pytestmark = pytest.mark.django_db(transaction=True)


def _tenant_models() -> list[type[TenantModel]]:
    return [m for m in apps.get_models() if issubclass(m, TenantModel)]


def test_every_tenant_table_has_forced_rls_with_policy() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid "
            "AND p.polname = 'tenant_isolation') "
            "FROM pg_class c WHERE c.relkind = 'r'"
        )
        catalog = {row[0]: row[1:] for row in cursor.fetchall()}
    missing = [
        m._meta.db_table
        for m in _tenant_models()
        if catalog.get(m._meta.db_table) != (True, True, True)
    ]
    assert missing == []


def test_every_reference_between_tenant_tables_is_composite() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conrelid::regclass::text, "
            "(SELECT array_agg(a.attname ORDER BY k.ord) "
            " FROM unnest(con.conkey) WITH ORDINALITY k(num, ord) "
            " JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.num) "
            "FROM pg_constraint con WHERE contype = 'f'"
        )
        composite = {(table, tuple(cols)) for table, cols in cursor.fetchall()}
    missing = []
    for model in _tenant_models():
        for field in model._meta.concrete_fields:
            target = getattr(field, "related_model", None)
            if (
                field.name == "organization"
                or target is None
                or not issubclass(target, TenantModel)
            ):
                continue
            if (model._meta.db_table, ("organization_id", field.column)) not in composite:
                missing.append(f"{model._meta.db_table}.{field.column}")
    assert missing == []


def test_no_context_sees_nothing_and_context_sees_only_own_rows() -> None:
    org_a, _ = factories.organization("A")
    org_b, _ = factories.organization("B")
    factories.department(org_a, "Jurídico A")
    factories.department(org_b, "Jurídico B")
    assert Department.objects.count() == 0
    with tenant_context(org_a.id):
        assert list(Department.objects.values_list("name", flat=True)) == ["Jurídico A"]
    with tenant_context(org_b.id):
        assert list(Department.objects.values_list("name", flat=True)) == ["Jurídico B"]


def test_write_for_another_organization_is_rejected_by_policy() -> None:
    org_a, _ = factories.organization("A")
    org_b, _ = factories.organization("B")
    with pytest.raises(Exception, match="row-level security"), tenant_context(org_a.id):
        Department.objects.create(organization=org_b, name="Intruso")


def test_cross_tenant_reference_is_rejected_by_composite_fk() -> None:
    org_a, owner_a = factories.organization("A")
    org_b, _ = factories.organization("B")
    dept_b = factories.department(org_b, "Depto B")
    with pytest.raises(Exception, match="tenant_fk"), tenant_context(org_a.id):
        membership = Membership.objects.get(user=owner_a)
        # O RLS de org A permite gravar a linha (organization_id = A), mas a
        # FK composta impede apontar para o departamento de B.
        DepartmentMembership.objects.create(
            organization=org_a, membership=membership, department_id=dept_b.id
        )
        connection.check_constraints()


def test_context_is_local_to_transaction_and_connection_is_clean_after() -> None:
    org, _ = factories.organization()
    with tenant_context(org.id):
        assert current_tenant() == org.id
    assert current_tenant() is None
    with pytest.raises(TenantContextMissingError):
        require_tenant()


def test_context_is_cleared_after_rollback() -> None:
    org, _ = factories.organization()
    with pytest.raises(RuntimeError), tenant_context(org.id):
        raise RuntimeError
    assert current_tenant() is None


def test_switching_tenant_inside_bound_transaction_is_rejected() -> None:
    org_a, _ = factories.organization()
    org_b, _ = factories.organization()
    with tenant_context(org_a.id), pytest.raises(TenantContextConflictError):
        bind_tenant(org_b.id)


def test_sequential_contexts_in_outer_transaction_do_not_leak() -> None:
    org_a, _ = factories.organization()
    org_b, _ = factories.organization()
    factories.department(org_a, "Só de A")
    with transaction.atomic():
        with tenant_context(org_a.id):
            assert Department.objects.count() == 1
        assert current_tenant() is None
        with tenant_context(org_b.id):
            assert Department.objects.count() == 0


def test_binding_outside_transaction_is_refused() -> None:
    with pytest.raises(TenantContextConflictError):
        bind_tenant(uuid.uuid4())


def test_app_role_without_context_reads_no_tenant_rows(
    app_role_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    org, _ = factories.organization()
    factories.department(org, "Visível só com contexto")
    cur = app_role_connection.cursor()
    assert cur.execute("SELECT count(*) FROM identity_department").fetchone() == (0,)
    cur.execute("SELECT set_config('pactor.tenant_id', %s, true)", [str(org.id)])
    assert cur.execute("SELECT count(*) FROM identity_department").fetchone() == (1,)
    app_role_connection.commit()
    # Mesma conexão reutilizada: o parâmetro local expirou com a transação.
    assert cur.execute("SELECT count(*) FROM identity_department").fetchone() == (0,)
    assert cur.execute("SELECT current_setting('pactor.tenant_id', true)").fetchone() == ("",)


def test_app_role_cannot_bypass_rls_by_disabling_it(
    app_role_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_role_connection.execute("ALTER TABLE identity_department DISABLE ROW LEVEL SECURITY")
