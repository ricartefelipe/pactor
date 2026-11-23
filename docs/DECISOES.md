# Registro de decisões arquiteturais

Formato: contexto, decisão e consequências. Decisões substituídas permanecem
registradas com a indicação da decisão que as substitui.

## ADR-001 — Python 3.13 e Django 5.2 LTS

**Contexto.** O produto precisa de suporte de longo prazo. Django 5.2 é a LTS
vigente (suporte de segurança até abril de 2028) e suporta Python 3.10–3.14.

**Decisão.** Python 3.13 (estável, suportado por todas as dependências fixadas)
e Django 5.2.x. Stubs alinhados à série 5.2 (`django-stubs` 5.2.9,
`djangorestframework-stubs` 3.16.9) e mypy 1.19, que é a faixa aceita por esses stubs.

**Consequências.** Atualizações de patch do Django são aplicadas dentro da série 5.2.
A migração para a próxima LTS será uma decisão própria.

## ADR-002 — Monólito modular com processos separados

**Decisão.** Um único pacote `pactor` com apps por contexto de negócio. Processos
separados: web (Gunicorn), workers Celery por fila (`default`/`notifications`,
`integrations`, `extraction`), agendador (Celery beat) e migração (job único).

**Consequências.** Transações locais garantem consistência entre contextos; a
separação física de processos isola perfis de carga e falhas.

## ADR-003 — Gunicorn com workers síncronos

**Contexto.** O ORM do Django é síncrono; o caminho HTTP não depende de I/O longo
(chamadas externas ocorrem em tasks).

**Decisão.** Gunicorn com workers síncronos. Nenhuma view assíncrona.

## ADR-004 — Dois papéis de banco

**Decisão.** `pactor_owner` é proprietário das tabelas e executa apenas migrations.
`pactor_app` é usado pelos processos web e worker: sem superuser, sem `BYPASSRLS`,
sem `CREATEDB`/`CREATEROLE` e sem posse de tabelas. A primeira migration concede
somente DML via privilégios padrão.

**Consequências.** RLS vale para a aplicação mesmo diante de erro de escopo no código.

## ADR-005 — Identificadores UUID gerados pelo servidor

**Decisão.** Entidades expostas usam UUIDv4 como chave primária.

**Consequências.** Identificadores não são enumeráveis; a autorização continua obrigatória.

## ADR-006 — Usuário customizado com geração de sessão

**Decisão.** `identity.User` autentica por e-mail. O hash de autenticação da sessão
inclui `session_generation`; incrementar o campo revoga todas as sessões do usuário.

## ADR-007 — Logs JSON e métricas com cardinalidade limitada

**Decisão.** Logs estruturados (python-json-logger) com chaves de correlação
(`request_id`, `organization_id`, `contract_id`, `revision_id`, `envelope_id`,
`task_id`) e redação de segredos. Métricas Prometheus usam a rota resolvida, nunca
o caminho bruto, como label.

## ADR-008 — Gates de cobertura por escopo

**Contexto.** O `fail_under` do coverage combina linhas e branches.

**Decisão.** `scripts/check_coverage.py` aplica limites separados de linhas (≥95%) e
branches (≥90%) por escopo declarado no `pyproject.toml`; escopo vazio falha.

## ADR-009 — Proteção de branches com um único mantenedor

**Contexto.** O repositório possui um único colaborador, e o GitHub não permite
aprovar o próprio pull request.

**Decisão.** `main` e `develop` exigem pull request, checks obrigatórios atualizados,
histórico sem force push nem exclusão, e a regra vale também para administradores.
A contagem mínima de aprovações fica em zero até existir um segundo revisor; nenhuma
aprovação é simulada.

**Consequências.** A revisão registrada em cada PR é a do próprio mantenedor. Ao
entrar um segundo colaborador, a exigência deve subir para pelo menos uma aprovação.

## ADR-010 — Arquivos de IDE fora do versionamento

**Decisão.** `.idea/` é ignorado; configurações de IDE permanecem locais.

## ADR-011 — RLS forçada com parâmetro local à transação

**Contexto.** O isolamento não pode depender apenas de filtros na aplicação, de managers ou
de `contextvars`.

**Decisão.** Policies RLS comparam `organization_id` com `current_setting('pactor.tenant_id')`,
definido com `set_config(..., true)` na transação. `FORCE ROW LEVEL SECURITY` aplica a regra
até ao proprietário das tabelas. Requisições usam `ATOMIC_REQUESTS`; tasks usam `tenant_context`.

**Consequências.** Cada requisição é uma transação; chamadas externas longas acontecem em tasks.
Testes que trocam de organização usam `transactional_db` para exercitar commits reais.

## ADR-012 — Outbox com reivindicação e publicação fora de transação

**Decisão.** Ver "Processamento assíncrono" em `ARQUITETURA.md`. Mensagens Celery em JSON,
`acks_late`, `task_reject_on_worker_lost`, `prefetch_multiplier=1`. Roteamento declarado em
`pactor/config/events.py`.

**Consequências.** Entrega pelo menos uma vez; deduplicação por consumidor. Nenhuma
configuração isolada do broker é tratada como garantia de ausência de perda ou duplicidade.

