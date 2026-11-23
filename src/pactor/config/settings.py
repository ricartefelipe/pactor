"""Configuração Django do Pactor.

Todos os valores sensíveis ou dependentes do ambiente vêm de variáveis de
ambiente (ver ``.env.example``). Não há arquivos de configuração alternativos
por ambiente: as diferenças são explícitas e validadas aqui.
"""

from __future__ import annotations

from pathlib import Path

import django_stubs_ext

from pactor.config.env import ConfigurationError, env_bool, env_int, env_list, env_str

# Torna classes genéricas do Django (ex.: ModelAdmin[Model]) subscritáveis
# em tempo de execução, conforme os stubs usados na verificação de tipos.
django_stubs_ext.monkeypatch()

BASE_DIR = Path(__file__).resolve().parents[3]

PACTOR_ENVIRONMENT = env_str("PACTOR_ENVIRONMENT", "development")
if PACTOR_ENVIRONMENT not in {"development", "test", "staging", "production"}:
    raise ConfigurationError(f"PACTOR_ENVIRONMENT inválido: {PACTOR_ENVIRONMENT!r}")
IS_PRODUCTION = PACTOR_ENVIRONMENT == "production"

DEBUG = env_bool("PACTOR_DEBUG", default=False)
if DEBUG and IS_PRODUCTION:
    raise ConfigurationError("PACTOR_DEBUG não pode ser habilitado em produção.")

SECRET_KEY = env_str("PACTOR_SECRET_KEY", None if IS_PRODUCTION else "insecure-development-key")
ALLOWED_HOSTS = env_list("PACTOR_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("PACTOR_CSRF_TRUSTED_ORIGINS", "http://localhost:8000")
PACTOR_PUBLIC_URL = env_str("PACTOR_PUBLIC_URL", "http://localhost:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "drf_spectacular",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "allauth.headless",
    "allauth.usersessions",
    "pactor.core",
    "pactor.identity",
    "pactor.plans",
    "pactor.audit",
    "pactor.notifications",
    "pactor.counterparties",
    "pactor.contracts",
]

MIDDLEWARE = [
    "pactor.core.observability.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "csp.middleware.CSPMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "allauth.usersessions.middleware.UserSessionsMiddleware",
]

ROOT_URLCONF = "pactor.config.urls"
WSGI_APPLICATION = "pactor.config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Banco de dados
# A aplicação conecta-se com um papel sem superuser, sem BYPASSRLS e sem
# propriedade das tabelas. Migrations são executadas por um processo separado
# com o papel proprietário (variáveis POSTGRES_* do serviço de migração).
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env_str("POSTGRES_HOST", "localhost"),
        "PORT": env_int("POSTGRES_PORT", 5432),
        "NAME": env_str("POSTGRES_DB", "pactor"),
        "USER": env_str("POSTGRES_USER", "pactor_app"),
        "PASSWORD": env_str("POSTGRES_PASSWORD", "" if not IS_PRODUCTION else None),
        "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"application_name": "pactor"},
        # Cada requisição é uma transação: o vínculo de tenant (set_config
        # local) e a outbox ficam atômicos com as alterações de negócio.
        "ATOMIC_REQUESTS": True,
        "TEST": {"NAME": env_str("POSTGRES_TEST_DB", "test_pactor")},
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Papel de aplicação que recebe privilégios DML nas tabelas criadas pelas
# migrations. O nome é configurável para permitir ambientes compartilhados.
PACTOR_DB_APP_ROLE = env_str("PACTOR_DB_APP_ROLE", "pactor_app")

# ---------------------------------------------------------------------------
# Cache e controles temporários
# ---------------------------------------------------------------------------
REDIS_URL = env_str("REDIS_URL", "redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "pactor",
        "TIMEOUT": 300,
    },
}

# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "identity.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "allauth.account.auth_backends.AuthenticationBackend",
]

# django-allauth: login por e-mail, confirmação obrigatória, MFA (TOTP) e
# API headless apenas para o cliente de navegador (sessão + CSRF, sem tokens
# expostos ao JavaScript).
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_PREVENT_ENUMERATION = True
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[Pactor] "
ACCOUNT_RATE_LIMITS = {
    "login": "30/m/ip",
    "login_failed": "10/m/ip,5/5m/key",
    "signup": "10/h/ip",
    # Bandit B105: "reset_password" nomeia um limite de taxa, não uma credencial.
    "reset_password": "10/m/ip,5/m/key",  # nosec B105
    "confirm_email": "1/3m/key",
}
HEADLESS_ONLY = True
HEADLESS_CLIENTS = ("browser",)
HEADLESS_FRONTEND_URLS = {
    "account_confirm_email": f"{PACTOR_PUBLIC_URL}/conta/confirmar-email/{{key}}",
    "account_reset_password_from_key": f"{PACTOR_PUBLIC_URL}/conta/redefinir-senha/{{key}}",
    "account_signup": f"{PACTOR_PUBLIC_URL}/cadastro",
}
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "Pactor"
USERSESSIONS_TRACK_ACTIVITY = True

