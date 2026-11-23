# Comandos locais equivalentes aos gates da CI.
# Requer: uv, Docker (para PostgreSQL/RabbitMQ/Redis) e Node.js para o frontend.

export PACTOR_ENVIRONMENT ?= test
export POSTGRES_HOST ?= localhost
export POSTGRES_USER ?= pactor_owner
export POSTGRES_PASSWORD ?= owner-local
export PACTOR_APP_PASSWORD ?= app-local
export CELERY_BROKER_URL ?= amqp://pactor:pactor-local@localhost:5672/pactor
export REDIS_URL ?= redis://localhost:6379/0

.PHONY: mutation test-external pytest infra lock-check lint format typecheck audit bandit secrets test-unit test coverage \
        migrations-check openapi-check check frontend-check e2e pitr-drill

infra:
	docker compose up -d postgres rabbitmq redis mailpit clamav

lock-check:
	uv lock --check

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

audit:
	uv export --frozen --no-dev --no-emit-project --format requirements-txt > var/requirements-audit.txt
	uv run pip-audit --strict --disable-pip --require-hashes --requirement var/requirements-audit.txt

bandit:
	uv run bandit -c pyproject.toml -r src

secrets:
	gitleaks git --redact --verbose .

test-unit:
	uv run pytest -m unit

# Testes com worker real (marcador infra) rodam em série, após os demais.
test:
	uv run pytest -n 4 -m "not infra and not external" --cov --cov-report=
	uv run pytest -m infra --cov --cov-append --cov-report=term --cov-report=json:var/coverage.json
	uv run python scripts/check_coverage.py var/coverage.json

migrations-check:
	uv run python manage.py makemigrations --check --dry-run

# Mutation testing no núcleo de regras (ver [tool.mutmut] no pyproject).
mutation:
	rm -rf mutants
	uv run mutmut run
	uv run mutmut results

openapi-check:
	uv run python manage.py spectacular --validate --fail-on-warn --file var/openapi.yaml

# Frontend: tipos gerados a partir do esquema atual, lint, formatação, testes e build.
frontend-check: openapi-check
	cd frontend && npm ci --no-fund && npm run check:api && npm run lint && npm run format:check \
		&& npm run typecheck && npm test && npm audit && npm run build

# Roteiro ponta a ponta no navegador. Requer a pilha completa no ar
# (docker compose up --build) e os dados de demonstração (seed_demo).
e2e:
	cd frontend && npx playwright test

# Recuperação a um ponto no tempo em contêineres descartáveis (ver docs/RUNBOOKS.md).
# Uso: make pitr-drill DUMP=backups/pactor.dump
pitr-drill:
	scripts/pitr-drill.sh $(DUMP)

check: lock-check lint typecheck audit bandit migrations-check openapi-check test frontend-check

# Integração real com provedores externos (exige credenciais; ver .env.example).
test-external:
	uv run pytest -m external -v

# Execução pontual: make pytest ARGS="tests/integration/test_x.py -q"
pytest:
	uv run pytest $(ARGS)
