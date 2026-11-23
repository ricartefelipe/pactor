"""Aplicação Celery.

A configuração vem das variáveis ``CELERY_*`` do Django. A criação do objeto
não abre conexões: o broker só é contatado quando um worker ou produtor atua.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pactor.config.settings")

app = Celery("pactor")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
