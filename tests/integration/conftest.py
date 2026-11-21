"""Fixtures de integração com PostgreSQL real."""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from django.db import connection


@pytest.fixture
def app_role_connection(transactional_db: None) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """Conexão separada com o papel da aplicação no banco de teste.

    A suíte principal usa o papel proprietário (que precisa criar o banco de
    teste); esta conexão comprova o comportamento efetivo do papel restrito.
    """
    params = connection.get_connection_params()
    conn = psycopg.connect(
        host=params.get("host"),
        port=params.get("port"),
        dbname=params["dbname"],
        user=os.environ.get("PACTOR_DB_APP_ROLE", "pactor_app"),
        password=os.environ.get("PACTOR_APP_PASSWORD", "app-local"),
        autocommit=False,
    )
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
