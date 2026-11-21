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
- Organizações, departamentos, equipes, associações com papéis, convites por e-mail, troca de
  organização com rotação de sessão e revogação de acesso com efeito imediato.
- Políticas de autorização puras com matriz de papéis, visibilidade por classificação e MFA
  obrigatório para permissões privilegiadas.
- Row-Level Security forçada, FKs compostas por organização e contexto de tenant local à transação.
- Planos com trial e limites de usuários, contratos ativos, armazenamento e módulos.
- Auditoria somente-inserção com redação de dados sensíveis.
- Transactional Outbox com relay recuperável, consumidores deduplicados e registro de falhas.
- Idempotência persistida por chave, escopo e fingerprint.
- Autenticação por e-mail com confirmação e MFA (django-allauth headless).
- Pipeline de CI com lockfile, Ruff, mypy, pip-audit, Bandit, gitleaks, testes, gates de cobertura e análise da imagem.
