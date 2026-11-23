"""API de contratos, rascunhos, revisões, comparação, comentários e compartilhamento."""

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
from pactor.access.policies import Confidentiality, Permission, can_edit_contract
from pactor.contracts import selectors, services
from pactor.contracts.domain import ContractType, PartyRole
from pactor.contracts.models import (
    CONFIDENTIALITY_CHOICES,
    CONTRACT_TYPE_CHOICES,
    DRAFTING_CHOICES,
    PARTY_ROLE_CHOICES,
    Comment,
    Contract,
    ContractDraft,
    ContractGrant,
    Revision,
)
from pactor.contracts.visibility import facts_for
from pactor.core.api.filters import parse_filters
from pactor.core.api.pagination import StablePagination


class RevisionSummarySerializer(serializers.ModelSerializer[Revision]):
    author_name = serializers.CharField(source="author.full_name", read_only=True)

    class Meta:
        model = Revision
        fields: tuple[str, ...] = (
            "id",
            "number",
            "source",
            "content_sha256",
            "fingerprint",
            "summary",
            "author_id",
            "author_name",
            "created_at",
        )
        read_only_fields: tuple[str, ...] = fields


class RevisionDetailSerializer(RevisionSummarySerializer):
    class Meta(RevisionSummarySerializer.Meta):
        fields = (
            *RevisionSummarySerializer.Meta.fields,
            "content",
            "metadata",
            "attachments",
            "previous_id",
            "template_version_id",
            "generation_data",
        )
        read_only_fields = fields


