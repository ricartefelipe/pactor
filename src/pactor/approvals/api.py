"""API de workflows, rodadas de aprovação, votos e delegações."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.db.models import Q, QuerySet
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantViewSet
from pactor.access.policies import Permission
from pactor.approvals import services
from pactor.approvals.domain import Decision
from pactor.approvals.models import (
    ApprovalRound,
    ApprovalStep,
    Delegation,
    StepApprover,
    Vote,
    WorkflowDefinition,
)
from pactor.core.api.filters import parse_filters
from pactor.core.api.pagination import StablePagination
from pactor.core.errors import NotFoundError


class WorkflowSerializer(serializers.ModelSerializer[WorkflowDefinition]):
    class Meta:
        model = WorkflowDefinition
        fields = (
            "id",
            "name",
            "version",
            "rules",
            "prohibit_self_approval",
            "is_active",
            "created_at",
        )
        read_only_fields = ("id", "version", "is_active", "created_at")


class ApproverSerializer(serializers.ModelSerializer[StepApprover]):
    class Meta:
        model = StepApprover
        fields = ("user_id", "active", "replaced_reason")
        read_only_fields = fields


class StepSerializer(serializers.ModelSerializer[ApprovalStep]):
    approvers = ApproverSerializer(many=True, read_only=True)

    class Meta:
        model = ApprovalStep
        fields = ("key", "name", "order", "quorum", "state", "approvers")
        read_only_fields = fields


class VoteSerializer(serializers.ModelSerializer[Vote]):
    step_key = serializers.CharField(source="step.key", read_only=True)

    class Meta:
        model = Vote
        fields = (
            "id",
            "step_key",
            "approver_id",
            "cast_by_id",
            "delegation_id",
            "decision",
            "comment",
            "revision_id",
            "fingerprint",
            "cast_at",
        )
        read_only_fields = fields


class RoundSerializer(serializers.ModelSerializer[ApprovalRound]):
    steps = StepSerializer(many=True, read_only=True)
    votes = VoteSerializer(many=True, read_only=True)
    revision_number = serializers.IntegerField(source="revision.number", read_only=True)
    contract_reference = serializers.CharField(source="contract.reference", read_only=True)
    contract_title = serializers.CharField(source="contract.title", read_only=True)

    class Meta:
        model = ApprovalRound
        fields: tuple[str, ...] = (
            "id",
            "contract_id",
            "contract_reference",
            "contract_title",
            "revision_id",
            "revision_number",
            "fingerprint",
            "workflow_id",
            "workflow_version",
            "rule_index",
            "rule_snapshot",
            "prohibit_self_approval",
            "status",
            "opened_by_id",
            "created_at",
            "closed_at",
            "close_reason",
            "steps",
            "votes",
        )
        read_only_fields: tuple[str, ...] = fields


class InboxRoundSerializer(RoundSerializer):
    """Rodada para quem vota: inclui o conteúdo exato da revisão submetida."""

    revision_content = serializers.CharField(source="revision.content", read_only=True)
    revision_metadata = serializers.JSONField(source="revision.metadata", read_only=True)

    class Meta(RoundSerializer.Meta):
        fields = (*RoundSerializer.Meta.fields, "revision_content", "revision_metadata")
        read_only_fields = fields


class SubmitSerializer(serializers.Serializer[ApprovalRound]):
    contract_id = serializers.UUIDField()
    expected_fingerprint = serializers.CharField(min_length=64, max_length=64)


class VoteCreateSerializer(serializers.Serializer[Vote]):
    step_key = serializers.CharField(max_length=40)
    decision = serializers.ChoiceField(choices=[d.value for d in Decision])
    expected_fingerprint = serializers.CharField(min_length=64, max_length=64)
    on_behalf_of = serializers.UUIDField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, default="", max_length=2000)


class ReplaceApproverSerializer(serializers.Serializer[StepApprover]):
    step_key = serializers.CharField(max_length=40)
    old_user_id = serializers.UUIDField()
    new_user_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=300)


class RoundFilterSerializer(serializers.Serializer[None]):
    contract_id = serializers.UUIDField()


class DelegationSerializer(serializers.ModelSerializer[Delegation]):
    class Meta:
        model = Delegation
        fields = (
            "id",
            "delegator_id",
            "delegate_id",
            "starts_at",
            "ends_at",
            "reason",
            "revoked_at",
            "created_at",
        )
        read_only_fields = ("id", "delegator_id", "revoked_at", "created_at")

    delegate_id = serializers.UUIDField()


class WorkflowViewSet(mixins.ListModelMixin, TenantViewSet):
    serializer_class = WorkflowSerializer
    pagination_class = StablePagination
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": Permission.WORKFLOWS_MANAGE,
        "create": Permission.WORKFLOWS_MANAGE,
    }

    def get_queryset(self) -> QuerySet[WorkflowDefinition]:
        return WorkflowDefinition.objects.filter(organization_id=self.principal.organization_id)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = WorkflowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workflow = services.publish_workflow(
            self.principal,
            name=serializer.validated_data["name"],
            rules=serializer.validated_data["rules"],
            prohibit_self_approval=serializer.validated_data.get("prohibit_self_approval", True),
        )
        return Response(WorkflowSerializer(workflow).data, status=status.HTTP_201_CREATED)


class ApprovalRoundViewSet(mixins.ListModelMixin, TenantViewSet):
    serializer_class = RoundSerializer
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        # Listagem por contrato depende da visibilidade do contrato.
        "list": None,
        "retrieve": None,
        "submit": Permission.CONTRACTS_SUBMIT,
        "inbox": Permission.APPROVALS_VOTE,
        "vote": Permission.APPROVALS_VOTE,
        "replace_approver": Permission.WORKFLOWS_MANAGE,
    }

    def get_queryset(self) -> QuerySet[ApprovalRound]:
        filters = parse_filters(self.request, RoundFilterSerializer)
        return services.rounds_of_contract(self.principal, filters["contract_id"])

    @extend_schema(responses=RoundSerializer)
    def retrieve(self, _request: Request, pk: uuid.UUID) -> Response:
        head = ApprovalRound.objects.filter(
            organization_id=self.principal.organization_id, id=pk
        ).first()
        if head is None:
            raise NotFoundError("Rodada não encontrada.")
        round_ = services.rounds_of_contract(self.principal, head.contract_id).get(id=head.id)
        return Response(RoundSerializer(round_).data)

    @extend_schema(request=SubmitSerializer, responses={201: RoundSerializer})
    @action(detail=False, methods=["post"])
    def submit(self, request: Request) -> Response:
        def operation() -> Response:
            serializer = SubmitSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            round_ = services.submit_for_approval(
                self.principal,
                serializer.validated_data["contract_id"],
                expected_fingerprint=serializer.validated_data["expected_fingerprint"],
            )
            fresh = services.rounds_of_contract(self.principal, round_.contract_id).get(
                id=round_.id
            )
            return Response(RoundSerializer(fresh).data, status=status.HTTP_201_CREATED)

        return self.idempotent(request, "approvals.submit", operation)

    @extend_schema(responses=InboxRoundSerializer(many=True))
    @action(detail=False, methods=["get"])
    def inbox(self, _request: Request) -> Response:
        return Response(InboxRoundSerializer(services.inbox(self.principal), many=True).data)

    @extend_schema(request=VoteCreateSerializer, responses={201: VoteSerializer})
    @action(detail=True, methods=["post"])
    def vote(self, request: Request, pk: uuid.UUID) -> Response:
        def operation() -> Response:
            serializer = VoteCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            vote = services.cast_vote(
                self.principal,
                pk,
                step_key=data["step_key"],
                decision=Decision(data["decision"]),
                expected_fingerprint=data["expected_fingerprint"],
                on_behalf_of=data.get("on_behalf_of"),
                comment=data["comment"],
            )
            return Response(VoteSerializer(vote).data, status=status.HTTP_201_CREATED)

        return self.idempotent(request, "approvals.vote", operation)

    @extend_schema(request=ReplaceApproverSerializer, responses=ApproverSerializer)
    @action(detail=True, methods=["post"], url_path="replace-approver")
    def replace_approver(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = ReplaceApproverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        slot = services.replace_approver(self.principal, pk, **serializer.validated_data)
        return Response(ApproverSerializer(slot).data)


class DelegationViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, TenantViewSet):
    serializer_class = DelegationSerializer
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        "list": Permission.APPROVALS_VOTE,
        "create": Permission.APPROVALS_VOTE,
        "revoke": Permission.APPROVALS_VOTE,
    }

    def get_queryset(self) -> QuerySet[Delegation]:
        actor = self.principal.actor_id
        return (
            Delegation.objects.filter(organization_id=self.principal.organization_id)
            .filter(Q(delegator_id=actor) | Q(delegate_id=actor))
            .order_by("-starts_at", "id")
        )

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = DelegationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        delegation = services.create_delegation(self.principal, **serializer.validated_data)
        return Response(DelegationSerializer(delegation).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=DelegationSerializer)
    @action(detail=True, methods=["post"])
    def revoke(self, _request: Request, pk: uuid.UUID) -> Response:
        return Response(DelegationSerializer(services.revoke_delegation(self.principal, pk)).data)
