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
| Aprovações | Workflow versionado, condições validadas, sequencial/paralelo, quórum, rejeição, ajustes, delegação, autoaprovação, perda de acesso, substituição | `tests/integration/test_approvals.py`, `tests/unit/test_approval_domain.py` |
| Disputa aprovação × nova revisão | Duas ordens forçadas com conexões separadas; invariante verificada | `tests/concurrency/test_approval_vs_revision_race.py` |
| Assinaturas (simulador) | Envio da revisão aprovada, hash do enviado/retornado, ordem, participante × envelope, webhook duplicado e fora de ordem, envelope antigo, timeout após criação, indisponibilidade, cancelamento, reconciliação | `tests/integration/test_signatures.py`, `test_signature_rejections.py` |
| Assinaturas (worker real) | Outbox → RabbitMQ → worker `integrations` → simulador | `tests/infra/test_signature_pipeline.py` (exercitado) |
| Adaptador Docusign | Formato das requisições, JWT, referência, classificação de falhas, HMAC do Connect | `tests/unit/test_docusign_adapter.py` (transporte simulado; **integração real não exercitada** por falta de credenciais) |
| Modelos | Definição validada sem execução de código, versões imutáveis, geração com versão e dados registrados | `tests/unit/test_drafting_domain.py`, `tests/integration/test_drafting_intake.py` |
| Solicitações | Ciclo, visibilidade por solicitante, histórico somente-inserção, conversão em contrato | `tests/integration/test_drafting_intake.py` |
| Vigência | Início futuro, indeterminado, agendador idempotente com recuperação, suspensão por política, rescisão, arquivamento, restrição no banco | `tests/unit/test_lifecycle_domain.py`, `tests/integration/test_lifecycle.py` |
| Aditivos | Alterações no snapshot aprovado, efeito após assinatura, condições por data, original preservado | `tests/integration/test_lifecycle.py` |
| Arquivos | Quarentena inacessível, scanner indisponível, arquivo malformado, macros, referência externa, caminhos, bomba ZIP, PDF ativo/criptografado, hash alterado, cota, anexos no fingerprint | `tests/unit/test_file_inspection.py`, `tests/integration/test_files.py` |
| ClamAV real | EICAR bloqueado e PDF liberado pelo pipeline | `tests/infra/test_clamav.py` (exercitado) |
| Prazos e calendário | Dias úteis, feriados, ajuste, fim de mês, bissextos, fuso da organização, recálculo após renovação | `tests/unit/test_calendar.py`, `tests/integration/test_deadlines_obligations.py` |
| Alertas | Únicos por alvo/tipo/data, disparo único após indisponibilidade, cancelamento, e-mail a destinatários ativos | `test_deadlines_obligations.py` |
| Renovação | Automática somente autorizada, registrada, antes do encerramento, bloqueada por não renovação | `test_deadlines_obligations.py` |
| Obrigações | Ocorrências estáveis e idempotentes, bissexto, mudança de cronograma preservando concluídas, evidência, dependência, aprovação | `test_deadlines_obligations.py` |
| Agendamento | Tasks agendadas e consumidores roteados registrados | `tests/unit/test_scheduling.py` |
| Valores | Decimal/BRL, recusa de float e não finitos, rateio exato, meio-par, confirmações e reajustes imutáveis, resumo | `tests/unit/test_money.py`, `tests/integration/test_finance.py` |
| Pesquisa | Permissões diferentes, facetas/contagens/sugestões/exportação sem vazamento, indexação atrasada, anexos não liberados fora do índice, isolamento | `tests/integration/test_search.py` |
| Integrações OAuth | Token real via /o/token/, tenant do cliente, escopos restritivos, visibilidade, revogação, token só no cabeçalho, MFA para gestão | `tests/integration/test_integrations.py` |
| Operação | `/metrics` com token, métricas de negócio somadas por organização sem identificadores em labels, modo multiprocesso, reprocessamento autorizado e isolado, correlação de logs por contrato, revisão e envelope | `tests/integration/test_operations.py`, `test_log_correlation.py`, `tests/unit/test_gunicorn_hooks.py` |
| Seed de demonstração | Duas organizações fictícias, idempotente, TOTP de demonstração, recusado em produção | `tests/integration/test_demo_seed.py`; executado duas vezes no banco local |
| Backup e restauração | `pg_dump` com `pactor_backup`, `pg_restore` em banco novo, conferência de contagens, donos, RLS e triggers; volume de mídia arquivado e restaurado | `docs/RUNBOOKS.md` (exercitado em 2026-11-23) |
| Aplicação web | Todas as áreas em português; estados de carregamento, vazio, erro, validação e sucesso; troca de organização com descarte do cache; formulários que só aparecem com os dados de referência carregados | `frontend/src/**/*.test.tsx` (Vitest) |
| Roteiro demonstrativo | 15 passos pela interface com web, workers, agendador, Mailpit e simulador reais; administração com TOTP; axe-core (WCAG 2 A/AA) | `frontend/e2e/demo.spec.ts` (exercitado localmente; também na CI com o Compose) |
| Telas complementares | Estrutura, convites, workflow, integrações, operação, auditoria, edição de contrato, acesso, anexo liberado pelo ClamAV real, parcelas, confirmação, índice e reajuste único, modelos, recusa de solicitação, contrapartes, calendário e relatórios, com axe-core | `frontend/e2e/telas.spec.ts` (exercitado localmente; também na CI com o Compose) |
| Contrato da API | Respostas documentadas comparadas com as reais; tipos do frontend gerados e conferidos na CI | `tests/integration/test_api_documentation.py`, `npm run check:api` |
| Concorrência de revisões | Edição simultânea de rascunho e publicação simultânea | `tests/concurrency/test_revision_races.py` |

