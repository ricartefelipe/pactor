"""Agendamento periódico e roteamento de eventos apontam para tasks registradas."""

from __future__ import annotations

from django.conf import settings

from pactor.config.celery import app
from pactor.config.events import ROUTES

REQUIRED_PERIODIC = {
    "pactor.core.relay_outbox",
    "pactor.signatures.reconcile",
    "pactor.signatures.process_pending_events",
    "pactor.deadlines.daily_cycle",
    "pactor.deadlines.dispatch_alerts",
}


def _registered() -> set[str]:
    app.loader.import_default_modules()
    return set(app.tasks.keys())


def test_beat_schedule_covers_required_periodic_tasks() -> None:
    scheduled = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    assert scheduled >= REQUIRED_PERIODIC


def test_every_scheduled_task_and_event_consumer_is_registered() -> None:
    registered = _registered()
    scheduled = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    consumers = {route.consumer for routes in ROUTES.values() for route in routes}
    assert scheduled - registered == set()
    assert consumers - registered == set()


def test_consumer_queues_are_known() -> None:
    queues = {route.queue for routes in ROUTES.values() for route in routes}
    assert queues <= {"default", "notifications", "integrations", "extraction"}
