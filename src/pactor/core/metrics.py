"""Métricas operacionais expostas em /metrics (formato Prometheus).

Labels têm cardinalidade limitada: situações e nomes de consumidores são
conjuntos fechados; nunca há identificadores de organização, contrato ou
usuário em labels. Contagens de tabelas de tenant somam todas as organizações
percorrendo cada uma com seu próprio contexto (sem desviar do RLS).
"""

from __future__ import annotations

import hmac
from collections import Counter
from collections.abc import Iterator
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Min
from django.utils import timezone
from prometheus_client import REGISTRY, CollectorRegistry, generate_latest, multiprocess
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from pactor.core.models import ConsumerFailure, ConsumerFailureStatus, OutboxEvent, OutboxStatus
from pactor.core.tenancy import tenant_context
from pactor.deadlines.models import Alert, AlertStatus
from pactor.files.models import StoredFile
from pactor.identity.models import Organization
from pactor.signatures.models import Envelope, EventOutcome, SignatureEvent

STUCK_AFTER_SECONDS = 15 * 60


class PactorCollector(Collector):
    def collect(self) -> Iterator[GaugeMetricFamily]:
        now = timezone.now()
        pending = OutboxEvent.objects.filter(
            status__in=[OutboxStatus.PENDING, OutboxStatus.CLAIMED]
        )
        oldest = pending.aggregate(oldest=Min("created_at"))["oldest"]
        outbox = GaugeMetricFamily(
            "pactor_outbox_events", "Eventos da outbox por situação.", labels=["status"]
        )
        for row in OutboxEvent.objects.values("status").annotate(n=Count("id")).order_by("status"):
            outbox.add_metric([row["status"]], row["n"])
        yield outbox
        yield GaugeMetricFamily(
            "pactor_outbox_oldest_pending_age_seconds",
            "Idade do evento pendente mais antigo da outbox.",
            value=(now - oldest).total_seconds() if oldest else 0,
        )
        failures = GaugeMetricFamily(
            "pactor_consumer_failures", "Falhas de consumidores por situação.", labels=["status"]
        )
        for row in (
            ConsumerFailure.objects.values("status").annotate(n=Count("id")).order_by("status")
        ):
            failures.add_metric([row["status"]], row["n"])
        yield failures
        yield GaugeMetricFamily(
            "pactor_consumer_stuck",
            "Consumidores em retentativa há mais de 15 minutos.",
            value=ConsumerFailure.objects.filter(
                status=ConsumerFailureStatus.RETRYING,
                updated_at__lt=now - timedelta(seconds=STUCK_AFTER_SECONDS),
            ).count(),
        )
        yield GaugeMetricFamily(
            "pactor_signature_events_pending",
            "Eventos de assinatura recebidos e ainda não aplicados.",
            value=SignatureEvent.objects.filter(outcome=EventOutcome.PENDING).count(),
        )
        files: Counter[str] = Counter()
        envelopes: Counter[str] = Counter()
        overdue_alerts = 0
        for organization_id in Organization.objects.order_by("id").values_list("id", flat=True):
            with tenant_context(organization_id):
                files.update(
                    {
                        r["status"]: r["n"]
                        for r in StoredFile.objects.values("status")
                        .annotate(n=Count("id"))
                        .order_by()
                    }
                )
                envelopes.update(
                    {
                        r["state"]: r["n"]
                        for r in Envelope.objects.values("state").annotate(n=Count("id")).order_by()
                    }
                )
                overdue_alerts += Alert.objects.filter(
                    status=AlertStatus.PENDING, fire_at__lt=now
                ).count()
        files_metric = GaugeMetricFamily(
            "pactor_files", "Arquivos por situação do pipeline.", labels=["status"]
        )
        for status, value in sorted(files.items()):
            files_metric.add_metric([status], value)
        yield files_metric
        envelope_metric = GaugeMetricFamily(
            "pactor_envelopes", "Envelopes de assinatura por situação.", labels=["state"]
        )
        for state, value in sorted(envelopes.items()):
            envelope_metric.add_metric([state], value)
        yield envelope_metric
        yield GaugeMetricFamily(
            "pactor_alerts_overdue", "Alertas vencidos ainda não disparados.", value=overdue_alerts
        )


# Registro das métricas de negócio, coletadas sob demanda. Sem ``auto_describe``: com ele,
# o registro chamaria ``collect()`` (e consultaria o banco) já no import do módulo.
_BUSINESS_REGISTRY = CollectorRegistry()
_BUSINESS_REGISTRY.register(PactorCollector())


def exposition() -> bytes:
    """Métricas HTTP (de todos os workers, em modo multiprocesso) e métricas de negócio."""
    if settings.PROMETHEUS_MULTIPROC_DIR:
        http_registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(http_registry, path=settings.PROMETHEUS_MULTIPROC_DIR)
    else:
        http_registry = REGISTRY
    return generate_latest(http_registry) + generate_latest(_BUSINESS_REGISTRY)


def authorized(header: str) -> bool:
    expected = settings.PACTOR_METRICS_TOKEN
    given = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    return bool(expected) and bool(given) and hmac.compare_digest(given, expected)
