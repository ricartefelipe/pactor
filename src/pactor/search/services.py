"""Pesquisa autorizada: o filtro de visibilidade vem antes de qualquer resultado.

Resultados, snippets, contagens, facetas e sugestões são calculados apenas
sobre contratos visíveis ao principal; um contrato confidencial sem vínculo
não aparece em nenhum deles. Paginação limitada e ordenação determinística.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank
from django.db.models import Count, F, Q, QuerySet

from pactor.access.policies import Principal
from pactor.contracts.models import Contract
from pactor.contracts.selectors import visible_contracts

MAX_PAGE_SIZE = 50
MAX_PAGE = 100
MAX_QUERY_LENGTH = 200
MIN_SUGGEST_LENGTH = 2


@dataclass(frozen=True, slots=True)
class SearchFilters:
    text: str = ""
    contract_type: str | None = None
    drafting_status: str | None = None
    lifecycle_status: str | None = None
    counterparty_id: uuid.UUID | None = None
    owner_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    tag: str | None = None
    ends_after: date | None = None
    ends_before: date | None = None


@dataclass(frozen=True, slots=True)
class SearchPage:
    total: int
    page: int
    page_size: int
    results: list[Contract]
    snippets: Mapping[uuid.UUID, str]
    facets: Mapping[str, Mapping[str, int]]


def _filtered(principal: Principal, filters: SearchFilters) -> QuerySet[Contract]:
    queryset = visible_contracts(principal)
    exact = {
        "contract_type": filters.contract_type,
        "drafting_status": filters.drafting_status,
        "lifecycle_status": filters.lifecycle_status,
        "counterparty_id": filters.counterparty_id,
        "owner_id": filters.owner_id,
        "department_id": filters.department_id,
    }
    queryset = queryset.filter(**{k: v for k, v in exact.items() if v is not None})
    if filters.tag:
        queryset = queryset.filter(tags__contains=[filters.tag.lower()])
    if filters.ends_after:
        queryset = queryset.filter(
            Q(effective_end__gte=filters.ends_after) | Q(planned_end__gte=filters.ends_after)
        )
    if filters.ends_before:
        queryset = queryset.filter(
            Q(effective_end__lte=filters.ends_before) | Q(planned_end__lte=filters.ends_before)
        )
    return queryset


def search(
    principal: Principal, filters: SearchFilters, *, page: int = 1, page_size: int = 20
) -> SearchPage:
    page = max(1, min(page, MAX_PAGE))
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    base = _filtered(principal, filters)
    text = filters.text.strip()[:MAX_QUERY_LENGTH]
    start = (page - 1) * page_size
    snippets: dict[uuid.UUID, str] = {}
    if text:
        query = SearchQuery(text, search_type="websearch", config="portuguese")
        matched = base.filter(Q(search_document__vector=query) | Q(reference__iexact=text))
        ranked = matched.annotate(rank=SearchRank(F("search_document__vector"), query)).order_by(
            F("rank").desc(nulls_last=True), "reference", "id"
        )
        results: list[Contract] = [*ranked[start : start + page_size]]
        if results:
            headline = SearchHeadline(
                "search_document__body",
                query,
                config="portuguese",
                max_fragments=2,
                max_words=20,
                min_words=5,
                start_sel="«",
                stop_sel="»",
            )
            rows = Contract.objects.filter(id__in=[c.id for c in results]).annotate(
                snippet=headline
            )
            snippets = {row.id: str(row.snippet or "") for row in rows}
    else:
        matched = base
        results = [*matched.order_by("-updated_at", "id")[start : start + page_size]]
    total = matched.count()
    facets = {
        field: {
            str(row[field]): int(row["n"])
            for row in matched.values(field).annotate(n=Count("id")).order_by(field)
        }
        for field in ("contract_type", "drafting_status", "lifecycle_status")
    }
    return SearchPage(
        total=total,
        page=page,
        page_size=page_size,
        results=results,
        snippets=snippets,
        facets=facets,
    )


def suggest(principal: Principal, prefix: str, *, limit: int = 10) -> list[Contract]:
    """Autocomplete sobre referência e título, somente entre contratos visíveis."""
    term = prefix.strip()[:MAX_QUERY_LENGTH]
    if len(term) < MIN_SUGGEST_LENGTH:
        return []
    return list(
        visible_contracts(principal)
        .filter(Q(reference__istartswith=term) | Q(title__icontains=term))
        .order_by("reference", "id")[: min(limit, 20)]
    )
