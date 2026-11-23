"""Configuração do Gunicorn (servidor WSGI de produção).

Workers síncronos combinam com o ORM síncrono do Django e com transações
curtas por requisição; não há código assíncrono no caminho HTTP.
"""

import os
from pathlib import Path

from prometheus_client import multiprocess

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


def on_starting(_server: object) -> None:
    """Prepara o diretório de métricas multiprocesso, descartando dados de execuções anteriores."""
    directory = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        for stale in path.glob("*.db"):
            stale.unlink()


def child_exit(_server: object, worker: object) -> None:
    """Remove as séries de um worker encerrado (modo multiprocesso do prometheus_client)."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.mark_process_dead(getattr(worker, "pid", 0))
