# Arquitetura

## C4 — Contexto

```mermaid
flowchart LR
  user[Usuários da organização] -->|navegador| pactor[Pactor]
  ext[Participantes externos] -->|navegador, acesso restrito| pactor
  m2m[Sistemas do cliente] -->|OAuth client credentials| pactor
  pactor -->|envelopes, webhooks| sign[Provedor de assinatura]
  pactor -->|SMTP| mail[Servidor de e-mail]
```

## C4 — Containers

```mermaid
flowchart TB
  browser[SPA React servida na mesma origem] --> web
  web[web: Gunicorn + Django/DRF] --> pg[(PostgreSQL 17 + RLS)]
  web --> redis[(Redis: cache, rate limit)]
  web -->|outbox no mesmo commit| pg
  beat[scheduler: Celery beat] --> mq[(RabbitMQ)]
  relay[relay da outbox] --> mq
  mq --> wdef[worker default/notifications]
  mq --> wint[worker integrations]
  mq --> wext[worker extraction]
  wext --> clam[ClamAV]
  wint --> sign[Provedor de assinatura]
  wdef --> mail[Mailpit/SMTP]
  migrate[migrate: job único com papel proprietário] --> pg
```

## Processos

| Processo | Comando | Papel de banco |
| --- | --- | --- |
| `migrate` | `manage.py migrate` | `pactor_owner` |
| `web` | Gunicorn (`pactor.config.gunicorn`) | `pactor_app` |
| `worker*` | `celery worker` por fila | `pactor_app` |
| `scheduler` | `celery beat` | `pactor_app` |

## Contextos e módulos

| Módulo | Responsabilidade |
| --- | --- |
| `pactor.core` | Tenancy no banco, outbox, consumidores, idempotência, erros, observabilidade, health |
| `pactor.access` | Políticas puras (papéis, permissões, visibilidade) e base das views por tenant |
| `pactor.identity` | Usuários, organizações, departamentos, equipes, associações e convites |
| `pactor.plans` | Planos comerciais, trial e limites (assinatura da organização no Pactor) |
| `pactor.audit` | Trilha de auditoria somente-inserção e bloqueios de retenção |
| `pactor.notifications` | Consumidores que enviam e-mails |

Regras de negócio ficam em módulos sem dependência de Django (`access/policies.py`)
ou em serviços com limites transacionais explícitos (`*/services.py`). Views apenas
validam entrada, chamam serviços e serializam a saída.

## Estratégia de isolamento entre organizações

Defesa em profundidade, da aplicação ao banco:

1. **Principal por requisição.** Após autenticar, `TenantScopedMixin.initial` relê a
   associação ativa do usuário na organização selecionada (sessão do servidor). Sem
   associação ativa, a requisição falha com `organization_not_selected`.
2. **Escopo explícito.** Toda consulta de serviço/selector filtra por
   `organization_id` do principal ou de `require_tenant()`.
3. **Policies.** Funções puras decidem permissões e visibilidade por contrato.
4. **RLS forçada.** Toda tabela de tenant tem `ENABLE` e `FORCE ROW LEVEL SECURITY`
   com a policy `tenant_isolation`:
   `organization_id = NULLIF(current_setting('pactor.tenant_id', true), '')::uuid`.
   Associações também são legíveis pelo próprio usuário (`pactor.user_id`), para
   listar suas organizações; escritas sempre exigem o tenant.
5. **Contexto local à transação.** O tenant é definido com
   `set_config('pactor.tenant_id', ..., true)` dentro da transação da requisição
   (`ATOMIC_REQUESTS`) ou de `tenant_context()`. Ao terminar a transação, o valor
   expira; conexões reutilizadas não herdam tenant. Ausência de contexto ⇒ zero linhas.
6. **FKs compostas.** Referências entre tabelas de tenant usam
   `(organization_id, <fk>) → (organization_id, id)`, impedindo vínculos entre
   organizações mesmo com erro de escopo no código.
7. **Papel sem privilégios.** `pactor_app` não é superuser, não tem `BYPASSRLS` e não
   é dono das tabelas (não pode desabilitar RLS).

Testes de integração percorrem o catálogo do PostgreSQL e falham se alguma tabela de
tenant estiver sem RLS forçada ou se alguma FK entre tabelas de tenant não for composta.

