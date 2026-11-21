# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e
[versionamento semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado

- Fundação do projeto: Django 5.2 LTS, DRF, Celery/RabbitMQ, Redis e PostgreSQL 17.
- Papéis de banco separados para migrations e aplicação.
- Logs estruturados em JSON com correlação e redação de segredos.
- Endpoints de liveness e readiness; métricas HTTP com cardinalidade limitada.
- Formato único de erros da API.
- Docker Compose com aplicação, workers, agendador, PostgreSQL, RabbitMQ, Redis, Mailpit e ClamAV.
- Pipeline de CI com lockfile, Ruff, mypy, pip-audit, Bandit, gitleaks, testes, gates de cobertura e análise da imagem.
