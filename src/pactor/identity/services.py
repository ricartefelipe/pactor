"""Casos de uso de organizações, associações e estrutura interna.

Cada função que altera dados de tenant espera transação vinculada à
organização (``tenant_context`` ou requisição da API) e registra auditoria.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta

from allauth.account.models import EmailAddress
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal, Role
from pactor.audit import services as audit
from pactor.core import outbox
from pactor.core.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
)
from pactor.core.tenancy import bind_user, require_tenant, tenant_context
from pactor.identity import invitations
from pactor.identity.models import (
    Department,
    DepartmentMembership,
    Invitation,
    InvitationStatus,
    Membership,
    MembershipKind,
    MembershipStatus,
    Organization,
    Team,
    TeamMembership,
    User,
)
from pactor.plans import services as plans
from pactor.plans.models import Plan

INVITATION_TTL = timedelta(days=7)


def _normalize_roles(roles: Iterable[str], kind: MembershipKind) -> list[str]:
    try:
        parsed = sorted({Role(r) for r in roles})
    except ValueError as exc:
        raise DomainValidationError("Papel desconhecido.", details={"roles": list(roles)}) from exc
    if kind == MembershipKind.EXTERNAL and parsed:
        raise DomainValidationError("Participantes externos não recebem papéis internos.")
    if kind == MembershipKind.INTERNAL and not parsed:
        raise DomainValidationError("Informe ao menos um papel.")
    return [r.value for r in parsed]


def _require_owner_for_owner_changes(
    principal: Principal, before: set[str], after: set[str]
) -> None:
    touches_owner = (Role.OWNER.value in before) != (Role.OWNER.value in after)
    if touches_owner and Role.OWNER not in principal.roles:
        raise AccessDeniedError(
            "Somente proprietários concedem ou removem o papel de proprietário."
        )


def _ensure_other_owner_remains(membership: Membership) -> None:
    others = (
        Membership.objects.filter(
            organization_id=membership.organization_id,
            status=MembershipStatus.ACTIVE,
            roles__contains=[Role.OWNER.value],
        )
        .exclude(id=membership.id)
        .exists()
    )
    if not others:
        raise ConflictError("A organização precisa manter ao menos um proprietário ativo.")


def _departments(ids: Iterable[uuid.UUID]) -> list[Department]:
    wanted = set(ids)
    found = list(Department.objects.filter(organization_id=require_tenant(), id__in=wanted))
    if len(found) != len(wanted):
        raise DomainValidationError("Departamento inexistente.")
    return found


def _teams(ids: Iterable[uuid.UUID]) -> list[Team]:
    wanted = set(ids)
    found = list(Team.objects.filter(organization_id=require_tenant(), id__in=wanted))
    if len(found) != len(wanted):
        raise DomainValidationError("Equipe inexistente.")
    return found


def create_organization(
    *, owner: User, name: str, plan: Plan, slug: str | None = None
) -> Organization:
    """Cria a organização, a associação do proprietário e o trial."""
    with transaction.atomic():
        organization = Organization.objects.create(name=name, slug=slug or slugify(name)[:60])
        with tenant_context(organization.id, user_id=owner.id):
            Membership.objects.create(
                organization=organization, user=owner, roles=[Role.OWNER.value]
            )
            plans.start_trial(plan)
            audit.record(
                None,
                action="organization.created",
                resource_type="organization",
                resource_id=organization.id,
                changes={"name": name, "plan": plan.code},
                actor_label=owner.email,
            )
    return organization


def memberships_of(user: User) -> list[Membership]:
    """Associações ativas do usuário (todas as organizações). Exige transação."""
    bind_user(user.id)
    return list(
        Membership.objects.select_related("organization")
        .filter(user=user, status=MembershipStatus.ACTIVE, organization__status="active")
        .order_by("organization__name", "organization_id")
    )


def invite(
    principal: Principal,
    *,
    email: str,
    kind: MembershipKind,
    roles: Iterable[str],
    department_ids: Iterable[uuid.UUID] = (),
    now: datetime | None = None,
) -> Invitation:
    authorize(principal, Permission.MEMBERS_MANAGE)
    normalized_email = email.strip().lower()
    normalized_roles = _normalize_roles(roles, kind)
    _require_owner_for_owner_changes(principal, set(), set(normalized_roles))
    departments = _departments(department_ids)
    organization_id = require_tenant()
    if Membership.objects.filter(
        organization_id=organization_id,
        user__email=normalized_email,
        status=MembershipStatus.ACTIVE,
    ).exists():
        raise ConflictError("Este e-mail já participa da organização.")
    if Invitation.objects.filter(
        organization_id=organization_id, email=normalized_email, status=InvitationStatus.PENDING
    ).exists():
        raise ConflictError("Já existe convite pendente para este e-mail.")
    if kind == MembershipKind.INTERNAL:
        plans.ensure_seat_available()
    else:
        plans.ensure_writable()
    invitation = Invitation.objects.create(
        organization_id=organization_id,
        email=normalized_email,
        kind=kind,
        roles=normalized_roles,
        department_ids=[d.id for d in departments],
        expires_at=(now or timezone.now()) + INVITATION_TTL,
        invited_by_id=principal.actor_id,
    )
    outbox.emit(
        "identity.invitation_created",
        {"invitation_id": str(invitation.id)},
        organization_id=organization_id,
    )
    audit.record(
        principal,
        action="invitation.created",
        resource_type="invitation",
        resource_id=invitation.id,
        changes={"email": normalized_email, "kind": kind, "roles": normalized_roles},
    )
    return invitation


def revoke_invitation(principal: Principal, invitation_id: uuid.UUID) -> Invitation:
    authorize(principal, Permission.MEMBERS_MANAGE)
    invitation = (
        Invitation.objects.select_for_update()
        .filter(organization_id=require_tenant(), id=invitation_id)
        .first()
    )
    if invitation is None:
        raise NotFoundError("Convite não encontrado.")
    if invitation.status != InvitationStatus.PENDING:
        raise ConflictError("Somente convites pendentes podem ser revogados.")
    invitation.status = InvitationStatus.REVOKED
    invitation.save(update_fields=["status"])
    audit.record(
        principal,
        action="invitation.revoked",
        resource_type="invitation",
        resource_id=invitation.id,
    )
    return invitation


def accept_invitation(user: User, token: str, *, now: datetime | None = None) -> Membership:
    """Aceita o convite. O e-mail do usuário autenticado precisa coincidir."""
    parsed = invitations.parse_token(token)
    moment = now or timezone.now()
    if not EmailAddress.objects.filter(user=user, email__iexact=user.email, verified=True).exists():
        raise DomainValidationError("Confirme seu e-mail antes de aceitar convites.")
    with tenant_context(parsed.organization_id, user_id=user.id):
        invitation = Invitation.objects.select_for_update().filter(id=parsed.invitation_id).first()
        if (
            invitation is None
            or invitation.status != InvitationStatus.PENDING
            or invitation.expires_at <= moment
            or invitation.email != user.email.lower()
        ):
            raise DomainValidationError("Convite inválido ou expirado.")
        membership, created = Membership.objects.select_for_update().get_or_create(
            organization_id=invitation.organization_id,
            user=user,
            defaults={"kind": invitation.kind, "roles": invitation.roles},
        )
        if not created:
            if membership.status == MembershipStatus.ACTIVE:
                raise ConflictError("Usuário já participa da organização.")
            membership.kind = invitation.kind
            membership.roles = invitation.roles
            membership.status = MembershipStatus.ACTIVE
            membership.revoked_at = None
            membership.revoked_by = None
            membership.revocation_reason = ""
            membership.save()
            membership.department_links.all().delete()
            membership.team_links.all().delete()
        for department in _departments(invitation.department_ids):
            DepartmentMembership.objects.create(
                organization_id=invitation.organization_id,
                membership=membership,
                department=department,
            )
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_by = user
        invitation.accepted_at = moment
        invitation.save(update_fields=["status", "accepted_by", "accepted_at"])
        audit.record(
            None,
            action="invitation.accepted",
            resource_type="membership",
            resource_id=membership.id,
            actor_label=user.email,
        )
    return membership


def _locked_membership(membership_id: uuid.UUID) -> Membership:
    membership = (
        Membership.objects.select_for_update()
        .filter(organization_id=require_tenant(), id=membership_id)
        .first()
    )
    if membership is None:
        raise NotFoundError("Associação não encontrada.")
    return membership


def update_membership(
    principal: Principal,
    membership_id: uuid.UUID,
    *,
    roles: Iterable[str],
    department_ids: Iterable[uuid.UUID],
    team_ids: Iterable[uuid.UUID],
) -> Membership:
    authorize(principal, Permission.MEMBERS_MANAGE)
    membership = _locked_membership(membership_id)
    if membership.status != MembershipStatus.ACTIVE:
        raise ConflictError("Associação revogada não pode ser alterada.")
    new_roles = _normalize_roles(roles, MembershipKind(membership.kind))
    before = set(membership.roles)
    _require_owner_for_owner_changes(principal, before, set(new_roles))
    if Role.OWNER.value in before and Role.OWNER.value not in new_roles:
        _ensure_other_owner_remains(membership)
    departments = _departments(department_ids)
    teams = _teams(team_ids)
    membership.roles = new_roles
    membership.save(update_fields=["roles"])
    membership.department_links.all().delete()
    membership.team_links.all().delete()
    DepartmentMembership.objects.bulk_create(
        DepartmentMembership(
            organization_id=membership.organization_id, membership=membership, department=d
        )
        for d in departments
    )
    TeamMembership.objects.bulk_create(
        TeamMembership(organization_id=membership.organization_id, membership=membership, team=t)
        for t in teams
    )
    audit.record(
        principal,
        action="membership.updated",
        resource_type="membership",
        resource_id=membership.id,
        changes={
            "roles": {"before": sorted(before), "after": new_roles},
            "departments": sorted(str(d.id) for d in departments),
            "teams": sorted(str(t.id) for t in teams),
        },
    )
    return membership


def revoke_membership(
    principal: Principal, membership_id: uuid.UUID, *, reason: str, now: datetime | None = None
) -> Membership:
    """Revoga o acesso. O efeito é imediato: cada requisição relê a associação."""
    authorize(principal, Permission.MEMBERS_MANAGE)
    if not reason.strip():
        raise DomainValidationError("Informe a justificativa da revogação.")
    membership = _locked_membership(membership_id)
    if membership.status != MembershipStatus.ACTIVE:
        raise ConflictError("Associação já revogada.")
    if Role.OWNER.value in membership.roles:
        _require_owner_for_owner_changes(principal, {Role.OWNER.value}, set())
        _ensure_other_owner_remains(membership)
    membership.status = MembershipStatus.REVOKED
    membership.revoked_at = now or timezone.now()
    membership.revoked_by_id = principal.actor_id
    membership.revocation_reason = reason.strip()[:300]
    membership.save(update_fields=["status", "revoked_at", "revoked_by", "revocation_reason"])
    outbox.emit(
        "identity.membership_revoked",
        {"membership_id": str(membership.id), "user_id": str(membership.user_id)},
        organization_id=membership.organization_id,
    )
    audit.record(
        principal,
        action="membership.revoked",
        resource_type="membership",
        resource_id=membership.id,
        changes={"reason": membership.revocation_reason},
    )
    return membership


def create_department(principal: Principal, *, name: str) -> Department:
    authorize(principal, Permission.MEMBERS_MANAGE)
    clean = name.strip()
    if not clean:
        raise DomainValidationError("Informe o nome do departamento.")
    organization_id = require_tenant()
    if Department.objects.filter(organization_id=organization_id, name__iexact=clean).exists():
        raise ConflictError("Departamento já existe.")
    department = Department.objects.create(organization_id=organization_id, name=clean)
    audit.record(
        principal,
        action="department.created",
        resource_type="department",
        resource_id=department.id,
        changes={"name": clean},
    )
    return department


def create_team(principal: Principal, *, name: str) -> Team:
    authorize(principal, Permission.MEMBERS_MANAGE)
    clean = name.strip()
    if not clean:
        raise DomainValidationError("Informe o nome da equipe.")
    organization_id = require_tenant()
    if Team.objects.filter(organization_id=organization_id, name__iexact=clean).exists():
        raise ConflictError("Equipe já existe.")
    team = Team.objects.create(organization_id=organization_id, name=clean)
    audit.record(
        principal,
        action="team.created",
        resource_type="team",
        resource_id=team.id,
        changes={"name": clean},
    )
    return team


def revoke_all_sessions(user: User) -> None:
    """Invalida todas as sessões do usuário (troca de senha, suspeita de abuso)."""
    User.objects.filter(id=user.id).update(session_generation=F("session_generation") + 1)
