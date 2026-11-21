"""Configuração do Gunicorn (servidor WSGI de produção).

Workers síncronos combinam com o ORM síncrono do Django e com transações
curtas por requisição; não há código assíncrono no caminho HTTP.
"""

import os

bind = os.environ.get("PACTOR_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("PACTOR_WEB_WORKERS", "3"))
threads = 1
timeout = int(os.environ.get("PACTOR_WEB_TIMEOUT", "30"))
graceful_timeout = 20
max_requests = 2000
max_requests_jitter = 200
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = os.environ.get("PACTOR_FORWARDED_ALLOW_IPS", "127.0.0.1")
