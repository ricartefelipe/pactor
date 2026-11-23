# Contribuindo com o Pactor

## Fluxo de branches (GitFlow)

| Branch | Origem | Destino | Finalidade |
| --- | --- | --- | --- |
| `main` | — | — | Versões estáveis e liberáveis. Recebe somente `release/*` e `hotfix/*`. |
| `develop` | `main` | — | Integração da próxima versão. Recebe `feature/*` e reintegrações. |
| `feature/<descricao>` | `develop` | `develop` | Uma alteração coesa. |
| `release/<versao>` | `develop` | `main` e `develop` | Estabilização: só correções, documentação e ajustes da versão. |
| `hotfix/<versao>` | `main` | `main`, `develop` e release ativa | Correção de produção. |

Regras:

- Nunca faça commits diretamente em `main` ou `develop`; toda integração passa por pull request.
- Integrações usam commit de merge (equivalente a `--no-ff`). Squash e rebase estão desabilitados no repositório.
- Releases e hotfixes recebem tag anotada `vX.Y.Z` no commit de merge em `main`.
- Não reescreva histórico publicado, não use force push em branches compartilhadas e não use `--no-verify`.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/pt-br/v1.0.0/):
`feat(contracts): ...`, `fix(approvals): ...`, `test(...)`, `docs(...)`, `chore(...)`, `ci(...)`.
Commits pequenos, limitados à tarefa, sem arquivos temporários, segredos ou resultados locais.

## Ambiente local

```bash
cp .env.example .env
make infra                     # PostgreSQL, RabbitMQ, Redis, Mailpit e ClamAV
uv sync --frozen
```

O banco usa dois papéis: `pactor_owner` (migrations) e `pactor_app` (aplicação, sem
superuser, sem `BYPASSRLS` e sem posse das tabelas). O `Makefile` já exporta as
variáveis apontando para os containers locais.

## Verificações (mesmos gates da CI)

```bash
make lock-check     # lockfile consistente
make lint           # Ruff lint + formatação
make typecheck      # mypy estrito
make audit          # pip-audit nas dependências de produção
make bandit         # análise estática de segurança
make secrets        # gitleaks (requer binário instalado)
make migrations-check
make test-unit      # sem banco, broker, cache ou rede
make test           # suíte completa com PostgreSQL real e gates de cobertura
make frontend-check # tipos da API, ESLint, Prettier, TypeScript, Vitest, npm audit e build
make check          # todos acima, exceto segredos e o roteiro no navegador
```

### Frontend

```bash
cd frontend
npm ci
npm run gen:api     # regenera os tipos após mudar a API (requer make openapi-check antes)
npm run dev         # servidor de desenvolvimento com proxy para a API em :8000
```

`make frontend-check` executa os mesmos gates da CI: tipos da API atualizados,
ESLint, Prettier, TypeScript, Vitest, `npm audit` e build. `make e2e` roda o
roteiro demonstrativo no navegador e exige a pilha no ar (`docker compose up --build`)
e os dados de demonstração (`seed_demo`).

Mudou um serializer ou uma view? Regenere o esquema e os tipos e confira o diff de
`frontend/src/api/schema.d.ts`. Respostas sem serializer de modelo precisam de um
serializer de documentação, verificado por `tests/integration/test_api_documentation.py`.

Hooks opcionais (somente neste repositório): `git config --local core.hooksPath .githooks`.
Eles antecipam lint e testes unitários, mas não substituem a CI nem as proteções remotas.

## Pull requests

Descreva problema, mudança, validação executada e limitações. Antes de abrir o PR,
revise o diff completo, as mensagens dos commits e a origem/destino da branch.
