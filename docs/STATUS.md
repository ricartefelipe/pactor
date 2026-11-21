# Situação da implementação

Atualizado a cada integração em `develop`. "Exercitado" significa executado
contra a dependência real; "implementado" sem essa marca indica código e testes
locais apenas.

## Concluído

| Área | Itens | Evidência |
| --- | --- | --- |
| Fundação | Configuração tipada, papéis de banco, health checks, erros da API, logs e métricas | `make check`; CI no PR da fundação |

## Em andamento

- Organizações, identidade, permissões e RLS (primeira fatia vertical).

## Verificações executadas

| Data | Comando | Resultado |
| --- | --- | --- |
| 2026-11-21 | `make test` (PostgreSQL 17, RabbitMQ 4.2, Redis 8.2 locais) | 42 testes aprovados; gate `core` 100% linhas/branches |
| 2026-11-21 | `make lint typecheck audit bandit` | Sem apontamentos |

## Pendências reais

- Demais contextos de negócio, frontend e testes de navegador.
