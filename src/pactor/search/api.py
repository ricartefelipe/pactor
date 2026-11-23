"""API de pesquisa, sugestões, painel e exportação."""

from __future__ import annotations

from typing import ClassVar

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response

from pactor.access.api import TenantAPIView
from pactor.access.policies import Permission
from pactor.contracts.models import CONTRACT_TYPE_CHOICES, DRAFTING_CHOICES, LIFECYCLE_CHOICES
from pactor.core.api.filters import parse_filters
from pactor.search import reports, services


class SearchQuerySerializer(serializers.Serializer[None]):
    q = serializers.CharField(
        required=False, allow_blank=True, max_length=services.MAX_QUERY_LENGTH, default=""
    )
    contract_type = serializers.ChoiceField(choices=CONTRACT_TYPE_CHOICES, required=False)
    drafting_status = serializers.ChoiceField(choices=DRAFTING_CHOICES, required=False)
    lifecycle_status = serializers.ChoiceField(choices=LIFECYCLE_CHOICES, required=False)
    counterparty_id = serializers.UUIDField(required=False)
    owner_id = serializers.UUIDField(required=False)
    department_id = serializers.UUIDField(required=False)
    tag = serializers.CharField(required=False, max_length=40)
    ends_after = serializers.DateField(required=False)
    ends_before = serializers.DateField(required=False)
    page = serializers.IntegerField(
        required=False, min_value=1, max_value=services.MAX_PAGE, default=1
    )
    size = serializers.IntegerField(
        required=False, min_value=1, max_value=services.MAX_PAGE_SIZE, default=20
    )


class SuggestQuerySerializer(serializers.Serializer[None]):
    q = serializers.CharField(max_length=services.MAX_QUERY_LENGTH)


class SearchResultSerializer(serializers.Serializer[None]):
    id = serializers.UUIDField()
    reference = serializers.CharField()
    title = serializers.CharField()
    counterparty_name = serializers.CharField()
    contract_type = serializers.ChoiceField(choices=CONTRACT_TYPE_CHOICES)
    drafting_status = serializers.ChoiceField(choices=DRAFTING_CHOICES)
    lifecycle_status = serializers.ChoiceField(choices=LIFECYCLE_CHOICES)
    snippet = serializers.CharField()


class SearchFacetsSerializer(serializers.Serializer[None]):
    contract_type = serializers.DictField(child=serializers.IntegerField())
    drafting_status = serializers.DictField(child=serializers.IntegerField())
    lifecycle_status = serializers.DictField(child=serializers.IntegerField())


class SearchPageSerializer(serializers.Serializer[None]):
    total = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    facets = SearchFacetsSerializer()
    results = SearchResultSerializer(many=True)


class SuggestionSerializer(serializers.Serializer[None]):
    id = serializers.UUIDField()
    reference = serializers.CharField()
    title = serializers.CharField()


class DashboardSerializer(serializers.Serializer[None]):
    today = serializers.DateField()
    contracts_total = serializers.IntegerField()
    by_drafting_status = serializers.DictField(child=serializers.IntegerField())
    by_lifecycle_status = serializers.DictField(child=serializers.IntegerField())
    deadlines_next_30_days = serializers.IntegerField()
    overdue_obligations = serializers.IntegerField()
    pending_approvals = serializers.IntegerField()


class SearchView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(parameters=[SearchQuerySerializer], responses=SearchPageSerializer)
    def get(self, request: Request) -> Response:
        params = parse_filters(request, SearchQuerySerializer)
        page, size = int(params.pop("page", 1)), int(params.pop("size", 20))
        result = services.search(
            self.principal,
            services.SearchFilters(text=params.pop("q", ""), **params),
            page=page,
            page_size=size,
        )
        return Response(
            {
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
                "facets": result.facets,
                "results": [
                    {
                        "id": str(c.id),
                        "reference": c.reference,
                        "title": c.title,
                        "counterparty_name": c.counterparty.name,
                        "contract_type": c.contract_type,
                        "drafting_status": c.drafting_status,
                        "lifecycle_status": c.lifecycle_status,
                        "snippet": result.snippets.get(c.id, ""),
                    }
                    for c in result.results
                ],
            }
        )


class SuggestView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(parameters=[SuggestQuerySerializer], responses=SuggestionSerializer(many=True))
    def get(self, request: Request) -> Response:
        params = SuggestQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        items = services.suggest(self.principal, params.validated_data["q"])
        return Response(
            [{"id": str(c.id), "reference": c.reference, "title": c.title} for c in items]
        )


class DashboardView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": None}

    @extend_schema(responses=DashboardSerializer)
    def get(self, _request: Request) -> Response:
        return Response(reports.dashboard(self.principal))


class ContractsExportView(TenantAPIView):
    action_permissions: ClassVar[dict[str, Permission | None]] = {"get": Permission.REPORTS_READ}

    @extend_schema(
        responses={(200, "text/csv"): OpenApiResponse(description="Contratos visíveis em CSV")}
    )
    def get(self, _request: Request) -> HttpResponse:
        response = HttpResponse(
            reports.export_contracts_csv(self.principal), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = 'attachment; filename="contratos.csv"'
        response["Cache-Control"] = "no-store"
        return response
