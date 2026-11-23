# Runbooks operacionais

Cada procedimento indica se foi **exercitado** (executado de ponta a ponta,
com resultado conferido) ou se é apenas **escrito** (ainda não executado em
ambiente algum). Um procedimento escrito não deve ser tratado como garantia de
recuperação até ser exercitado.

Os comandos assumem o Compose do repositório (`docker compose`, projeto
`pactor`). Em outros ambientes, troque `docker compose exec postgres` pelo
acesso equivalente ao servidor PostgreSQL.

## 1. Papéis de banco envolvidos

| Papel | Uso | Observação |
| --- | --- | --- |
| `postgres` (superusuário) | Inicialização do volume e restauração | Não é usado por nenhum processo da aplicação. |
| `pactor_owner` | Migrations | Dono das tabelas; **sujeito ao RLS** (RLS forçada). |
| `pactor_app` | web, workers e agendador | Sem `BYPASSRLS`; só enxerga a organização vinculada à transação. |
| `pactor_backup` | `pg_dump` | Somente leitura (`pg_read_all_data`) com `BYPASSRLS`. |

Com RLS forçada, nem o dono consegue exportar as tabelas: o `pg_dump` falha
com `query would be affected by row-level security policy`. Por isso existe o
papel `pactor_backup`, criado por `docker/postgres/10-roles.sh` em volumes
novos. Em um cluster já existente, crie-o uma vez como superusuário:

```sh
docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE pactor_backup LOGIN NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD '<senha>'" \
  -c "GRANT pg_read_all_data TO pactor_backup"
```

## 2. Backup e restauração do banco — exercitado

**Exercitado em 23/11/2026**, no Compose local (PostgreSQL 17.12), com os dados
do `seed_demo`. O banco restaurado foi conferido contra o original:

- mesmas contagens de organizações (2), contratos (2) e eventos de auditoria (40);
- tabelas continuam com dono `pactor_owner` e RLS forçada;
- `pactor_app` mantém os privilégios de DML;
- os 11 triggers de trilha imutável foram restaurados;
- como `pactor_app`, sem contexto de organização, a consulta a contratos
  retorna 0 linhas; com o contexto da Organização A, retorna somente o contrato dela.

### Backup

```sh
mkdir -p backups
docker compose exec -T -e PGPASSWORD="$PACTOR_BACKUP_PASSWORD" postgres \
  pg_dump -h localhost -U pactor_backup -d pactor --format=custom \
  > "backups/pactor-$(date +%Y%m%dT%H%M%S).dump"
```

O formato `custom` preserva donos, privilégios, policies, triggers e
privilégios padrão. Não use `--no-owner`: a restauração deve devolver as
tabelas ao `pactor_owner`.

### Restauração

A restauração exige superusuário: carregar tabelas com RLS forçada contorna as
policies, e os donos originais precisam ser restabelecidos.

1. Pare os processos que escrevem no banco: `docker compose stop web worker worker-integrations worker-extraction scheduler`.
2. Restaure em um banco novo. Nunca restaure por cima do banco em uso.

   ```sh
   docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 \
     -c "CREATE DATABASE pactor_restore OWNER pactor_owner"
   docker compose exec -T postgres pg_restore -U postgres -d pactor_restore \
     --exit-on-error < backups/<arquivo>.dump
   ```

3. Confira as contagens e o isolamento:

   ```sh
   docker compose exec -T postgres psql -U postgres -d pactor_restore -At \
     -c "SELECT count(*) FROM identity_organization" \
     -c "SELECT tableowner FROM pg_tables WHERE tablename = 'contracts_contract'" \
     -c "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'contracts_contract'"
   ```

4. Troque os bancos (`ALTER DATABASE pactor RENAME TO pactor_antigo;`
   `ALTER DATABASE pactor_restore RENAME TO pactor;`), rode
   `docker compose run --rm migrate` e suba os processos novamente.
5. Mantenha `pactor_antigo` até concluir a validação funcional e só então remova-o.

## 3. Backup e restauração dos documentos — exercitado

Os arquivos (originais, quarentena e documentos assinados) ficam no volume
`media`, montado em `/srv/pactor/media`. Os metadados e hashes SHA-256 ficam
no banco, por isso os dois backups precisam ser feitos no mesmo intervalo de
manutenção. Arquivo sem registro no banco nunca é servido; registro sem
arquivo aparece como erro de leitura e deve ser investigado.

**Exercitado em 23/11/2026**: um arquivo de teste foi gravado no volume,
arquivado, restaurado em um volume novo e lido com conteúdo idêntico.

