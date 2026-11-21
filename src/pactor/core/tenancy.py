"""Contexto de tenant na transação do PostgreSQL.

O isolamento entre organizações depende de Row-Level Security: as policies
comparam ``organization_id`` com o parâmetro ``pactor.tenant_id`` da sessão do
banco. O parâmetro é sempre definido com ``set_config(..., is_local => true)``,
portanto vale apenas até o fim da transação corrente. Ao reutilizar uma conexão
(requisição seguinte, próxima task do worker), não resta tenant definido e as
consultas a tabelas protegidas retornam zero linhas.

O contexto de tenant nunca é guardado em variáveis do processo: a fonte de
verdade é a própria transação, consultada com :func:`current_tenant`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection, transaction

from pactor.core.errors import TenantContextMissingError
from pactor.core.observability import bind_log_context

TENANT_SETTING = "pactor.tenant_id"
USER_SETTING = "pactor.user_id"


class TenantContextConflictError(RuntimeError):
    """Tentativa de trocar de tenant dentro de uma transação já vinculada.

    Misturar organizações na mesma transação tornaria ambíguo qual policy de
    RLS protege cada escrita; trata-se de erro de programação, não de negócio.
    """


def _read_setting(name: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [name])
        row = cursor.fetchone()
    value = row[0] if row else None
    return value or ""


def _set_local(name: str, value: str) -> None:
    if not connection.in_atomic_block:
        # Fora de uma transação explícita o set_config local expiraria ao fim
        # da própria instrução e daria falsa impressão de contexto definido.
        raise TenantContextConflictError("set_config local exige transação ativa.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [name, value])


def current_tenant() -> uuid.UUID | None:
    raw = _read_setting(TENANT_SETTING)
    return uuid.UUID(raw) if raw else None


def require_tenant() -> uuid.UUID:
    tenant = current_tenant()
    if tenant is None:
        raise TenantContextMissingError("Nenhuma organização selecionada para esta operação.")
    return tenant


def current_user_setting() -> uuid.UUID | None:
    raw = _read_setting(USER_SETTING)
    return uuid.UUID(raw) if raw else None


def bind_user(user_id: uuid.UUID) -> None:
    """Identifica o usuário na transação corrente (usado por policies de associação)."""
    existing = current_user_setting()
    if existing is not None and existing != user_id:
        raise TenantContextConflictError("Transação já vinculada a outro usuário.")
    _set_local(USER_SETTING, str(user_id))


def bind_tenant(organization_id: uuid.UUID) -> None:
    """Vincula a transação corrente a uma organização.

    Pode ser chamada novamente com a mesma organização (operações aninhadas);
    uma organização diferente é rejeitada.
    """
    existing = current_tenant()
    if existing is not None and existing != organization_id:
        raise TenantContextConflictError("Transação já vinculada a outra organização.")
    _set_local(TENANT_SETTING, str(organization_id))


@contextmanager
def tenant_context(
    organization_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
) -> Iterator[None]:
    """Executa o bloco em transação vinculada à organização informada.

    No bloco mais externo, o vínculo termina junto com a transação. Quando
    aninhado em uma transação já existente sem tenant (ex.: tarefas que
    percorrem várias organizações), o vínculo é desfeito ao sair do bloco,
    para que a organização seguinte comece sem contexto herdado.
    """
    with transaction.atomic():
        previous_tenant = current_tenant()
        previous_user = current_user_setting()
        bind_tenant(organization_id)
        if user_id is not None:
            bind_user(user_id)
        with bind_log_context(organization_id=str(organization_id)):
            yield
        # Em caso de exceção, o rollback do savepoint (ou da transação) já
        # desfaz os parâmetros; aqui só se trata a saída normal.
        if previous_tenant is None:
            _set_local(TENANT_SETTING, "")
        if previous_user is None and user_id is not None:
            _set_local(USER_SETTING, "")
