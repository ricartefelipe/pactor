#!/usr/bin/env bash
# Exercício de recuperação a um ponto no tempo (PITR) em contêineres descartáveis.
#
# Uso: scripts/pitr-drill.sh <backup.dump>
#   <backup.dump>: saída de pg_dump --format=custom (ver docs/RUNBOOKS.md, seção 2).
#
# Passos: cluster com arquivamento de WAL → carga do backup → backup base
# (pg_basebackup) → alteração acidental em massa depois de um instante T →
# recuperação até T a partir do backup base e dos WAL arquivados → conferência.
# Nada do ambiente local é alterado; contêineres e volumes são removidos ao fim.
set -euo pipefail

DUMP="${1:?informe o arquivo de backup (pg_dump --format=custom)}"
[[ -r "$DUMP" ]] || { echo "backup não encontrado: $DUMP" >&2; exit 2; }
IMAGE="postgres:17.12-alpine3.24"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="pactor-pitr-$$"
PRIMARY="$RUN-primario"
RESTORED="$RUN-restaurado"

cleanup() {
  docker rm -f "$PRIMARY" "$RESTORED" >/dev/null 2>&1 || true
  docker volume rm "$RUN-dados" "$RUN-wal" "$RUN-base" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_ready() {
  for _ in $(seq 1 60); do
    if docker exec "$1" pg_isready -U postgres -q 2>/dev/null; then return 0; fi
    sleep 1
  done
  docker logs "$1" >&2
  return 1
}

sql() { docker exec -i "$1" psql -U postgres -d pactor -At -v ON_ERROR_STOP=1 -c "$2"; }

docker volume create "$RUN-dados" >/dev/null
docker volume create "$RUN-wal" >/dev/null
docker volume create "$RUN-base" >/dev/null
docker run --rm -v "$RUN-wal:/wal" -v "$RUN-base:/base" "$IMAGE" \
  chown postgres:postgres /wal /base

echo "1. Cluster com arquivamento de WAL"
docker run -d --name "$PRIMARY" \
  -e POSTGRES_PASSWORD=exercicio -e PACTOR_OWNER_PASSWORD=exercicio \
  -e PACTOR_APP_PASSWORD=exercicio -e PACTOR_BACKUP_PASSWORD=exercicio \
  -v "$RUN-dados:/var/lib/postgresql/data" -v "$RUN-wal:/wal" -v "$RUN-base:/base" \
  -v "$ROOT/docker/postgres/10-roles.sh:/docker-entrypoint-initdb.d/10-roles.sh:ro" \
  "$IMAGE" -c wal_level=replica -c archive_mode=on \
  -c "archive_command=test ! -f /wal/%f && cp %p /wal/%f" >/dev/null
wait_ready "$PRIMARY"

echo "2. Carga do backup"
docker exec -i "$PRIMARY" pg_restore -U postgres -d pactor --exit-on-error < "$DUMP"
CONTRACTS="$(sql "$PRIMARY" "SELECT count(*) FROM contracts_contract")"
TITLES="$(sql "$PRIMARY" "SELECT md5(string_agg(title, '|' ORDER BY id)) FROM contracts_contract")"
echo "   contratos: $CONTRACTS"

echo "3. Backup base"
docker exec -u postgres "$PRIMARY" pg_basebackup -U postgres -D /base/dados -Fp -Xs -c fast

echo "4. Alteração acidental depois do instante T"
sql "$PRIMARY" "CREATE TABLE exercicio_pitr (marca text); INSERT INTO exercicio_pitr VALUES ('antes de T')" >/dev/null
sleep 1
TARGET="$(sql "$PRIMARY" "SELECT now()")"
sleep 1
sql "$PRIMARY" "UPDATE contracts_contract SET title = 'ALTERADO POR ENGANO'; INSERT INTO exercicio_pitr VALUES ('depois de T')" >/dev/null
sql "$PRIMARY" "SELECT pg_switch_wal()" >/dev/null
for _ in $(seq 1 30); do
  pending="$(sql "$PRIMARY" "SELECT count(*) FROM pg_ls_dir('pg_wal/archive_status') f WHERE f LIKE '%.ready'")"
  [[ "$pending" == "0" ]] && break
  sleep 1
done
echo "   T = $TARGET"
docker stop "$PRIMARY" >/dev/null

echo "5. Recuperação até T (backup base + WAL arquivados)"
docker run --rm -u postgres -v "$RUN-base:/base" "$IMAGE" touch /base/dados/recovery.signal
docker run -d --name "$RESTORED" -e PGDATA=/base/dados \
  -v "$RUN-base:/base" -v "$RUN-wal:/wal:ro" \
  "$IMAGE" -c "restore_command=cp /wal/%f %p" -c "recovery_target_time=$TARGET" \
  -c recovery_target_action=promote >/dev/null
for _ in $(seq 1 60); do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$RESTORED")" != "true" ]]; then
    echo "   FALHOU: o PostgreSQL encerrou durante a recuperação:" >&2
    docker logs --tail 15 "$RESTORED" >&2
    exit 1
  fi
  if docker exec "$RESTORED" pg_isready -U postgres -q 2>/dev/null \
    && [[ "$(sql "$RESTORED" "SELECT pg_is_in_recovery()" 2>/dev/null)" == "f" ]]; then
    break
  fi
  sleep 1
done

echo "6. Conferência"
fail=0
check() {
  if [[ "$2" == "$3" ]]; then echo "   ok: $1"; else echo "   FALHOU: $1 (esperado $3, obtido $2)"; fail=1; fi
}
check "fora de recuperação" "$(sql "$RESTORED" "SELECT pg_is_in_recovery()")" "f"
check "contratos" "$(sql "$RESTORED" "SELECT count(*) FROM contracts_contract")" "$CONTRACTS"
check "títulos anteriores à alteração" \
  "$(sql "$RESTORED" "SELECT md5(string_agg(title, '|' ORDER BY id)) FROM contracts_contract")" "$TITLES"
check "marca anterior a T" "$(sql "$RESTORED" "SELECT string_agg(marca, ',') FROM exercicio_pitr")" "antes de T"
check "RLS forçada mantida" \
  "$(sql "$RESTORED" "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'contracts_contract'")" "t"
exit "$fail"
