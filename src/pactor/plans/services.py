"""Controle efetivo dos limites do plano da organização."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from pactor.core.errors import PlanLimitExceededError
from pactor.core.tenancy import require_tenant
from pactor.identity.models import Invitation, InvitationStatus, Membership, MembershipStatus
from pactor.plans.models import Module, Plan, Subscription, SubscriptionStatus

TRIAL_DAYS = 14


def start_trial(plan: Plan, *, now: datetime | None = None) -> Subscription:
    moment = now or timezone.now()
    return Subscription.objects.create(
        organization_id=require_tenant(),
        plan=plan,
        status=SubscriptionStatus.TRIALING,
        trial_ends_at=moment + timedelta(days=TRIAL_DAYS),
    )


def current_subscription() -> Subscription:
    subscription = (
        Subscription.objects.select_related("plan").filter(organization_id=require_tenant()).first()
    )
    if subscription is None:
        raise PlanLimitExceededError("Organização sem assinatura do Pactor.")
    return subscription


def ensure_writable(*, now: datetime | None = None) -> Subscription:
    """Bloqueia escrita quando o trial terminou ou a assinatura foi cancelada."""
    subscription = current_subscription()
    moment = now or timezone.now()
    if subscription.status == SubscriptionStatus.CANCELED:
        raise PlanLimitExceededError("Assinatura cancelada: somente leitura e exportação.")
    if (
        subscription.status == SubscriptionStatus.TRIALING
        and subscription.trial_ends_at is not None
        and subscription.trial_ends_at <= moment
    ):
        raise PlanLimitExceededError(
            "Período de avaliação encerrado: somente leitura e exportação."
        )
    return subscription


def ensure_module(module: Module) -> None:
    subscription = ensure_writable()
    if module.value not in subscription.plan.modules:
        raise PlanLimitExceededError(
            "Módulo não incluído no plano.", details={"module": module.value}
        )


def seats_in_use() -> int:
    organization_id = require_tenant()
    members = Membership.objects.filter(
        organization_id=organization_id, status=MembershipStatus.ACTIVE, kind="internal"
    ).count()
    pending = Invitation.objects.filter(
        organization_id=organization_id, status=InvitationStatus.PENDING, kind="internal"
    ).count()
    return members + pending


def ensure_seat_available() -> None:
    subscription = ensure_writable()
    if seats_in_use() >= subscription.plan.max_users:
        raise PlanLimitExceededError(
            "Limite de usuários do plano atingido.",
            details={"limit": subscription.plan.max_users},
        )


def ensure_capacity(
    *, active_contracts: int | None = None, storage_bytes: int | None = None
) -> None:
    """Verifica limites de contratos ativos e armazenamento após a operação pretendida."""
    plan = ensure_writable().plan
    if active_contracts is not None and active_contracts > plan.max_active_contracts:
        raise PlanLimitExceededError(
            "Limite de contratos ativos do plano atingido.",
            details={"limit": plan.max_active_contracts},
        )
    if storage_bytes is not None and storage_bytes > plan.max_storage_bytes:
        raise PlanLimitExceededError(
            "Limite de armazenamento do plano atingido.",
            details={"limit": plan.max_storage_bytes},
        )
