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