Tabelas globais (sem RLS): usuários, organizações, planos, sessões, outbox e registros
de consumo. A outbox contém apenas identificadores; consumidores reconstituem o tenant
antes de ler entidades.

## Matriz de permissões

`✔` concedido pelo papel; `MFA` exige autenticação multifator na sessão.
Permissões de contrato dependem ainda da visibilidade do contrato (abaixo).

| Permissão | Prop. | Admin | Gestor | Solic. | Revisor | Aprov. | Financ. | Auditor |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| members.manage | MFA | MFA | | | | | | |
| organization.manage / billing.manage | MFA | | | | | | | |
| integrations.manage / workflows.manage | MFA | MFA | | | | | | |
| operations.reprocess | MFA | MFA | | | | | | |
| audit.read | ✔ | ✔ | | | | | | ✔ |
| counterparties.read | | | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| counterparties.write | | | ✔ | | | | | |
| requests.create | | | ✔ | ✔ | | | | |
| requests.manage | | | ✔ | | | | | |
| templates.manage | | | ✔ | | | | | |
| contracts.read | | | ✔ | | ✔ | ✔ | ✔ | ✔ |
| contracts.create / submit / lifecycle / share | | | ✔ | | | | | |
| contracts.edit | | | ✔ | | ✔ | | | |
| contracts.comment | | | ✔ | ✔ | ✔ | ✔ | | |
| approvals.vote | | | | | | ✔ | | |
| signatures.send | | | ✔ | | | | | |
| obligations.manage | | | ✔ | | | | | |
| obligations.complete | | | ✔ | ✔ | | | ✔ | |
| finance.read | | | ✔ | | | | ✔ | |
| finance.write | | | | | | | ✔ | |
| reports.read | | | ✔ | | | | ✔ | ✔ |

Participantes externos: `contracts.read`, `contracts.comment` e `obligations.complete`,
apenas nos contratos compartilhados explicitamente com eles.

### Visibilidade de contratos

| Classificação | Quem enxerga (além de vínculo direto) |
| --- | --- |
| Interno | Quem tem `contracts.read` |
| Restrito | Gestor de contratos, auditor e membros do departamento do contrato com `contracts.read` |
| Confidencial | Somente vínculo direto |

Vínculo direto: concessão ao usuário ou a uma equipe dele, responsável, solicitante ou
aprovador de rodada ativa. Proprietário e administrador **não** enxergam contratos pelo papel.

## Processamento assíncrono

```mermaid
sequenceDiagram
  participant S as Serviço (transação)
  participant DB as PostgreSQL
  participant R as Relay (beat 10s / on_commit)
  participant MQ as RabbitMQ
  participant W as Worker
  S->>DB: alteração de negócio + OutboxEvent (mesmo commit)
  R->>DB: reivindica lote (SKIP LOCKED, concessão 60s)
  R->>MQ: publica fora de transação (task_id estável)
  R->>DB: marca publicado / agenda retentativa com backoff
  MQ->>W: entrega (acks_late, reject_on_worker_lost, prefetch 1)
  W->>DB: tenant_context + ConsumedEvent único + efeitos
```

- Falha entre commit e publicação: o evento permanece `pending` e o relay periódico publica.
- Queda do relay após publicar: a concessão expira, o evento é republicado e o consumidor
  deduplica por `(consumidor, evento)`.
- Consumidores transacionais aplicam efeitos e registram consumo no mesmo commit (efeito
  único). Consumidores com efeito externo (e-mail) registram consumo após o efeito: garantia
  de pelo menos uma vez, com deduplicação nas repetições posteriores.
- Retentativas: até 6, backoff exponencial (5s a 10min). Falhas ficam em `ConsumerFailure`.
- Cada task fecha conexões antigas antes e depois e abre o tenant do evento.

## Contratos, rascunho e revisões

```mermaid
stateDiagram-v2
  [*] --> draft
  draft --> in_approval: envio para aprovação
  in_approval --> draft: ajustes, rejeição ou nova revisão
  in_approval --> approved: quórum atingido
  approved --> draft: nova revisão ou metadados alterados
  approved --> signing: envio da revisão aprovada
  signing --> signed: envelope concluído
  signing --> approved: envelope anulado ou recusado
  draft --> cancelled
  in_approval --> cancelled
  approved --> cancelled
  signing --> cancelled
  signed --> [*]
  cancelled --> [*]
```

