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

O teste `frontend/e2e/demo.spec.ts` executa o roteiro inteiro pela interface, na
pilha completa: workers, agendador, Mailpit e simulador. Na CI, ele roda contra
os contêineres do Compose (`make e2e` localmente). A última coluna aponta os
testes de backend que cobrem cada regra em detalhe.

| # | Passo | Na interface | Testes de backend |
| --- | --- | --- | --- |
| 1 | Entrar na Organização A | Entrar como `gestora.a@exemplo.test`; o painel mostra a Organização A. | `test_demo_seed.py::test_privileged_demo_users_have_totp_and_can_sign_in`, `test_identity.py::test_privileged_action_without_mfa_is_blocked` |
| 2 | Cadastrar contraparte | Contrapartes → Nova contraparte, com CNPJ válido. | `test_contracts.py::test_counterparty_create_normalizes_and_rejects_duplicates_and_invalid` |
| 3 | Criar solicitação | Solicitações → Nova solicitação → Iniciar triagem → Converter em contrato. | `test_drafting_intake.py::test_request_lifecycle_visibility_and_conversion` |
| 4 | Produzir revisão a partir de modelo | Contrato → Rascunho → Gerar a partir de modelo; campos tipados do modelo publicado. | `test_drafting_intake.py::test_generation_records_template_version_and_data_and_is_immutable` |
| 5 | Comentar e publicar nova revisão | Revisões e comentários → Comentar; Rascunho → Salvar → Publicar; comparar revisões 1 e 2. | `test_contracts.py::test_comments_and_resolution`, `test_contracts.py::test_revisions_listing_detail_and_textual_compare` |
| 6 | Aprovar a versão correta | Aprovação → Enviar revisão 2; entrar como `aprovador.a@exemplo.test` → Aprovações → Aprovar. | `test_approvals.py::test_full_approval_binds_exact_revision_and_fingerprint` |
| 7 | Tentar enviar outra versão | Publicar a revisão 3 depois da aprovação: a Assinatura informa que o envio exige revisão aprovada. Reenviar e aprovar a revisão 3. | `test_signatures.py::test_send_requires_current_approval_and_matching_fingerprint`, `test_approvals.py::test_approval_is_invalidated_when_new_revision_is_published_after_it` |
| 8 | Enviar ao simulador de assinatura | Assinatura → dois signatários → Enviar; o worker de integrações envia e o envelope fica "Enviado". | `test_signatures.py::test_sent_document_is_the_approved_revision_with_registered_hash` |
| 9 | Confirmar a conclusão por webhook | Assinar como cada signatário; cada ação entrega um webhook assinado e o envelope fica "Concluído". | `test_signatures.py::test_participant_signature_does_not_complete_envelope_and_completion_via_webhook` |
| 10 | Repetir o evento | Reenviar último evento: "reconhecido como duplicado: nenhum efeito novo". | `test_signatures.py::test_api_send_list_and_simulate_flow` |
| 11 | Ativar a vigência pela data | Vigência mostra "Vigente" (início previsto hoje); um início futuro fica "Aguardando início" até o agendador ativar. | `test_lifecycle.py::test_signed_with_future_start_awaits_and_scheduler_starts_and_ends` |
| 12 | Criar obrigação e prazo de renovação | Obrigações → Nova obrigação; Prazos e renovação → Novo prazo e política manual. | `test_deadlines_obligations.py::test_obligation_and_calendar_api`, `test_deadlines_obligations.py::test_automatic_renewal_requires_authorization_and_runs_once` |
| 13 | Disparar alerta sem duplicidade | O lembrete vencido é enviado pelo agendador; o Mailpit recebe um único e-mail, mesmo depois de outro ciclo. | `test_deadlines_obligations.py::test_alerts_are_idempotent_and_recovered_after_downtime`, `test_deadlines_obligations.py::test_fired_alert_is_emailed_once_to_active_recipients` |
| 14 | Pesquisar o contrato com autorização | Pesquisa pelo termo incluído na revisão: um resultado, com o termo destacado. | `test_search.py::test_search_results_counts_and_facets_respect_visibility` |
| 15 | Entrar na Organização B | Entrar como `gestora.b@exemplo.test`: a pesquisa não encontra nada, o endereço do contrato da A responde "não encontrado" e a contraparte não aparece. | `test_identity.py::test_switch_to_foreign_organization_is_indistinguishable_from_missing`, `test_tenant_isolation.py`, `test_search.py::test_search_is_isolated_between_organizations` |

O mesmo arquivo verifica a administração com segundo fator (`admin.a@exemplo.test`
com TOTP) e roda o axe-core (WCAG 2 A/AA) nas telas principais. As demais telas
(estrutura, convites, workflows, integrações, operação, auditoria, acesso, anexos,
valores, modelos, solicitações, contrapartes, calendário e relatórios) são
exercitadas por `frontend/e2e/telas.spec.ts`, inclusive o envio de anexo liberado
pelo ClamAV real.
