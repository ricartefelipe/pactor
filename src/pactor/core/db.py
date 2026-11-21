"""Operações de migration para defesas de isolamento no PostgreSQL.

``makemigrations`` não gera estas operações; elas são acrescentadas às
migrations de cada app. Os testes de integração percorrem o catálogo do banco
e falham se alguma tabela de tenant estiver sem RLS forçada ou se alguma
referência entre tabelas de tenant não for composta com ``organization_id``.
"""

from __future__ import annotations

from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.operations.base import Operation
from django.db.migrations.state import ProjectState
from psycopg import sql

TENANT_PREDICATE = "organization_id = NULLIF(current_setting('pactor.tenant_id', true), '')::uuid"
USER_PREDICATE = "{column} = NULLIF(current_setting('pactor.user_id', true), '')::uuid"


def _table(state: ProjectState, app_label: str, model_name: str) -> str:
    return str(state.apps.get_model(app_label, model_name)._meta.db_table)


class EnableTenantRLS(Operation):
    """Habilita e força RLS com a policy de isolamento por organização.

    ``FORCE`` faz a policy valer também para o proprietário da tabela, de modo
    que nem o papel de migrations lê dados de tenant sem contexto definido.
    ``user_column`` permite leitura adicional das linhas do próprio usuário
    (ex.: listar as organizações das quais ele participa), mas escritas
    continuam exigindo o tenant correto.
    """

    reversible = True

    def __init__(self, model_name: str, user_column: str | None = None) -> None:
        self.model_name = model_name
        self.user_column = user_column

    def state_forwards(self, app_label: str, state: ProjectState) -> None:
        """Não altera o estado dos models; atua apenas no banco."""

    def database_forwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        _from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        table = sql.Identifier(_table(to_state, app_label, self.model_name))
        using = TENANT_PREDICATE
        if self.user_column is not None:
            using = f"({TENANT_PREDICATE}) OR ({USER_PREDICATE.format(column=self.user_column)})"
        statements = [
            sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table),
            sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(table),
            sql.SQL("CREATE POLICY tenant_isolation ON {} USING ({}) WITH CHECK ({})").format(
                table, sql.SQL(using), sql.SQL(TENANT_PREDICATE)
            ),
        ]
        with schema_editor.connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    def database_backwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        _to_state: ProjectState,
    ) -> None:
        table = sql.Identifier(_table(from_state, app_label, self.model_name))
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP POLICY IF EXISTS tenant_isolation ON {}").format(table))
            cursor.execute(sql.SQL("ALTER TABLE {} NO FORCE ROW LEVEL SECURITY").format(table))
            cursor.execute(sql.SQL("ALTER TABLE {} DISABLE ROW LEVEL SECURITY").format(table))

    def describe(self) -> str:
        return f"Habilita RLS por organização em {self.model_name}"


class AddTenantForeignKey(Operation):
    """Cria FK composta ``(organization_id, <coluna>)`` para a tabela alvo.

    Impede, no próprio banco, que um registro referencie outro de organização
    diferente, mesmo que o código da aplicação tenha um erro de escopo.
    """

    reversible = True

    def __init__(self, model_name: str, field_name: str, to: str) -> None:
        self.model_name = model_name
        self.field_name = field_name
        self.to = to

    def _names(self, app_label: str, state: ProjectState) -> tuple[str, str, str, str]:
        model = state.apps.get_model(app_label, self.model_name)
        target_app, target_model = self.to.split(".")
        target = state.apps.get_model(target_app, target_model)
        column = model._meta.get_field(self.field_name).column
        table = str(model._meta.db_table)
        constraint = f"{table}_{column}_tenant_fk"[:63]
        return table, str(column), str(target._meta.db_table), constraint

    def state_forwards(self, app_label: str, state: ProjectState) -> None:
        """Não altera o estado dos models; atua apenas no banco."""

    def database_forwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        _from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        table, column, target, constraint = self._names(app_label, to_state)
        statement = sql.SQL(
            "ALTER TABLE {} ADD CONSTRAINT {} FOREIGN KEY (organization_id, {}) "
            "REFERENCES {} (organization_id, id) DEFERRABLE INITIALLY DEFERRED"
        ).format(
            sql.Identifier(table),
            sql.Identifier(constraint),
            sql.Identifier(column),
            sql.Identifier(target),
        )
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(statement)

    def database_backwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        _to_state: ProjectState,
    ) -> None:
        table, _column, _target, constraint = self._names(app_label, from_state)
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("ALTER TABLE {} DROP CONSTRAINT IF EXISTS {}").format(
                    sql.Identifier(table), sql.Identifier(constraint)
                )
            )

    def describe(self) -> str:
        return f"FK composta por organização em {self.model_name}.{self.field_name}"


class MakeAppendOnly(Operation):
    """Impede UPDATE e DELETE na tabela por trigger.

    O descarte por política de retenção define ``pactor.retention_purge`` como
    ``on`` localmente na transação do comando de retenção; nenhum outro
    caminho da aplicação altera essas linhas.
    """

    reversible = True

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def state_forwards(self, app_label: str, state: ProjectState) -> None:
        """Não altera o estado dos models; atua apenas no banco."""

    def database_forwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        _from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        table = _table(to_state, app_label, self.model_name)
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE OR REPLACE FUNCTION pactor_forbid_mutation() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    IF TG_OP = 'DELETE'
                       AND current_setting('pactor.retention_purge', true) = 'on' THEN
                        RETURN OLD;
                    END IF;
                    RAISE EXCEPTION 'registro somente-inserção em %', TG_TABLE_NAME
                        USING ERRCODE = 'insufficient_privilege';
                END;
                $$
                """
            )
            cursor.execute(
                sql.SQL(
                    "CREATE TRIGGER {} BEFORE UPDATE OR DELETE ON {} "
                    "FOR EACH ROW EXECUTE FUNCTION pactor_forbid_mutation()"
                ).format(sql.Identifier(f"{table}_append_only"[:63]), sql.Identifier(table))
            )

    def database_backwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        _to_state: ProjectState,
    ) -> None:
        table = _table(from_state, app_label, self.model_name)
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP TRIGGER IF EXISTS {} ON {}").format(
                    sql.Identifier(f"{table}_append_only"[:63]), sql.Identifier(table)
                )
            )

    def describe(self) -> str:
        return f"Torna {self.model_name} somente-inserção"