- **Rascunho** (`ContractDraft`): único por contrato, editável, com `version` para
  concorrência otimista (`UPDATE ... WHERE version = esperado`; zero linhas ⇒ `stale_version`).
- **Revisão** (`Revision`): publicada a partir do rascunho exigindo a versão do rascunho que
  o autor viu. Guarda conteúdo, SHA-256 do conteúdo, snapshot de metadados relevantes,
  anexos (com hash), autor, data, revisão anterior e **fingerprint** do conjunto submetido.
  Um trigger no banco impede `UPDATE` e `DELETE`.
- **Fingerprint**: SHA-256 do JSON canônico com hash do conteúdo, metadados (título, tipo,
  contraparte e documento, papéis das partes, departamento, classificação, valor, datas,
  contrato principal) e anexos. Identifica os bytes e dados registrados; não substitui
  assinatura digital nem constitui comprovação jurídica independente.
- **Bloqueio**: publicação, alteração de metadados e compartilhamento travam a linha do
  contrato (`SELECT ... FOR UPDATE` sem joins); relações são lidas depois do lock para não
  usar dados anteriores a um commit concorrente.
- **Comparação**: textual, por linhas (`difflib`); não há comparação semântica.
- **Referências**: `CT-<ano>-<sequência>` por organização, com numerador travado.

## Aprovações

- **Workflow versionado** (`WorkflowDefinition`): regras ordenadas; cada regra tem condições
  (campo permitido + operador permitido + valor tipado) e etapas. A última regra é a padrão,
  sem condições. Publicar cria nova versão e desativa a anterior; não há edição in-place.
