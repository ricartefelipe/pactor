# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

FROM python:3.13.18-slim-trixie AS runtime

LABEL org.opencontainers.image.title="pactor" \
      org.opencontainers.image.description="Pactor: gestão do ciclo de vida de contratos" \
      org.opencontainers.image.source="https://github.com/ricartefelipe/pactor"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=pactor.config.settings \
    PACTOR_STATIC_ROOT=/srv/pactor/static \
    PACTOR_MEDIA_ROOT=/srv/pactor/media

COPY --from=uv /uv /usr/local/bin/uv

RUN groupadd --system --gid 10001 pactor \
 && useradd --system --uid 10001 --gid pactor --home-dir /srv/pactor --create-home pactor

WORKDIR /srv/pactor/app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY manage.py ./
COPY src ./src
RUN uv sync --frozen --no-dev \
 && PACTOR_SECRET_KEY=build-only python manage.py collectstatic --noinput \
 && mkdir -p /srv/pactor/media \
 && chown -R pactor:pactor /srv/pactor/media

USER pactor

EXPOSE 8000

CMD ["gunicorn", "pactor.config.wsgi:application", "--config", "python:pactor.config.gunicorn"]
