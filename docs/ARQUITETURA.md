# Arquitetura

## C4 — Contexto

```mermaid
flowchart LR
  user[Usuários da organização] -->|navegador| pactor[Pactor]
  ext[Participantes externos] -->|navegador, acesso restrito| pactor
  m2m[Sistemas do cliente] -->|OAuth client credentials| pactor
  pactor -->|envelopes, webhooks| sign[Provedor de assinatura]
  pactor -->|SMTP| mail[Servidor de e-mail]
```

## C4 — Containers

```mermaid
flowchart TB
  browser[SPA React servida na mesma origem] --> web
  web[web: Gunicorn + Django/DRF] --> pg[(PostgreSQL 17 + RLS)]
  web --> redis[(Redis: cache, rate limit)]
  web -->|outbox no mesmo commit| pg
  beat[scheduler: Celery beat] --> mq[(RabbitMQ)]
  relay[relay da outbox] --> mq
  mq --> wdef[worker default/notifications]
  mq --> wint[worker integrations]
  mq --> wext[worker extraction]
  wext --> clam[ClamAV]
  wint --> sign[Provedor de assinatura]
  wdef --> mail[Mailpit/SMTP]
  migrate[migrate: job único com papel proprietário] --> pg
```

## Processos

| Processo | Comando | Papel de banco |
| --- | --- | --- |
| `migrate` | `manage.py migrate` | `pactor_owner` |
| `web` | Gunicorn (`pactor.config.gunicorn`) | `pactor_app` |
| `worker*` | `celery worker` por fila | `pactor_app` |
| `scheduler` | `celery beat` | `pactor_app` |
