"""Pactor: gestão do ciclo de vida de contratos."""

from pactor.config.celery import app as celery_app

__all__ = ["celery_app"]