## ADR-013 — Idempotência por chave, escopo e fingerprint

**Decisão.** `Idempotency-Key` + escopo da operação + ator + organização. O registro nasce na
transação da operação (`INSERT ... ON CONFLICT DO NOTHING`); a segunda requisição concorrente
aguarda o commit da primeira e recebe a resposta gravada, ou `idempotency_conflict` se o
conteúdo for diferente.

## ADR-014 — Convites com token derivado por HMAC

**Decisão.** O link contém `organização.convite.HMAC(SECRET_KEY)`. Nada secreto vai ao banco ou
à outbox; o consumidor de e-mail recalcula o token. A organização no token permite abrir o
contexto de tenant antes de ler o convite.

**Consequências.** Trocar `SECRET_KEY` invalida convites pendentes.

## ADR-015 — Filtros de listagem explícitos, sem django-filter

**Contexto.** Os stubs tipados do django-filter exigem a série de stubs do Django 6, incompatível
com a LTS 5.2 adotada.

**Decisão.** Cada listagem declara um serializer de filtros; parâmetros desconhecidos são
rejeitados (`validation_failed`).

## ADR-016 — MFA obrigatório para permissões privilegiadas

**Decisão.** Permissões de gestão (membros, organização, cobrança, integrações, workflows,
reprocessamento) exigem que a sessão tenha concluído MFA (registro de autenticação do
django-allauth). Sem isso, a API responde `mfa_required`.

## ADR-017 — django-allauth headless somente para navegador

**Decisão.** `HEADLESS_ONLY` com cliente `browser`: sessão no servidor, cookie `HttpOnly` e CSRF
em login/logout. O cliente `app` (tokens) fica desabilitado; nenhum token é exposto ao JavaScript.

## ADR-018 — Condições de workflow como dados validados

**Decisão.** Condições são tuplas (campo, operador, valor) de listas fechadas, validadas na
publicação e avaliadas por funções puras. Valores decimais são texto ou inteiro (nunca `float`).

**Consequências.** Nenhuma entrada do usuário é executada; regras novas exigem código revisado.

## ADR-019 — Aprovadores ganham acesso temporário ao contrato

**Decisão.** A abertura da rodada cria concessões `approval` para as vagas; o encerramento as
remove. Assim, aprovadores de contratos confidenciais enxergam o conteúdo somente durante a rodada.

## ADR-020 — Mutation testing semanal no núcleo de regras

**Decisão.** `mutmut` sobre políticas de acesso, domínio de aprovações, domínio de contratos e
documentos (`make mutation`; workflow `mutation.yml` manual e semanal). Não é gate de PR pelo
custo; os sobreviventes são analisados e registrados em `STATUS.md`.

## ADR-021 — Envio de assinatura em duas fases, sem transação durante a rede

**Decisão.** A requisição registra envelope, documento e evento na outbox. O worker planeja o envio
em transação curta, chama o provedor fora de transação e confirma em nova transação. Timeout ou
resposta 5xx na criação marcam `dispatch_unknown`; toda tentativa posterior consulta o provedor pela
referência antes de criar outro envelope.

**Consequências.** Não há envelope duplicado por retentativa cega. A reconciliação periódica cobre
tentativas esgotadas e webhooks perdidos.

## ADR-022 — Simulador de assinatura como provedor de primeira classe

**Decisão.** O simulador implementa a mesma porta, persiste o estado do "lado do provedor" e emite
webhooks assinados com HMAC que percorrem o mesmo endpoint dos provedores reais. É recusado quando
`PACTOR_ENVIRONMENT=production`, e a configuração falha se for selecionado em produção.

## ADR-023 — Testes de provedores externos falham sem credenciais

**Decisão.** `tests/external` só roda com `make test-external` e falha quando faltam credenciais.
A CI não o executa; a situação fica registrada em `STATUS.md` como não exercitada.

## ADR-024 — Papel dedicado de backup com BYPASSRLS

**Contexto.** Com RLS forçada, nem o dono das tabelas consegue exportá-las: o `pg_dump` falha.
Desligar o `FORCE` para o backup abriria uma janela em que o dono enxergaria tudo.

**Decisão.** O papel `pactor_backup` (somente leitura via `pg_read_all_data`, com `BYPASSRLS`)
existe apenas para o `pg_dump`. Nenhum processo da aplicação usa esse papel. A restauração é feita
pelo superusuário do cluster, que restabelece donos, policies e privilégios.

**Consequências.** A credencial de backup dá leitura de todas as organizações e deve ser
guardada como as credenciais do banco. Backup e restauração foram exercitados (`RUNBOOKS.md`).

## ADR-025 — Métricas de negócio calculadas na coleta

**Decisão.** As métricas operacionais (outbox, falhas, arquivos, envelopes, alertas) são
consultadas no banco a cada coleta, em vez de contadores mantidos pelos processos.

**Consequências.** Os valores são corretos mesmo após reinícios e com vários workers. O custo
é linear no número de organizações, por causa do contexto de tenant em cada uma. Isso é
aceitável no intervalo usual de coleta (15 a 60 s) e deve ser revisto se o número de
organizações crescer muito.
