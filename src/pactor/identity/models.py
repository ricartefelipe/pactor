"""Usuários, organizações (tenants), estrutura interna e associações.

O usuário é global e pode participar de várias organizações; todo acesso a
dados de negócio depende de uma associação ativa à organização selecionada.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac

from pactor.access.policies import ROLE_LABELS, Role
from pactor.core.models import TenantModel


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra: object) -> User:
        if not email:
            raise ValueError("O e-mail é obrigatório.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra: object) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("e-mail", unique=True)
    full_name = models.CharField("nome completo", max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    # Incrementar este contador invalida todas as sessões existentes do
    # usuário, pois ele participa do hash de autenticação da sessão.
    session_generation = models.PositiveIntegerField(default=0)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    def __str__(self) -> str:
        return self.email

    def get_session_auth_hash(self) -> str:
        key_salt = "pactor.identity.User.get_session_auth_hash"
        return salted_hmac(
            key_salt,
            f"{self.password}:{self.session_generation}",
            algorithm="sha256",
        ).hexdigest()


class OrganizationStatus(models.TextChoices):
    ACTIVE = "active", "Ativa"
    SUSPENDED = "suspended", "Suspensa"


class Organization(models.Model):
    """Empresa contratante do Pactor: a raiz de cada tenant.

    A tabela não tem RLS: contém apenas nome e situação, e é sempre consultada
    a partir das associações do usuário autenticado.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=60, unique=True)
    legal_document = models.CharField("CNPJ", max_length=14, blank=True)
    time_zone = models.CharField("fuso horário", max_length=64, default="America/Sao_Paulo")
    status = models.CharField(
        max_length=10, choices=OrganizationStatus.choices, default=OrganizationStatus.ACTIVE
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "organização"
        verbose_name_plural = "organizações"

    def __str__(self) -> str:
        return self.name


class Department(TenantModel):
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["organization", "name"], name="department_name_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class Team(TenantModel):
    name = models.CharField(max_length=120)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["organization", "name"], name="team_name_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class MembershipKind(models.TextChoices):
    INTERNAL = "internal", "Interno"
    EXTERNAL = "external", "Participante externo"


class MembershipStatus(models.TextChoices):
    ACTIVE = "active", "Ativa"
    REVOKED = "revoked", "Revogada"


ROLE_CHOICES = [(role.value, ROLE_LABELS[role]) for role in Role]


class Membership(TenantModel):
    """Associação de um usuário a uma organização, com papéis e vínculos."""

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="memberships")
    kind = models.CharField(
        max_length=10, choices=MembershipKind.choices, default=MembershipKind.INTERNAL
    )
    roles = ArrayField(models.CharField(max_length=20, choices=ROLE_CHOICES), default=list)
    status = models.CharField(
        max_length=10, choices=MembershipStatus.choices, default=MembershipStatus.ACTIVE
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    revocation_reason = models.CharField(max_length=300, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["organization", "user"], name="membership_unique"),
            models.CheckConstraint(
                condition=models.Q(roles__contained_by=[role.value for role in Role]),
                name="membership_roles_known",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind="external") | models.Q(roles=[]),
                name="membership_external_without_roles",
            ),
            models.CheckConstraint(
                condition=models.Q(status="active", revoked_at__isnull=True)
                | models.Q(status="revoked", revoked_at__isnull=False),
                name="membership_revocation_consistent",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.organization_id}"


class DepartmentMembership(TenantModel):
    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="department_links"
    )
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="+")

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["membership", "department"], name="department_membership_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.membership_id}->{self.department_id}"


class TeamMembership(TenantModel):
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="team_links")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="member_links")

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(fields=["membership", "team"], name="team_membership_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.membership_id}->{self.team_id}"


class InvitationStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    ACCEPTED = "accepted", "Aceito"
    REVOKED = "revoked", "Revogado"


class Invitation(TenantModel):
    """Convite por e-mail.

    O segredo do link é derivado por HMAC do identificador do convite (ver
    ``identity.invitations``); nada secreto é armazenado no banco ou na outbox.
    """

    email = models.EmailField()
    kind = models.CharField(
        max_length=10, choices=MembershipKind.choices, default=MembershipKind.INTERNAL
    )
    roles = ArrayField(models.CharField(max_length=20, choices=ROLE_CHOICES), default=list)
    department_ids = ArrayField(models.UUIDField(), default=list)
    status = models.CharField(
        max_length=10, choices=InvitationStatus.choices, default=InvitationStatus.PENDING
    )
    expires_at = models.DateTimeField()
    invited_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    accepted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        constraints: ClassVar[list[models.BaseConstraint]] = [
            *TenantModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=models.Q(status="pending"),
                name="invitation_pending_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.email}@{self.organization_id}"
