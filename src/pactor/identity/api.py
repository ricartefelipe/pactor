"""API de identidade, organizações e estrutura interna."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.conf import settings
from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from pactor.access.api import TenantAPIView, TenantViewSet
from pactor.access.policies import ROLE_LABELS, Decision, Permission, decide
from pactor.access.principal import (
    ACTIVE_ORGANIZATION_SESSION_KEY,
    load_active_membership,
    principal_for_membership,
)
from pactor.core.api.pagination import StablePagination
from pactor.core.errors import AccessDeniedError, NotFoundError
from pactor.identity import services
from pactor.identity.mfa import has_mfa_enrolled, session_used_mfa
from pactor.identity.models import (
    PERMISSION_CHOICES,
    ROLE_CHOICES,
    Department,
    Invitation,
    Membership,
    MembershipKind,
    Team,
    User,
)
from pactor.plans import services as plans
from pactor.plans.models import Plan


def _user(request: Request) -> User:
    user = request.user
    if not isinstance(user, User):
        raise AccessDeniedError("Autenticação obrigatória.")
    return user


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
class OrganizationSummarySerializer(serializers.Serializer[Membership]):
    id = serializers.UUIDField(source="organization_id")
    name = serializers.CharField(source="organization.name")
    slug = serializers.CharField(source="organization.slug")
    kind = serializers.ChoiceField(choices=MembershipKind.choices)
    roles = serializers.ListField(child=serializers.ChoiceField(choices=ROLE_CHOICES))


class MembershipSerializer(serializers.ModelSerializer[Membership]):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    department_ids = serializers.SerializerMethodField()
    team_ids = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = (
            "id",
            "email",
            "full_name",
            "kind",
            "roles",
            "status",
            "department_ids",
            "team_ids",
            "created_at",
            "revoked_at",
            "revocation_reason",
        )
        read_only_fields = fields

    def get_department_ids(self, obj: Membership) -> list[str]:
        return sorted(str(link.department_id) for link in obj.department_links.all())

    def get_team_ids(self, obj: Membership) -> list[str]:
        return sorted(str(link.team_id) for link in obj.team_links.all())


class MembershipUpdateSerializer(serializers.Serializer[Membership]):
    roles = serializers.ListField(child=serializers.ChoiceField(choices=ROLE_CHOICES))
    department_ids = serializers.ListField(child=serializers.UUIDField(), default=list)
    team_ids = serializers.ListField(child=serializers.UUIDField(), default=list)


class RevokeSerializer(serializers.Serializer[Membership]):
    reason = serializers.CharField(max_length=300)


class InvitationSerializer(serializers.ModelSerializer[Invitation]):
    class Meta:
        model = Invitation
        fields = (
            "id",
            "email",
            "kind",
            "roles",
            "department_ids",
            "status",
            "expires_at",
            "created_at",
        )
        read_only_fields = ("id", "status", "expires_at", "created_at")


class AcceptInvitationSerializer(serializers.Serializer[Membership]):
    token = serializers.CharField(max_length=200)


class NamedSerializer(serializers.Serializer[Department | Team]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(max_length=120)


class SwitchOrganizationSerializer(serializers.Serializer[Membership]):
    organization_id = serializers.UUIDField()


class CreateOrganizationSerializer(serializers.Serializer[Membership]):
    name = serializers.CharField(max_length=200)


class ActiveOrganizationSerializer(serializers.Serializer[None]):
    """Documentação da organização ativa e do que o usuário pode fazer nela."""

    organization_id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=MembershipKind.choices)
    roles = serializers.ListField(child=serializers.ChoiceField(choices=ROLE_CHOICES))
    permissions = serializers.ListField(child=serializers.ChoiceField(choices=PERMISSION_CHOICES))
    mfa_required_for = serializers.ListField(
        child=serializers.ChoiceField(choices=PERMISSION_CHOICES)
    )


class MeSerializer(serializers.Serializer[None]):
    """Documentação da resposta de /me e das trocas de organização."""

    id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    mfa_enrolled = serializers.BooleanField()
    mfa_satisfied = serializers.BooleanField()
    organizations = OrganizationSummarySerializer(many=True)
    active_organization = ActiveOrganizationSerializer(allow_null=True)
    role_labels = serializers.DictField(child=serializers.CharField())


class DirectoryEntrySerializer(serializers.Serializer[None]):
    user_id = serializers.UUIDField()
    full_name = serializers.CharField()
    email = serializers.EmailField()


class AcceptedInvitationSerializer(serializers.Serializer[None]):
    organization_id = serializers.UUIDField()


# ---------------------------------------------------------------------------
# Views sem organização ativa
# ---------------------------------------------------------------------------
def _me_payload(request: Request, user: User) -> dict[str, Any]:
    memberships = services.memberships_of(user)
    active_raw = request.session.get(ACTIVE_ORGANIZATION_SESSION_KEY)
    active: dict[str, Any] | None = None
    if active_raw:
        membership = next((m for m in memberships if str(m.organization_id) == active_raw), None)
        if membership is not None:
            membership = load_active_membership(user, membership.organization_id)
        if membership is not None:
            principal = principal_for_membership(
                membership, mfa_satisfied=session_used_mfa(request, user)
            )
            active = {
                "organization_id": str(principal.organization_id),
                "name": membership.organization.name,
                "kind": principal.kind.value,
                "roles": sorted(r.value for r in principal.roles),
                "permissions": sorted(
                    p.value for p in Permission if decide(principal, p) is Decision.ALLOW
                ),
                "mfa_required_for": sorted(
                    p.value for p in Permission if decide(principal, p) is Decision.MFA_REQUIRED
                ),
            }
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "mfa_enrolled": has_mfa_enrolled(user),
        "mfa_satisfied": session_used_mfa(request, user),
        "organizations": [OrganizationSummarySerializer(m).data for m in memberships],
        "active_organization": active,
        "role_labels": {role.value: label for role, label in ROLE_LABELS.items()},
    }


class MeView(APIView):
    @extend_schema(responses=MeSerializer)
    def get(self, request: Request) -> Response:
        return Response(_me_payload(request, _user(request)))


class ActiveOrganizationView(APIView):
    @extend_schema(request=SwitchOrganizationSerializer, responses=MeSerializer)
    def put(self, request: Request) -> Response:
        user = _user(request)
        serializer = SwitchOrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization_id: uuid.UUID = serializer.validated_data["organization_id"]
        if load_active_membership(user, organization_id) is None:
            # Mesma resposta para organização inexistente ou sem associação.
            raise NotFoundError("Organização não encontrada.")
        # Nova chave de sessão a cada troca: impede reaproveitar uma sessão
        # capturada antes da troca e força o frontend a descartar caches.
        request.session.cycle_key()
        request.session[ACTIVE_ORGANIZATION_SESSION_KEY] = str(organization_id)
        return Response(_me_payload(request, user))


class OrganizationCreateView(APIView):
    @extend_schema(request=CreateOrganizationSerializer, responses={201: MeSerializer})
    def post(self, request: Request) -> Response:
        user = _user(request)
        serializer = CreateOrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = Plan.objects.get(code=settings.PACTOR_TRIAL_PLAN)
        organization = services.create_organization(
            owner=user,
            name=serializer.validated_data["name"],
            plan=plan,
            slug=f"org-{uuid.uuid4().hex[:12]}",
        )
        request.session.cycle_key()
        request.session[ACTIVE_ORGANIZATION_SESSION_KEY] = str(organization.id)
        return Response(_me_payload(request, user), status=status.HTTP_201_CREATED)


class AcceptInvitationView(APIView):
    @extend_schema(request=AcceptInvitationSerializer, responses=AcceptedInvitationSerializer)
    def post(self, request: Request) -> Response:
        user = _user(request)
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.accept_invitation(user, serializer.validated_data["token"])
        return Response({"organization_id": str(membership.organization_id)})


# ---------------------------------------------------------------------------
# Views da organização ativa
# ---------------------------------------------------------------------------
class MemberViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, TenantViewSet):
    serializer_class = MembershipSerializer
    pagination_class = StablePagination
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": Permission.MEMBERS_MANAGE,
        "retrieve": Permission.MEMBERS_MANAGE,
        "partial_update": Permission.MEMBERS_MANAGE,
        "revoke": Permission.MEMBERS_MANAGE,
        "directory": None,
    }

    def get_queryset(self) -> QuerySet[Membership]:
        return (
            Membership.objects.filter(organization_id=self.principal.organization_id)
            .select_related("user")
            .prefetch_related("department_links", "team_links")
        )

    @extend_schema(request=MembershipUpdateSerializer, responses=MembershipSerializer)
    def partial_update(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = MembershipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.update_membership(
            self.principal,
            pk,
            roles=serializer.validated_data["roles"],
            department_ids=serializer.validated_data["department_ids"],
            team_ids=serializer.validated_data["team_ids"],
        )
        return Response(MembershipSerializer(self.get_queryset().get(id=membership.id)).data)

    @extend_schema(request=RevokeSerializer, responses=MembershipSerializer)
    @action(detail=True, methods=["post"])
    def revoke(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = RevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.revoke_membership(
            self.principal, pk, reason=serializer.validated_data["reason"]
        )
        return Response(MembershipSerializer(self.get_queryset().get(id=membership.id)).data)

    @extend_schema(responses=DirectoryEntrySerializer(many=True))
    @action(detail=False, methods=["get"], pagination_class=None)
    def directory(self, _request: Request) -> Response:
        """Lista mínima (id, nome, e-mail) de membros internos ativos.

        Usada para escolher responsáveis e aprovadores; não expõe papéis nem
        vínculos e não está disponível para participantes externos.
        """
        if self.principal.is_external:
            raise AccessDeniedError("Participantes externos não acessam o diretório.")
        members = (
            self.get_queryset()
            .filter(status="active", kind=MembershipKind.INTERNAL)
            .order_by("user__full_name", "user__email", "id")
        )
        return Response(
            [
                {"user_id": str(m.user_id), "full_name": m.user.full_name, "email": m.user.email}
                for m in members
            ]
        )


class InvitationViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, TenantViewSet):
    serializer_class = InvitationSerializer
    pagination_class = StablePagination
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": Permission.MEMBERS_MANAGE,
        "create": Permission.MEMBERS_MANAGE,
        "revoke": Permission.MEMBERS_MANAGE,
    }

    def get_queryset(self) -> QuerySet[Invitation]:
        return Invitation.objects.filter(organization_id=self.principal.organization_id)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = InvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invitation = services.invite(
            self.principal,
            email=data["email"],
            kind=MembershipKind(data.get("kind", MembershipKind.INTERNAL)),
            roles=data.get("roles", []),
            department_ids=data.get("department_ids", []),
        )
        return Response(InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=InvitationSerializer)
    @action(detail=True, methods=["post"])
    def revoke(self, _request: Request, pk: uuid.UUID) -> Response:
        invitation = services.revoke_invitation(self.principal, pk)
        return Response(InvitationSerializer(invitation).data)


class DepartmentViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, TenantViewSet):
    serializer_class = NamedSerializer
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": None,
        "create": Permission.MEMBERS_MANAGE,
    }

    def get_queryset(self) -> QuerySet[Department]:
        if self.principal.is_external:
            return Department.objects.none()
        return Department.objects.filter(organization_id=self.principal.organization_id).order_by(
            "name", "id"
        )

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = NamedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        department = services.create_department(
            self.principal, name=serializer.validated_data["name"]
        )
        return Response(NamedSerializer(department).data, status=status.HTTP_201_CREATED)


class TeamViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, TenantViewSet):
    serializer_class = NamedSerializer
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": None,
        "create": Permission.MEMBERS_MANAGE,
    }

    def get_queryset(self) -> QuerySet[Team]:
        if self.principal.is_external:
            return Team.objects.none()
        return Team.objects.filter(organization_id=self.principal.organization_id).order_by(
            "name", "id"
        )

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = NamedSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team = services.create_team(self.principal, name=serializer.validated_data["name"])
        return Response(NamedSerializer(team).data, status=status.HTTP_201_CREATED)


class PlanSerializer(serializers.Serializer[Plan]):
    code = serializers.CharField()
    name = serializers.CharField()
    monthly_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    max_users = serializers.IntegerField()
    max_active_contracts = serializers.IntegerField()
    max_storage_bytes = serializers.IntegerField()
    modules = serializers.ListField(child=serializers.CharField())


class UsageSerializer(serializers.Serializer[None]):
    users = serializers.IntegerField()


class SubscriptionSerializer(serializers.Serializer[None]):
    plan = PlanSerializer()
    status = serializers.CharField()
    trial_ends_at = serializers.DateTimeField(allow_null=True)
    usage = UsageSerializer()


class SubscriptionView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": Permission.BILLING_MANAGE}

    @extend_schema(responses=SubscriptionSerializer)
    def get(self, _request: Request) -> Response:
        subscription = plans.current_subscription()
        plan = subscription.plan
        return Response(
            {
                "plan": {
                    "code": plan.code,
                    "name": plan.name,
                    "monthly_price": str(plan.monthly_price),
                    "max_users": plan.max_users,
                    "max_active_contracts": plan.max_active_contracts,
                    "max_storage_bytes": plan.max_storage_bytes,
                    "modules": plan.modules,
                },
                "status": subscription.status,
                "trial_ends_at": subscription.trial_ends_at,
                "usage": {"users": plans.seats_in_use()},
            }
        )