- **Campos permitidos**: `contract_type`, `department_id`, `classification`, `confidentiality`
  (operadores `eq`, `ne`, `in`, `not_in`) e `total_value` (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`).
  Valores lidos do snapshot da revisão submetida. Não há `eval` nem expressões.
- **Rodada** (`ApprovalRound`): vinculada à revisão, ao fingerprint, à versão do workflow e
  ao snapshot da regra usada. Uma rodada aberta por contrato.
- **Etapas**: mesmo `order` ⇒ paralelas; `order` crescente ⇒ sequenciais. Quórum por etapa.
- **Votos**: `aprovar`, `rejeitar` ou `solicitar ajustes`, com revisão e fingerprint gravados.
  Restrições no banco: uma vaga vota uma vez; uma pessoa vota uma vez por etapa (impede dupla
  contagem por delegação); votos são somente-inserção.
- **Delegação**: intervalo semiaberto `[início, fim)`, revogável, sem sobreposição; o delegado
  precisa ser aprovador ativo. Voto por delegação registra vaga (`approver`) e autor (`cast_by`).
- **Autoaprovação**: quando proibida, autor da revisão e solicitante não votam nem por delegação.
- **Perda de acesso**: vagas de quem perdeu associação ou papel não contam como elegíveis; a
  etapa fica `blocked` se o quórum se tornar inatingível, até a substituição auditada do
  aprovador (`replace-approver`). Votos já registrados são preservados.
- **Disputa com nova revisão**: voto e publicação travam o contrato. Se o voto vem antes, a
  publicação seguinte invalida a aprovação; se a publicação vem antes, a rodada é substituída
  e o voto é recusado. Testado nas duas ordens com conexões separadas.
- **Envio para assinatura** exige `approved`, revisão aprovada igual à vigente e fingerprint
  idêntico, sem rodada aberta (`require_approved_for_signature`).

## Assinaturas eletrônicas

```mermaid
sequenceDiagram
  participant U as Gestor (API)
  participant DB as PostgreSQL
  participant W as Worker integrations
  participant P as Provedor (simulador/Docusign)
  participant H as Webhook
  U->>DB: envio: aprovação vigente + fingerprint esperado, PDF da revisão aprovada (hash), envelope e outbox
  W->>DB: plan_dispatch (transação curta, lê bytes e confere hash)
  W->>P: create_envelope(referência = id do envelope) fora de transação
  alt timeout/5xx após envio
    W->>DB: dispatch_unknown
    W->>P: find_by_reference antes de qualquer novo envio
  end
  W->>DB: provider_envelope_id, rota do webhook, situação "sent"
  P->>H: evento assinado (HMAC)
  H->>DB: inbox (dedup) e aplicação no tenant com contrato e envelope travados
```

- **Porta** (`signatures/ports.py`): criar, consultar por referência, consultar situação, cancelar,
  baixar documentos e interpretar webhooks. Adaptadores: **simulador** (local, determinístico,
  identificado em todos os documentos, recusado em produção) e **Docusign** (eSignature REST v2.1,
  JWT Grant, campo customizado `pactor_reference`, Connect com HMAC).
- **Revisão estável**: o envelope guarda revisão, fingerprint, provedor e conta. O PDF é gerado da
  revisão **aprovada** e armazenado com SHA-256 no envio; o worker envia exatamente esses bytes
  (hash conferido na leitura) e recusa o envio se a aprovação mudou.
- **Participante × envelope**: assinatura de um participante não conclui o envelope.
- **Eventos**: inbox com chave de deduplicação do provedor; situações só avançam (eventos fora de
  ordem não regridem); eventos de envelope cancelado, com falha, substituído ou de revisão diferente
  da aprovada são registrados como `old_envelope` e não alteram o contrato.
- **Documentos**: enviado, assinado e evidências são preservados separadamente, cada um com hash;
  downloads passam por autorização do contrato e conferem o hash antes de entregar os bytes.
- **Recuperação**: `reconcile` (periódico) consulta o provedor para envelopes sem confirmação ou sem
  eventos recentes; `process_pending_events` reaplica eventos gravados e não processados.

## Modelos de contrato e solicitações

- **Modelo** (`ContractTemplate`): rascunho da definição com concorrência otimista; **versões**
  (`TemplateVersion`) são publicadas como novas linhas somente-inserção.
- **Definição**: campos tipados (`text`, `date`, `decimal`, `boolean`, `choice`), seções com
  marcadores `{{chave}}` e seções opcionais condicionadas a um campo booleano. Qualquer outra
  sintaxe entre chaves é recusada na validação; não há expressões, filtros ou execução de código.
  A substituição é feita em uma única passagem: valores do usuário entram como texto literal.
- **Geração**: valida os dados contra a versão do modelo, renderiza o texto e publica uma nova
  revisão (`source = template`) que registra a versão do modelo e os dados canônicos usados.
  Publicar outra versão do modelo não altera revisões existentes (imutáveis).
- **Solicitações** (`ContractRequest`): `submitted → in_review → converted` (ou `rejected` /
  `cancelled`), com histórico somente-inserção. Solicitantes veem apenas as próprias; gestores
  veem todas. A conversão cria o contrato preservando solicitante, contraparte, valor e datas.

## Vigência, aditivos e encerramento

```mermaid
stateDiagram-v2
  [*] --> not_started
  not_started --> awaiting_start: assinatura com início futuro
  not_started --> active: assinatura com início até hoje
  awaiting_start --> active: agendador na data de início
  active --> suspended: suspensão (se a política permitir)
  suspended --> active: retomada
  active --> ended: agendador após o término
  suspended --> ended: agendador após o término
  awaiting_start --> terminated: rescisão registrada
  active --> terminated: rescisão registrada
  suspended --> terminated: rescisão registrada
  ended --> archived
  terminated --> archived
```

- Situação de vigência é separada da elaboração e da assinatura; o banco impede vigência sem
  assinatura (`contract_lifecycle_requires_signature`).
- Datas de vigência são locais da organização (`zoneinfo`, fuso configurado) e os limites são
  inclusivos: o contrato vale do dia de início ao dia de término. Término vazio = indeterminado.
- O agendador (`pactor.contracts.advance_lifecycles`, de hora em hora) é idempotente e recupera
  indisponibilidades: se início e término já passaram, registra as duas transições.
- Ações manuais (suspender, retomar, rescindir, arquivar) exigem permissão, justificativa e data
  de efeito, e geram histórico somente-inserção (`LifecycleEvent`) e auditoria.
- **Aditivos** são contratos do tipo `amendment` ligados ao principal. Suas alterações
  (`AmendmentChange`: valor total, término, descrição) fazem parte do snapshot aprovado e
  assinado do aditivo e só valem depois da assinatura. O contrato original nunca é reescrito:
  `conditions_at` aplica, em ordem (data de efeito, ordem de registro), as alterações efetivas
  na data consultada.
- **Limite do plano**: contratos aguardando início, vigentes, suspensos ou em assinatura contam
  como ativos; o envio para assinatura é bloqueado ao exceder o limite.
