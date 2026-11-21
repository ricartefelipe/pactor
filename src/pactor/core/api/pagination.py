"""Paginação por cursor com ordenação determinística e limite máximo."""

from __future__ import annotations

from rest_framework.pagination import CursorPagination


class StablePagination(CursorPagination):
    """Cursor estável: a ordenação inclui ``id`` para desempate determinístico."""

    page_size = 25
    max_page_size = 100
    page_size_query_param = "page_size"
    ordering = ("-created_at", "-id")