## Versões

| Versão | Data | Conteúdo |
| --- | --- | --- |
| 0.1.0 | 2026-11-23 | Primeira versão: todas as áreas acima, aplicação web e roteiro demonstrativo no navegador |

## Verificações executadas

| Data | Comando | Resultado |
| --- | --- | --- |
| 2026-11-23 | Revisão de código da versão 0.1.0 (`/code-review` sobre `release/0.1.0`) | 10 achados (9 importantes: aprovação com dados alterados, término de aditivos, renovação mensal, integrações criando solicitações, reajuste duplicado ou acima do máximo, corrida na numeração de parcelas, filtros de término; 1 menor: vencimentos após 9999); todos corrigidos com teste de regressão que falha sem a correção |
| 2026-11-23 | `make test` (após a revisão) | 583 testes aprovados; gate core 98,53% / 92,83%; gate crítico 98,04% / 91,57% |
| 2026-11-23 | `make test` (aplicação web) | 569 testes aprovados; gate core 98,52% / 92,81%; gate crítico 98,06% / 91,59% |
| 2026-11-23 | `make frontend-check` | Tipos da API atualizados; ESLint, Prettier e TypeScript sem apontamentos; 30 testes Vitest; `npm audit` sem vulnerabilidades; build ok |
| 2026-11-23 | `npx playwright test` (pilha local: gunicorn, worker, beat, Mailpit, simulador) | 2 testes aprovados (roteiro de 15 passos e administração com TOTP; axe-core sem violações graves) em 2,6 min |
| 2026-11-23 | `docker build` (estágio do frontend) | Imagem construída com a aplicação web compilada |
| 2026-11-23 | `make lock-check lint typecheck audit bandit migrations-check openapi-check` (operação, métricas e seed) | Sem apontamentos |
| 2026-11-23 | `make test` (operação, métricas e seed) | 553 testes aprovados; gate core 98,51% / 92,81%; gate crítico 98,01% / 91,59% |
| 2026-11-23 | `make test` (integrações OAuth) | 531 testes aprovados; gate core 98,49% / 92,86%; gate crítico 98,05% / 91,57% |
| 2026-11-23 | `make test` (pesquisa e relatórios) | 525 testes aprovados; gate core 98,55% / 93,09%; gate crítico 98,10% / 91,77% |
| 2026-11-23 | `make test` (valores e compromissos) | 518 testes aprovados; gate core 98,55% / 93,00%; gate crítico 98,11% / 91,82% |
| 2026-11-23 | `make test` (prazos e obrigações) | 483 testes aprovados; gate core 98,71% / 93,55%; gate crítico 98,24% / 92,27% |
| 2026-11-23 | `make test` (arquivos e ClamAV) | 445 testes aprovados; gate core 98,35% / 93,11%; gate crítico 97,83% / 91,67% |
| 2026-11-22 | `make test` (vigência e aditivos) | 407 testes aprovados; gate core 98,60% / 93,46%; gate crítico 98,27% / 92,17% |
| 2026-11-22 | `make test` (modelos e solicitações) | 375 testes aprovados; gate core 98,56% / 93,27%; gate crítico 98,24% / 92,05% |
| 2026-11-22 | `make mutation` (políticas, aprovações, contratos, documentos) | 645 mutantes: 604 mortos (93,6%), 41 sobreviventes analisados — mensagens de erro decoradas/sem localização e equivalentes (`zip(strict)` com tamanhos iguais, `ensure_ascii=None`, `"UTF-8"`, atribuição a `group_approved` já falso) |
| 2026-11-22 | `make openapi-check` | Esquema válido, sem avisos |
| 2026-11-22 | `make test` | 279 testes aprovados; gate core 99,08% / 95,76%; gate crítico 98,70% / 94,37% |
| 2026-11-21 | `make test` | 152 testes aprovados; gate core 99,66% linhas / 97,87% branches; gate crítico 97,71% / 93,90% |
| 2026-11-21 | `make lint typecheck audit bandit migrations-check` | Sem apontamentos |

## Pendências reais

- Integração Docusign real (adaptador pronto; requer credenciais de demonstração para `make test-external`).
- Recuperação a um ponto no tempo (WAL): só procedimento escrito (`RUNBOOKS.md`).
- Teste de perda de worker durante a execução (redelivery com `acks_late`) ainda não automatizado.
- gitleaks e Trivy executados apenas na CI (não disponíveis no ambiente local).
