# Comandos locais equivalentes aos gates da CI.
# Requer: uv, Docker (para PostgreSQL/RabbitMQ/Redis) e Node.js para o frontend.

export PACTOR_ENVIRONMENT ?= test
export POSTGRES_HOST ?= localhost
export POSTGRES_USER ?= pactor_owner
export POSTGRES_PASSWORD ?= owner-local
export PACTOR_APP_PASSWORD ?= app-local
export CELERY_BROKER_URL ?= amqp://pactor:pactor-local@localhost:5672/pactor
export REDIS_URL ?= redis://localhost:6379/0

.PHONY: infra lock-check lint format typecheck audit bandit secrets test-unit test coverage \
        migrations-check check

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

test:
	uv run pytest -n 4 --cov --cov-report=term --cov-report=json:var/coverage.json
	uv run python scripts/check_coverage.py var/coverage.json

migrations-check:
	uv run python manage.py makemigrations --check --dry-run

check: lock-check lint typecheck audit bandit migrations-check test
