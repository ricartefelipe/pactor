# Pactor

Plataforma SaaS B2B multitenant para gestão do ciclo de vida de contratos: da
solicitação e elaboração à aprovação, assinatura, execução, renovação e encerramento.

## Para quem

Empresas de serviços, departamentos administrativos e jurídicos, consultorias e
organizações que administram contratos com clientes e fornecedores.

O Pactor organiza informações e processos. Ele não interpreta cláusulas nem
garante validade jurídica ou conformidade dos documentos.

## Arquitetura

Monólito modular em Django com processos separados para web, workers e agendador.
Detalhes em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) e decisões em
[`docs/DECISOES.md`](docs/DECISOES.md).

| Componente | Tecnologia |
| --- | --- |
| Backend | Python 3.13, Django 5.2 LTS, Django REST Framework |
| Banco | PostgreSQL 17 com Row-Level Security |
| Filas | Celery com RabbitMQ |
| Cache e controles temporários | Redis |
| Servidor | Gunicorn |

## Executando

```bash
cp .env.example .env
docker compose up --build
```

A aplicação fica em `http://localhost:8000`. Health checks: `/health/live` e `/health/ready`.

## Testes

```bash
make infra          # dependências locais
make test-unit      # sem infraestrutura
make test           # suíte completa com gates de cobertura
make check          # todos os gates locais da CI
```

## Documentação

- [Requisitos e critérios de aceite](docs/REQUISITOS.md)
- [Situação da implementação](docs/STATUS.md)
- [Como contribuir](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## Limitações atuais

Consulte [`docs/STATUS.md`](docs/STATUS.md) para o que já está implementado e as pendências.
