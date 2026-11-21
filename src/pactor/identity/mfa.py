"""Leitura do estado de MFA mantido pelo django-allauth.

Encapsula o acesso às estruturas internas do allauth em um único ponto, para
que uma atualização da biblioteca exija ajuste somente aqui (coberto por testes).
"""

from __future__ import annotations

from allauth.account.internal.flows.login import get_authentication_records
from allauth.mfa.models import Authenticator
from django.http import HttpRequest
from rest_framework.request import Request

from pactor.identity.models import User


def has_mfa_enrolled(user: User) -> bool:
    return bool(Authenticator.objects.filter(user=user, type=Authenticator.Type.TOTP).exists())


def session_used_mfa(request: HttpRequest | Request, user: User) -> bool:
    """Verdadeiro quando a sessão atual concluiu um desafio de MFA."""
    records = get_authentication_records(request, user)
    return any(isinstance(r, dict) and r.get("method") == "mfa" for r in records)
