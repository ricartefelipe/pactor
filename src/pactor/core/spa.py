"""Entrega da aplicação web (SPA) na mesma origem da API.

Os arquivos compilados ficam em /static/ (whitenoise); esta view devolve o
index.html para as rotas da aplicação, que o roteador do navegador resolve.
O HTML não é cacheado: a cada publicação ele aponta para novos arquivos com
hash no nome.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET


@transaction.non_atomic_requests
@require_GET
@ensure_csrf_cookie
def index(_request: HttpRequest) -> HttpResponse:
    try:
        content = (settings.FRONTEND_DIST_DIR / "index.html").read_bytes()
    except FileNotFoundError:
        return HttpResponse(
            "Aplicação web não compilada: execute `npm run build` em frontend/.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
    response = HttpResponse(content, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response
