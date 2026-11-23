# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

# Aplicação web: compilada aqui e servida pelo Django (whitenoise) na mesma origem da API.
# O ambiente Node existe só neste estágio e não vai para a imagem final.
FROM node:24.22.2-trixie-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13.18-slim-trixie AS runtime

LABEL org.opencontainers.image.title="pactor" \
      org.opencontainers.image.description="Pactor: gestão do ciclo de vida de contratos" \
      org.opencontainers.image.source="https://github.com/ricartefelipe/pactor"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=pactor.config.settings \
    PACTOR_STATIC_ROOT=/srv/pactor/static \
    PACTOR_FRONTEND_DIST=/srv/pactor/app/frontend/dist \
    PACTOR_MEDIA_ROOT=/srv/pactor/media

# Atualizações de segurança do sistema base e remoção do pip global: a
# aplicação usa somente o ambiente criado pelo uv, e o pip da imagem base
# carrega cópias próprias (vendored) de bibliotecas que não são usadas.
# O binário do uv é montado apenas durante o build e não fica na imagem.
RUN apt-get update \
 && apt-get upgrade --yes --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall --yes pip

RUN groupadd --system --gid 10001 pactor \
 && useradd --system --uid 10001 --gid pactor --home-dir /srv/pactor --create-home pactor

WORKDIR /srv/pactor/app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev --no-install-project

COPY manage.py ./
COPY src ./src
COPY --from=frontend /build/dist ./frontend/dist
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev \
 && PACTOR_SECRET_KEY=build-only python manage.py collectstatic --noinput \
 && mkdir -p /srv/pactor/media \
 && chown -R pactor:pactor /srv/pactor/media

USER pactor

EXPOSE 8000

CMD ["gunicorn", "pactor.config.wsgi:application", "--config", "python:pactor.config.gunicorn"]