```sh
# Backup (volume montado somente leitura)
docker run --rm -v pactor_media:/media:ro -v "$PWD/backups:/backup" \
  postgres:17.12-alpine3.24 tar -C /media -czf /backup/media-$(date +%Y%m%dT%H%M%S).tar.gz .

# Restauração em volume novo
docker volume create pactor_media_restore
docker run --rm -v pactor_media_restore:/media -v "$PWD/backups:/backup:ro" \
  postgres:17.12-alpine3.24 tar -C /media -xzf /backup/<arquivo>.tar.gz
```

Depois de restaurar, aponte o serviço para o volume novo ou copie o conteúdo
para `pactor_media` com os processos parados.

## 4. Procedimentos escritos, ainda não exercitados

- **Recuperação a um ponto no tempo** (arquivamento de WAL e `restore_command`):
  exige armazenamento externo de WAL, que não existe no Compose local.
- **Cópia externa e retenção dos backups** (criptografia em repouso, retenção
  por 30 dias, teste mensal de restauração): depende do provedor de armazenamento.
- **Conferência de integridade dos documentos após a restauração**: recalcular
  o SHA-256 de cada arquivo e comparar com `files_storedfile.sha256`.
  Ainda não há comando para isso.

## 5. Outbox, consumidores e reprocessamento

Sinais em `/metrics` (exige `Authorization: Bearer $PACTOR_METRICS_TOKEN`):

| Métrica | Atenção quando |
| --- | --- |
| `pactor_outbox_oldest_pending_age_seconds` | > 120 s: o relay (`relay-outbox`, a cada 10 s) não está publicando. |
| `pactor_outbox_events{status="dead"}` | > 0: eventos esgotaram as tentativas de publicação. |
| `pactor_consumer_failures{status="failed"}` | > 0: consumidores falharam de forma definitiva. |
| `pactor_consumer_stuck` | > 0: consumidor em retentativa há mais de 15 minutos. |
| `pactor_signature_events_pending` | crescendo: webhooks recebidos e ainda não aplicados. |
| `pactor_files{status="quarantined"}` | crescendo: extração ou varredura paradas. |
| `pactor_alerts_overdue` | > 0 por mais de 2 minutos: `deadlines-dispatch-alerts` parado. |

**Outbox parada**: confira `docker compose ps scheduler worker` e a
conectividade com o RabbitMQ (`/health/ready`). O relay retoma sozinho
quando o broker volta, porque a publicação acontece fora da transação e os
leases expiram.

**Falhas de consumidores** (proprietário ou administrador, com MFA):

1. Corrija a causa (SMTP, Docusign, ClamAV etc.).
2. Liste: `GET /api/v1/operations/failures`.
3. Reprocesse uma falha: `POST /api/v1/operations/failures/<id>/requeue`.
   O evento original é republicado com o mesmo identificador, então a
   deduplicação do consumidor continua valendo. Fila indisponível responde
   `409 broker_unavailable` e a falha continua reprocessável.
4. Eventos esgotados da organização: `POST /api/v1/operations/dead-events/requeue`.

Toda ação de reprocessamento fica registrada na auditoria
(`operations.requeued`, `operations.dead_events_requeued`).

## 6. Assinaturas

- **Webhook perdido**: `signatures-reconcile` (a cada 5 minutos) consulta o
  provedor e aplica o estado. Nenhuma ação manual é necessária.
- **Eventos recebidos e não aplicados**: `signatures-pending-events` reprocessa
  a cada minuto. Se `pactor_signature_events_pending` não cair, verifique os
  logs do worker `integrations`.
- **Segredo HMAC do Docusign Connect trocado**: atualize
  `DOCUSIGN_CONNECT_HMAC_KEY` e reinicie o web. Webhooks com assinatura
  inválida são recusados com 401 e não são gravados.

## 7. Arquivos e ClamAV

- `/health/ready` não depende do ClamAV: o upload continua aceitando arquivos,
  que permanecem em quarentena até a varredura.
- Com o ClamAV indisponível, as varreduras são retentadas e nenhum arquivo é
  liberado sem varredura limpa. Esgotadas as tentativas, a falha de
  `pactor.files.process_file` aparece em `/api/v1/operations/failures` e é
  reprocessada como na seção 5, depois que o ClamAV voltar.
- Atualização de assinaturas de vírus: o contêiner `clamav` roda o `freshclam`;
  o volume `clamav-data` evita baixar a base completa a cada reinício.

## 8. Credenciais e segredos

- **Token de métricas**: gere outro (`openssl rand -hex 32`), atualize
  `PACTOR_METRICS_TOKEN` no web e no coletor e reinicie o web. Vazio desativa
  o endpoint (404).
- **Integração comprometida**: `POST /api/v1/integrations/<id>/revoke`
  invalida imediatamente todos os tokens emitidos.
- **Sessões de um usuário**: revogar a associação encerra o acesso na
  requisição seguinte, mesmo com a sessão aberta.
