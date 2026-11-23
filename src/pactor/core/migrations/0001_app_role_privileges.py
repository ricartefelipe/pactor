"""Concede ao papel da aplicação apenas DML nas tabelas criadas por migrations.

Executada antes de qualquer outra migration para que os privilégios padrão
valham para todas as tabelas seguintes. O papel da aplicação não recebe
propriedade, DDL, superuser nem BYPASSRLS.
"""

from django.apps.registry import Apps
from django.conf import settings
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from psycopg import sql


def grant_app_privileges(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    role = settings.PACTOR_DB_APP_ROLE
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
        if cursor.fetchone() is None:
            raise RuntimeError(
                f"Papel de aplicação {role!r} não existe; crie-o antes de migrar "
                "(ver docker/postgres/10-roles.sh)."
            )
        ident = sql.Identifier(role)
        statements = [
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}"),
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ),
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}"
            ),
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"),
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"),
        ]
        for statement in statements:
            cursor.execute(statement.format(ident))


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []
    run_before = [("contenttypes", "0001_initial"), ("auth", "0001_initial")]

    operations = [migrations.RunPython(grant_app_privileges, migrations.RunPython.noop)]
