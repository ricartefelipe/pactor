# Situação da implementação

Atualizado a cada integração em `develop`. "Exercitado" significa executado contra a
dependência real; sem essa marca, o item tem código e testes locais.

## Concluído

| Área | Itens | Evidência |
| --- | --- | --- |
| Fundação | Configuração tipada, papéis de banco, health checks, erros da API, logs e métricas | CI do PR #1 |
| Tenancy | RLS forçada, FKs compostas, contexto local à transação, reutilização de conexão e de worker | `tests/integration/test_tenant_isolation.py`, `test_outbox.py` |
| Identidade | Organizações, departamentos, equipes, associações, convites, troca de organização, revogação imediata, último proprietário | `tests/integration/test_identity.py`, `test_rejections.py` |
| Permissões | Matriz de papéis, visibilidade por classificação, MFA para privilegiados (propriedades Hypothesis) | `tests/unit/test_policies.py` |
| Planos | Trial, limites de usuários/contratos/armazenamento/módulos | `test_rejections.py` |
| Auditoria | Registro redigido, somente-inserção, isolado por organização | `test_identity.py` |
| Outbox | Emissão transacional, relay com concessão, backoff, eventos esgotados, consumidores deduplicados | `test_outbox.py`, `test_consumer_tasks.py` |
| Worker real | Outbox → RabbitMQ → worker Celery (papel `pactor_app`) → Mailpit, republicação sem duplicar e-mail | `tests/infra/test_worker_pipeline.py` (exercitado) |
| Idempotência | Chave, escopo e fingerprint; disputa entre conexões; rollback desfaz a reserva | `tests/concurrency/test_idempotency_race.py`, `test_contracts.py` |
| Contrapartes | CPF/CNPJ, contatos, endereços, representantes, concorrência otimista, isolamento | `tests/integration/test_contracts.py` |
| Contratos | Criação idempotente, referência sequencial, rascunho, revisões imutáveis, comparação textual, comentários, compartilhamento | `tests/integration/test_contracts.py` |
| Visibilidade | Consulta equivalente à política (Hypothesis sobre PostgreSQL), confidenciais, externos, equipes | `test_property_visibility_query_matches_policy` |
| Concorrência de revisões | Edição simultânea de rascunho e publicação simultânea | `tests/concurrency/test_revision_races.py` |

## Em andamento

- Aprovações vinculadas à revisão e envio ao simulador de assinatura.

## Verificações executadas

| Data | Comando | Resultado |
| --- | --- | --- |
| 2026-11-22 | `make test` | 194 testes aprovados; gate core 98,90% / 94,00%; gate crítico 98,33% / 91,67% |
| 2026-11-21 | `make test` | 152 testes aprovados; gate core 99,66% linhas / 97,87% branches; gate crítico 97,71% / 93,90% |
| 2026-11-21 | `make lint typecheck audit bandit migrations-check` | Sem apontamentos |

## Pendências reais

- Aprovações, assinaturas, vigência, obrigações, prazos, valores, arquivos,
  pesquisa, integrações OAuth e frontend.
- Teste de perda de worker durante a execução (redelivery com `acks_late`) ainda não automatizado.
- Anexos de revisão entram no fingerprint quando o módulo de arquivos for integrado.
- gitleaks e Trivy executados apenas na CI (não disponíveis no ambiente local).
