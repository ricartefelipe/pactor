# Pactor

Plataforma SaaS B2B multitenant para gestão do ciclo de vida de contratos: da
solicitação e elaboração à aprovação, assinatura, execução, renovação e encerramento.

## Para quem

Empresas de serviços, departamentos administrativos e jurídicos, consultorias e
organizações que administram contratos com clientes e fornecedores.

O Pactor organiza informações e processos. Ele não interpreta cláusulas nem
garante validade jurídica ou conformidade dos documentos.

## O que faz

- **Organizações isoladas**: cada organização só enxerga os próprios dados. O
  isolamento é garantido no banco, por Row-Level Security, e não apenas no código.
- **Identidade e acessos**: convites, papéis, departamentos e equipes; verificação
  em duas etapas (TOTP) exigida para ações administrativas.
- **Contrapartes, solicitações e modelos** com campos tipados e versões imutáveis.
- **Contratos**: rascunho com controle de concorrência, revisões imutáveis com
  impressão digital, comparação entre revisões e comentários por linha.
- **Aprovação vinculada à revisão exata**: quórum, delegação e substituição de
  aprovadores. Qualquer mudança invalida a aprovação.
- **Assinatura eletrônica** pelo simulador ou pelo Docusign, com webhooks assinados,
  idempotentes e tolerantes a eventos fora de ordem.
- **Vigência e aditivos; prazos** em dias úteis com feriados da organização;
  renovação autorizada; obrigações com evidência; alertas sem duplicidade.
- **Valores** em decimal exato: parcelas, marcos, confirmações e reajustes.
- **Arquivos com quarentena** (ClamAV e inspeção isolada), pesquisa com autorização,
  relatórios, integrações OAuth (client credentials), auditoria somente de inclusão
  e métricas Prometheus.

## Arquitetura

Monólito modular em Django, com processos separados para web, workers e agendador.
A aplicação web (React) é servida na mesma origem da API. Detalhes em
[`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) (inclui os diagramas C4) e decisões em
[`docs/DECISOES.md`](docs/DECISOES.md).

| Componente | Tecnologia |
| --- | --- |
| Backend | Python 3.13, Django 5.2 LTS, Django REST Framework, allauth (headless e MFA), Django OAuth Toolkit |
| Frontend | React 19, TypeScript estrito, Vite, TanStack Query, React Router; tipos gerados do OpenAPI |
| Banco | PostgreSQL 17 com Row-Level Security forçada |
| Filas | Celery com RabbitMQ (Transactional Outbox) |
| Cache e controles temporários | Redis |
| Arquivos | ClamAV e inspeção em subprocesso isolado |
| Servidor | Gunicorn e whitenoise |

## Executando

Requisitos: Docker com Compose.

```bash
cp .env.example .env
docker compose up --build
docker compose exec web python manage.py seed_demo
```

- Aplicação: <http://localhost:8000> (usuários fictícios em [`docs/DEMONSTRACAO.md`](docs/DEMONSTRACAO.md))
- E-mails enviados (Mailpit): <http://localhost:8025>
- Health checks: `/health/live` e `/health/ready`
- Esquema da API: `/api/v1/schema` (autenticado)

O ClamAV baixa a base de assinaturas na primeira subida, o que leva alguns
minutos. Até lá, os arquivos enviados ficam em quarentena.

### Desenvolvimento do frontend

```bash
make infra                                   # dependências no Docker
uv sync --frozen && uv run python manage.py runserver
cd frontend && npm ci && npm run dev          # http://localhost:5173, com proxy para a API
```

## Testes e verificações

```bash
make test-unit       # backend sem infraestrutura
make test            # backend completo, com PostgreSQL, RabbitMQ, Redis, Mailpit e ClamAV reais
make frontend-check  # tipos da API, ESLint, Prettier, TypeScript, Vitest, npm audit e build
make e2e             # roteiro demonstrativo no navegador (requer a pilha no ar e o seed)
make check           # todos os gates locais da CI
```

A CI executa os mesmos gates, além da varredura de segredos (gitleaks), da análise
da imagem (Trivy) e do roteiro ponta a ponta com a pilha do Compose.

## Documentação

- [Requisitos e critérios de aceite](docs/REQUISITOS.md)
- [Arquitetura (C4, isolamento, fluxos e políticas)](docs/ARQUITETURA.md)
- [Decisões arquiteturais](docs/DECISOES.md)
- [Roteiro demonstrativo](docs/DEMONSTRACAO.md)
- [Runbooks, backup e restauração](docs/RUNBOOKS.md)
- [Situação da implementação e verificações executadas](docs/STATUS.md)
- [Como contribuir](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## Limitações reais

- **Integração com o Docusign**: o adaptador está implementado e coberto por testes
  com transporte simulado, mas nunca rodou contra o ambiente real, por falta de
  credenciais. O teste existe (`make test-external`) e falha sem elas.
- **Cópia externa e retenção de backups e WAL**: dependem do provedor de armazenamento.
  Backup, restauração, recuperação a um ponto no tempo e conferência de integridade
  dos documentos foram exercitados.
- **Idioma**: somente português do Brasil.
- **Ferramentas só na CI**: gitleaks e Trivy rodam apenas na CI, não no ambiente local.
- **Mutation testing**: roda semanalmente e sob demanda, não a cada pull request.

A situação detalhada de cada área está em [`docs/STATUS.md`](docs/STATUS.md).
