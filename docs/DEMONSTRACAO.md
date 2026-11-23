# Roteiro demonstrativo

Todos os dados da demonstração são fictícios: organizações, pessoas,
documentos (CNPJs de exemplo com dígitos válidos), valores, feriados e textos
de modelo. O modelo de contrato não é texto jurídico recomendado.

## Preparação

```sh
cp .env.example .env
docker compose up -d --build
docker compose run --rm web python manage.py seed_demo
```

O `seed_demo` pode ser executado quantas vezes for preciso: nada é duplicado.
Ele é recusado quando `PACTOR_ENVIRONMENT=production`. O seed usa os casos de
uso reais (convites aceitos, regras de plano, auditoria e outbox), então os
e-mails de convite aparecem no Mailpit (<http://localhost:8025>).

### Credenciais fictícias

| Usuário | Papel | MFA |
| --- | --- | --- |
| `proprietaria.a@exemplo.test` | Proprietário | TOTP |
| `admin.a@exemplo.test` | Administrador | TOTP |
| `gestora.a@exemplo.test` | Gestor de contratos | — |
| `aprovador.a@exemplo.test` | Aprovador (Jurídico) | — |
| `revisora.a@exemplo.test` | Revisor (Jurídico) | — |
| `financeiro.a@exemplo.test` | Financeiro | — |
| `solicitante.a@exemplo.test` | Solicitante | — |

A Organização B tem os mesmos papéis, com o sufixo `.b`
(ex.: `gestora.b@exemplo.test`).

- Senha de todos: `Pactor-Demo-2026!`
- Segredo TOTP de demonstração (proprietário e administrador): `JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP`
  (cadastre em qualquer aplicativo autenticador ou gere o código atual com o
  comando abaixo).

```sh
docker compose run --rm web python manage.py shell -c "import time; from allauth.mfa.totp.internal.auth import format_hotp_value, hotp_value; print(format_hotp_value(hotp_value('JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP', int(time.time()) // 30)))"
```

Essas credenciais são públicas e só existem porque o seed é recusado em produção.

### O que o seed cria em cada organização

- Departamento "Jurídico (fictício)", com aprovador e revisora.
- Calendário de feriados demonstrativo (1º/1 e 25/12 de 2027, para revisão).
- Workflow "Aprovação padrão (demonstração)": uma etapa jurídica com quórum 1.
- Modelo publicado "Prestação de serviços (modelo demonstrativo)".
- Contraparte "Fornecedora Exemplo A/B Ltda (fictícia)".
- Contrato "Suporte técnico mensal — exemplo fictício A/B", em rascunho.

## Os 15 passos

Cada passo indica o comportamento esperado e o teste automatizado que hoje o
comprova no backend. Os passos pela interface web e o teste ponta a ponta no
navegador entram junto com o frontend.

| # | Passo | Comportamento esperado | Evidência automatizada |
| --- | --- | --- | --- |
| 1 | Entrar na Organização A | Login com senha; ações privilegiadas exigem TOTP. | `test_demo_seed.py::test_privileged_demo_users_have_totp_and_can_sign_in`, `test_identity.py::test_privileged_action_without_mfa_is_blocked` |
| 2 | Cadastrar contraparte | CNPJ validado e normalizado; documento duplicado é recusado. | `test_contracts.py::test_counterparty_create_normalizes_and_rejects_duplicates_and_invalid` |
| 3 | Criar solicitação | A solicitação é enviada, triada e convertida em contrato. | `test_drafting_intake.py::test_request_lifecycle_visibility_and_conversion` |
| 4 | Produzir revisão a partir do modelo | A revisão registra a versão do modelo e os dados usados. | `test_drafting_intake.py::test_generation_records_template_version_and_data_and_is_immutable` |
| 5 | Comentar e publicar nova revisão | Comentários resolvidos; revisões imutáveis e comparáveis. | `test_contracts.py::test_comments_and_resolution`, `test_contracts.py::test_revisions_listing_detail_and_textual_compare` |
| 6 | Aprovar a versão correta | A aprovação fica presa à revisão e à impressão digital do conteúdo. | `test_approvals.py::test_full_approval_binds_exact_revision_and_fingerprint` |
| 7 | Tentar enviar outra versão | Envio de revisão diferente da aprovada é bloqueado. | `test_signatures.py::test_send_requires_current_approval_and_matching_fingerprint`, `test_approvals.py::test_approval_is_invalidated_when_new_revision_is_published_after_it` |
| 8 | Enviar ao simulador de assinatura | Vai exatamente o documento aprovado, com hash registrado. | `test_signatures.py::test_sent_document_is_the_approved_revision_with_registered_hash` |
| 9 | Confirmar a conclusão por webhook | Webhook assinado conclui o envelope e registra a assinatura. | `test_signatures.py::test_participant_signature_does_not_complete_envelope_and_completion_via_webhook` |
| 10 | Repetir o evento | O mesmo evento é reconhecido como duplicado, sem novos efeitos. | `test_signatures.py::test_participant_signature_does_not_complete_envelope_and_completion_via_webhook`, `test_signatures.py::test_api_send_list_and_simulate_flow` |
| 11 | Ativar a vigência pela data | Com início futuro, aguarda; o agendador ativa na data. | `test_lifecycle.py::test_signed_with_future_start_awaits_and_scheduler_starts_and_ends` |
| 12 | Criar obrigação e prazo de renovação | Prazos em dias úteis com feriados; renovação com regra explícita. | `test_deadlines_obligations.py::test_obligation_and_calendar_api`, `test_deadlines_obligations.py::test_automatic_renewal_requires_authorization_and_runs_once` |
| 13 | Disparar alerta sem duplicidade | O alerta é disparado uma vez, mesmo após indisponibilidade. | `test_deadlines_obligations.py::test_alerts_are_idempotent_and_recovered_after_downtime`, `test_deadlines_obligations.py::test_fired_alert_is_emailed_once_to_active_recipients` |
| 14 | Pesquisar o contrato com autorização | Resultados, contagens e facetas respeitam a visibilidade. | `test_search.py::test_search_results_counts_and_facets_respect_visibility` |
| 15 | Entrar na Organização B | Nada da Organização A aparece, nem por identificador direto. | `test_identity.py::test_switch_to_foreign_organization_is_indistinguishable_from_missing`, `test_tenant_isolation.py`, `test_search.py::test_search_is_isolated_between_organizations` |

Os testes ficam em `tests/integration/` e rodam com `make test`.
