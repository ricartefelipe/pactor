#!/bin/sh
# Cria os papéis do Pactor na inicialização do volume do PostgreSQL.
#  - pactor_owner: proprietário do banco e das tabelas; usado só por migrations.
#  - pactor_app: papel da aplicação; sem superuser, sem BYPASSRLS, sem posse.
set -eu

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname postgres \
  -v owner_password="$PACTOR_OWNER_PASSWORD" \
  -v app_password="$PACTOR_APP_PASSWORD" <<'SQL'
CREATE ROLE pactor_owner LOGIN CREATEDB NOSUPERUSER NOBYPASSRLS NOCREATEROLE PASSWORD :'owner_password';
CREATE ROLE pactor_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
CREATE DATABASE pactor OWNER pactor_owner;
SQL