# Plano atribuído a novas organizações (trial).
PACTOR_TRIAL_PLAN = env_str("PACTOR_TRIAL_PLAN", "profissional")

# Sessões no servidor; o cookie carrega apenas o identificador.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "pactor_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = env_int("PACTOR_SESSION_AGE_SECONDS", 8 * 3600)
SESSION_COOKIE_SECURE = env_bool("PACTOR_SECURE_COOKIES", default=IS_PRODUCTION)
CSRF_COOKIE_NAME = "pactor_csrftoken"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
CSRF_COOKIE_SAMESITE = "Lax"
# O frontend lê o token CSRF do cookie para enviá-lo no cabeçalho.
CSRF_COOKIE_HTTPONLY = False

# ---------------------------------------------------------------------------
# Segurança HTTP
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_SSL_REDIRECT = env_bool("PACTOR_SSL_REDIRECT", default=IS_PRODUCTION)
SECURE_HSTS_SECONDS = env_int("PACTOR_HSTS_SECONDS", 31536000 if IS_PRODUCTION else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = IS_PRODUCTION
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# Content Security Policy: somente recursos da própria origem.
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
        "base-uri": ["'self'"],
        "object-src": ["'none'"],
    },
}
PACTOR_ADMIN_PATH = env_str("PACTOR_ADMIN_PATH", "admin/")

# ---------------------------------------------------------------------------
# Internacionalização
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Arquivos estáticos e frontend (servidos na mesma origem da API)
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = Path(env_str("PACTOR_STATIC_ROOT", str(BASE_DIR / "var" / "static")))
FRONTEND_DIST_DIR = Path(env_str("PACTOR_FRONTEND_DIST", str(BASE_DIR / "frontend" / "dist")))
STATICFILES_DIRS = [FRONTEND_DIST_DIR] if FRONTEND_DIST_DIR.is_dir() else []
MEDIA_ROOT = Path(env_str("PACTOR_MEDIA_ROOT", str(BASE_DIR / "var" / "media")))
# Arquivos de usuários nunca são servidos diretamente: não há MEDIA_URL
# público e todo download passa por uma view autorizada.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "pactor.core.api.errors.exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env_str("PACTOR_THROTTLE_ANON", "30/minute"),
        "user": env_str("PACTOR_THROTTLE_USER", "600/minute"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Pactor API",
    "DESCRIPTION": "API da plataforma Pactor de gestão do ciclo de vida de contratos.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
}

# ---------------------------------------------------------------------------
# Celery (RabbitMQ como broker; mensagens apenas em JSON)
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env_str("CELERY_BROKER_URL", "amqp://pactor:pactor@localhost:5672/pactor")
CELERY_RESULT_BACKEND = None
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = env_int("PACTOR_TASK_TIME_LIMIT", 300)
CELERY_TASK_SOFT_TIME_LIMIT = env_int("PACTOR_TASK_SOFT_TIME_LIMIT", 240)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ROUTES = {
    "pactor.core.relay_outbox": {"queue": "default"},
}
CELERY_BEAT_SCHEDULE = {
    "relay-outbox": {"task": "pactor.core.relay_outbox", "schedule": 10.0},
}

# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------
EMAIL_HOST = env_str("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 1025)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=False)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = env_str("PACTOR_FROM_EMAIL", "Pactor <nao-responda@pactor.local>")

# ---------------------------------------------------------------------------
# Logs estruturados com redação de segredos
# ---------------------------------------------------------------------------
LOG_LEVEL = env_str("PACTOR_LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "context": {"()": "pactor.core.observability.LogContextFilter"},
        "redact": {"()": "pactor.core.observability.RedactionFilter"},
    },
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
            "rename_fields": {"asctime": "timestamp", "levelname": "level", "name": "logger"},
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["context", "redact"],
        },
    },
    "root": {"handlers": ["stdout"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        "celery": {"level": "INFO"},
    },
}
