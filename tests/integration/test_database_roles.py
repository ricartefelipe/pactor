"""O papel da aplicação deve ter somente DML, sem DDL ou desvio de RLS."""

import psycopg
import pytest


def test_app_role_is_not_privileged(
    app_role_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    row = app_role_connection.execute(
        "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles "
        "WHERE rolname = current_user",
    ).fetchone()
    assert row == (False, False, False, False)


def test_app_role_does_not_own_tables(
    app_role_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    row = app_role_connection.execute(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user",
    ).fetchone()
    assert row == (0,)


def test_app_role_has_dml_on_application_tables(
    app_role_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    row = app_role_connection.execute("SELECT count(*) FROM identity_user").fetchone()
    assert row is not None


def test_app_role_cannot_run_ddl(
    app_role_connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_role_connection.execute("DROP TABLE identity_user")