class ContractSerializer(serializers.ModelSerializer[Contract]):
    counterparty_name = serializers.CharField(source="counterparty.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    owner_name = serializers.CharField(source="owner.full_name", read_only=True)
    current_revision = RevisionSummarySerializer(read_only=True)
    approved_revision_id = serializers.UUIDField(read_only=True)
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Contract
        fields = (
            "id",
            "reference",
            "title",
            "contract_type",
            "counterparty_id",
            "counterparty_name",
            "organization_role",
            "counterparty_role",
            "department_id",
            "department_name",
            "owner_id",
            "owner_name",
            "requester_id",
            "confidentiality",
            "classification",
            "tags",
            "total_value",
            "currency",
            "planned_start",
            "planned_end",
            "drafting_status",
            "current_revision",
            "approved_revision_id",
            "approved_fingerprint",
            "parent_id",
            "version",
            "created_at",
            "updated_at",
            "can_edit",
        )
        read_only_fields = fields

    def get_can_edit(self, obj: Contract) -> bool:
        view = self.context.get("view")
        principal = getattr(view, "principal", None)
        return principal is not None and can_edit_contract(principal, facts_for(obj))


class ContractCreateSerializer(serializers.Serializer[Contract]):
    title = serializers.CharField(max_length=200)
    contract_type = serializers.ChoiceField(choices=CONTRACT_TYPE_CHOICES)
    counterparty_id = serializers.UUIDField()
    organization_role = serializers.ChoiceField(choices=PARTY_ROLE_CHOICES)
    counterparty_role = serializers.ChoiceField(choices=PARTY_ROLE_CHOICES)
    owner_id = serializers.UUIDField()
    department_id = serializers.UUIDField(required=False, allow_null=True)
    requester_id = serializers.UUIDField(required=False, allow_null=True)
    confidentiality = serializers.ChoiceField(
        choices=CONFIDENTIALITY_CHOICES, default=Confidentiality.INTERNAL.value
    )
    classification = serializers.CharField(
        max_length=60, required=False, allow_blank=True, default=""
    )
    tags = serializers.ListField(
        child=serializers.CharField(max_length=40), required=False, default=list
    )
    total_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    planned_start = serializers.DateField(required=False, allow_null=True)
    planned_end = serializers.DateField(required=False, allow_null=True)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    initial_content = serializers.CharField(
        required=False, allow_blank=True, default="", trim_whitespace=False
    )


class ContractUpdateSerializer(serializers.Serializer[Contract]):
    expected_version = serializers.IntegerField(min_value=1)
    title = serializers.CharField(max_length=200, required=False)
    department_id = serializers.UUIDField(required=False, allow_null=True)
    owner_id = serializers.UUIDField(required=False)
    confidentiality = serializers.ChoiceField(choices=CONFIDENTIALITY_CHOICES, required=False)
    classification = serializers.CharField(max_length=60, required=False, allow_blank=True)
    tags = serializers.ListField(child=serializers.CharField(max_length=40), required=False)
    total_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    planned_start = serializers.DateField(required=False, allow_null=True)
    planned_end = serializers.DateField(required=False, allow_null=True)
    organization_role = serializers.ChoiceField(choices=PARTY_ROLE_CHOICES, required=False)
    counterparty_role = serializers.ChoiceField(choices=PARTY_ROLE_CHOICES, required=False)


class ContractFilterSerializer(serializers.Serializer[None]):
    q = serializers.CharField(required=False, max_length=100)
    drafting_status = serializers.ChoiceField(choices=DRAFTING_CHOICES, required=False)
    contract_type = serializers.ChoiceField(choices=CONTRACT_TYPE_CHOICES, required=False)
    counterparty_id = serializers.UUIDField(required=False)
    owner_id = serializers.UUIDField(required=False)
    department_id = serializers.UUIDField(required=False)
    tag = serializers.CharField(required=False, max_length=40)


class DraftSerializer(serializers.ModelSerializer[ContractDraft]):
    class Meta:
        model = ContractDraft
        fields = ("content", "version", "updated_by_id", "updated_at")
        read_only_fields = ("version", "updated_by_id", "updated_at")


class DraftUpdateSerializer(serializers.Serializer[ContractDraft]):
    content = serializers.CharField(allow_blank=True, trim_whitespace=False)
    expected_version = serializers.IntegerField(min_value=1)


class PublishSerializer(serializers.Serializer[Revision]):
    expected_draft_version = serializers.IntegerField(min_value=1)
    summary = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class CompareSerializer(serializers.Serializer[None]):
    old = serializers.UUIDField(help_text="Revisão de origem")
    new = serializers.UUIDField(help_text="Revisão de destino")


class CommentSerializer(serializers.ModelSerializer[Comment]):
    author_name = serializers.CharField(source="author.full_name", read_only=True)

    class Meta:
        model = Comment
        fields = (
            "id",
            "body",
            "line",
            "author_id",
            "author_name",
            "created_at",
            "resolved_at",
            "resolved_by_id",
        )
        read_only_fields = (
            "id",
            "author_id",
            "author_name",
            "created_at",
            "resolved_at",
            "resolved_by_id",
        )


class GrantSerializer(serializers.ModelSerializer[ContractGrant]):
    class Meta:
        model = ContractGrant
        fields = ("id", "user_id", "team_id", "can_edit", "source", "created_at")
        read_only_fields = ("id", "source", "created_at")


class GrantCreateSerializer(serializers.Serializer[ContractGrant]):
    user_id = serializers.UUIDField(required=False, allow_null=True)
    team_id = serializers.UUIDField(required=False, allow_null=True)
    can_edit = serializers.BooleanField(default=False)


class ContractPagination(StablePagination):
    ordering = ("-updated_at", "-id")


class ContractViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, TenantViewSet):
    serializer_class = ContractSerializer
    pagination_class = ContractPagination
    pk_log_key = "contract_id"
    action_permissions: ClassVar[dict[str, Permission | None]] = {
        # Leitura depende da visibilidade por contrato (vínculos incluídos),
        # não apenas do papel; por isso não exige `contracts.read` aqui.
        "list": None,
        "retrieve": None,
        "create": Permission.CONTRACTS_CREATE,
        "partial_update": Permission.CONTRACTS_EDIT,
        "draft": None,
        "revisions": None,
        "revision": None,
        "compare": None,
        "comments": None,
        "resolve_comment": Permission.CONTRACTS_COMMENT,
        "grants": None,
        "grant": Permission.CONTRACTS_SHARE,
    }

    def get_queryset(self) -> QuerySet[Contract]:
        queryset = selectors.visible_contracts(self.principal)
        if self.action != "list":
            return queryset
        filters = parse_filters(self.request, ContractFilterSerializer)
        if "q" in filters:
            term = filters.pop("q")
            queryset = queryset.filter(Q(title__icontains=term) | Q(reference__iexact=term))
        if "tag" in filters:
            queryset = queryset.filter(tags__contains=[filters.pop("tag").lower()])
        return queryset.filter(**filters)

    def _contract_response(
        self, contract_id: uuid.UUID, *, status_code: int = status.HTTP_200_OK
    ) -> Response:
        contract = selectors.get_visible_contract(self.principal, contract_id)
        return Response(
            ContractSerializer(contract, context={"view": self}).data, status=status_code
        )

    @extend_schema(request=ContractCreateSerializer, responses={201: ContractSerializer})
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        def operation() -> Response:
            serializer = ContractCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            contract = services.create_contract(
                self.principal,
                services.ContractInput(
                    title=data["title"],
                    contract_type=ContractType(data["contract_type"]),
                    counterparty_id=data["counterparty_id"],
                    organization_role=PartyRole(data["organization_role"]),
                    counterparty_role=PartyRole(data["counterparty_role"]),
                    owner_id=data["owner_id"],
                    department_id=data.get("department_id"),
                    requester_id=data.get("requester_id"),
                    confidentiality=Confidentiality(data["confidentiality"]),
                    classification=data["classification"],
                    tags=tuple(data["tags"]),
                    total_value=data.get("total_value"),
                    planned_start=data.get("planned_start"),
                    planned_end=data.get("planned_end"),
                    parent_id=data.get("parent_id"),
                    initial_content=data["initial_content"],
                ),
            )
            return self._contract_response(contract.id, status_code=status.HTTP_201_CREATED)

        return self.idempotent(request, "contracts.create", operation)

    @extend_schema(request=ContractUpdateSerializer, responses=ContractSerializer)
    def partial_update(self, request: Request, pk: uuid.UUID) -> Response:
        serializer = ContractUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        expected = changes.pop("expected_version")
        contract = services.update_metadata(
            self.principal, pk, expected_version=expected, changes=changes
        )
        return self._contract_response(contract.id)

    @extend_schema(methods=["GET"], responses=DraftSerializer)
    @extend_schema(methods=["PUT"], request=DraftUpdateSerializer, responses=DraftSerializer)
    @action(detail=True, methods=["get", "put"])
    def draft(self, request: Request, pk: uuid.UUID) -> Response:
        if request.method == "GET":
            return Response(DraftSerializer(services.get_draft(self.principal, pk)).data)
        serializer = DraftUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        draft = services.save_draft(
            self.principal,
            pk,
            content=serializer.validated_data["content"],
            expected_version=serializer.validated_data["expected_version"],
        )
        return Response(DraftSerializer(draft).data)

    @extend_schema(methods=["GET"], responses=RevisionSummarySerializer(many=True))
    @extend_schema(
        methods=["POST"], request=PublishSerializer, responses={201: RevisionDetailSerializer}
    )
    @action(detail=True, methods=["get", "post"])
    def revisions(self, request: Request, pk: uuid.UUID) -> Response:
        contract_id = pk
        if request.method == "GET":
            contract = selectors.get_visible_contract(self.principal, contract_id)
            revisions = selectors.revisions_of(contract).order_by("-number")
            return Response(RevisionSummarySerializer(revisions, many=True).data)

        def operation() -> Response:
            serializer = PublishSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            revision = services.publish_revision(
                self.principal,
                contract_id,
                expected_draft_version=serializer.validated_data["expected_draft_version"],
                summary=serializer.validated_data["summary"],
            )
            return Response(RevisionDetailSerializer(revision).data, status=status.HTTP_201_CREATED)

        return self.idempotent(request, "contracts.publish_revision", operation)

    @extend_schema(responses=RevisionDetailSerializer)
    @action(detail=True, methods=["get"], url_path="revisions/<uuid:revision_id>")
    def revision(self, _request: Request, pk: uuid.UUID, revision_id: uuid.UUID) -> Response:
        contract = selectors.get_visible_contract(self.principal, pk)
        return Response(
            RevisionDetailSerializer(selectors.get_revision(contract, revision_id)).data
        )

    @extend_schema(parameters=[CompareSerializer], responses={200: dict})
    @action(detail=True, methods=["get"])
    def compare(self, request: Request, pk: uuid.UUID) -> Response:
        params = CompareSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        diff = services.compare_revisions(
            self.principal,
            pk,
            params.validated_data["old"],
            params.validated_data["new"],
        )
        return Response(
            {
                "kind": "textual",
                "inserted_lines": diff.inserted,
                "deleted_lines": diff.deleted,
                "hunks": [
                    {
                        "kind": h.kind.value,
                        "old_start": h.old_start,
                        "old_lines": list(h.old_lines),
                        "new_start": h.new_start,
                        "new_lines": list(h.new_lines),
                    }
                    for h in diff.hunks
                ],
            }
        )

    @extend_schema(methods=["GET"], responses=CommentSerializer(many=True))
    @extend_schema(methods=["POST"], request=CommentSerializer, responses={201: CommentSerializer})
    @action(
        detail=True,
        methods=["get", "post"],
        url_path="revisions/<uuid:revision_id>/comments",
    )
    def comments(self, request: Request, pk: uuid.UUID, revision_id: uuid.UUID) -> Response:
        contract_id, rev_id = pk, revision_id
        if request.method == "GET":
            contract = selectors.get_visible_contract(self.principal, contract_id)
            revision = selectors.get_revision(contract, rev_id)
            return Response(CommentSerializer(selectors.comments_of(revision), many=True).data)
        serializer = CommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = services.add_comment(
            self.principal,
            contract_id,
            rev_id,
            body=serializer.validated_data["body"],
            line=serializer.validated_data.get("line"),
        )
        return Response(CommentSerializer(comment).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=CommentSerializer)
    @action(detail=True, methods=["post"], url_path="comments/<uuid:comment_id>/resolve")
    def resolve_comment(self, _request: Request, pk: uuid.UUID, comment_id: uuid.UUID) -> Response:
        comment = services.resolve_comment(self.principal, pk, comment_id)
        return Response(CommentSerializer(comment).data)

    @extend_schema(methods=["GET"], responses=GrantSerializer(many=True))
    @extend_schema(
        methods=["POST"], request=GrantCreateSerializer, responses={201: GrantSerializer}
    )
    @action(detail=True, methods=["get", "post"])
    def grants(self, request: Request, pk: uuid.UUID) -> Response:
        contract_id = pk
        if request.method == "GET":
            contract = selectors.get_visible_contract(self.principal, contract_id)
            return Response(GrantSerializer(contract.grants.all(), many=True).data)
        serializer = GrantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grant = services.share(
            self.principal,
            contract_id,
            user_id=serializer.validated_data.get("user_id"),
            team_id=serializer.validated_data.get("team_id"),
            can_edit=serializer.validated_data["can_edit"],
        )
        return Response(GrantSerializer(grant).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["delete"], url_path="grants/<uuid:grant_id>")
    def grant(self, _request: Request, pk: uuid.UUID, grant_id: uuid.UUID) -> Response:
        services.unshare(self.principal, pk, grant_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
