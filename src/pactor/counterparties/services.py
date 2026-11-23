"""Casos de uso de contrapartes."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from django.db.models import F, QuerySet
from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.audit import services as audit
from pactor.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    StaleVersionError,
)
from pactor.core.tenancy import require_tenant
from pactor.counterparties.domain import (
    InvalidDocumentError,
    PartyKind,
    normalize_document,
    only_digits,
)
from pactor.counterparties.models import Address, Contact, Counterparty, Representative
from pactor.plans import services as plans

_UF = frozenset(
    [
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    ]
)
_CEP_LENGTH = 8


@dataclass(frozen=True, slots=True)
class ContactData:
    name: str
    email: str = ""
    phone: str = ""
    position: str = ""


@dataclass(frozen=True, slots=True)
class AddressData:
    street: str
    city: str
    state: str
    postal_code: str
    number: str = ""
    complement: str = ""
    district: str = ""
    label: str = "Principal"


@dataclass(frozen=True, slots=True)
class RepresentativeData:
    name: str
    document: str
    email: str = ""
    capacity: str = ""


@dataclass(frozen=True, slots=True)
class CounterpartyData:
    kind: PartyKind
    name: str
    document: str
    trade_name: str = ""
    email: str = ""
    phone: str = ""
    notes: str = ""
    contacts: Sequence[ContactData] = field(default_factory=tuple)
    addresses: Sequence[AddressData] = field(default_factory=tuple)
    representatives: Sequence[RepresentativeData] = field(default_factory=tuple)


def _document(kind: PartyKind, raw: str) -> str:
    try:
        return normalize_document(kind, raw)
    except InvalidDocumentError as exc:
        raise DomainValidationError(str(exc), details={"document": [str(exc)]}) from exc


def _validated_address(data: AddressData) -> AddressData:
    state = data.state.strip().upper()
    postal = only_digits(data.postal_code)
    if state not in _UF:
        raise DomainValidationError("UF inválida.", details={"state": [data.state]})
    if len(postal) != _CEP_LENGTH:
        raise DomainValidationError(
            "CEP deve ter 8 dígitos.", details={"postal_code": [data.postal_code]}
        )
    if not data.street.strip() or not data.city.strip():
        raise DomainValidationError("Logradouro e cidade são obrigatórios.")
    return AddressData(
        street=data.street.strip(),
        city=data.city.strip(),
        state=state,
        postal_code=postal,
        number=data.number.strip(),
        complement=data.complement.strip(),
        district=data.district.strip(),
        label=data.label.strip() or "Principal",
    )


def counterparties_for(principal: Principal) -> QuerySet[Counterparty]:
    authorize(principal, Permission.COUNTERPARTIES_READ)
    return Counterparty.objects.filter(organization_id=principal.organization_id).prefetch_related(
        "contacts", "addresses", "representatives"
    )


def get_counterparty(principal: Principal, counterparty_id: uuid.UUID) -> Counterparty:
    counterparty = counterparties_for(principal).filter(id=counterparty_id).first()
    if counterparty is None:
        raise NotFoundError("Contraparte não encontrada.")
    return counterparty


def create_counterparty(principal: Principal, data: CounterpartyData) -> Counterparty:
    authorize(principal, Permission.COUNTERPARTIES_WRITE)
    plans.ensure_writable()
    organization_id = require_tenant()
    document = _document(data.kind, data.document)
    if not data.name.strip():
        raise DomainValidationError("Informe o nome da contraparte.")
    if Counterparty.objects.filter(organization_id=organization_id, document=document).exists():
        raise ConflictError(
            "Já existe contraparte com este documento.", details={"document": document}
        )
    addresses = [_validated_address(a) for a in data.addresses]
    representatives = [
        (r, _document(PartyKind.INDIVIDUAL, r.document)) for r in data.representatives
    ]
    counterparty = Counterparty.objects.create(
        organization_id=organization_id,
        kind=data.kind.value,
        name=data.name.strip(),
        trade_name=data.trade_name.strip(),
        document=document,
        email=data.email.strip().lower(),
        phone=data.phone.strip(),
        notes=data.notes.strip(),
    )
    Contact.objects.bulk_create(
        Contact(
            organization_id=organization_id,
            counterparty=counterparty,
            name=c.name.strip(),
            email=c.email.strip().lower(),
            phone=c.phone.strip(),
            position=c.position.strip(),
        )
        for c in data.contacts
    )
    Address.objects.bulk_create(
        Address(
            organization_id=organization_id,
            counterparty=counterparty,
            street=a.street,
            city=a.city,
            state=a.state,
            postal_code=a.postal_code,
            number=a.number,
            complement=a.complement,
            district=a.district,
            label=a.label,
        )
        for a in addresses
    )
    Representative.objects.bulk_create(
        Representative(
            organization_id=organization_id,
            counterparty=counterparty,
            name=r.name.strip(),
            document=doc,
            email=r.email.strip().lower(),
            capacity=r.capacity.strip(),
        )
        for r, doc in representatives
    )
    audit.record(
        principal,
        action="counterparty.created",
        resource_type="counterparty",
        resource_id=counterparty.id,
        changes={"name": counterparty.name, "kind": counterparty.kind, "document": document},
    )
    return counterparty


_EDITABLE_FIELDS = frozenset({"name", "trade_name", "email", "phone", "notes", "is_active"})


def update_counterparty(
    principal: Principal,
    counterparty_id: uuid.UUID,
    *,
    expected_version: int,
    changes: Mapping[str, object],
) -> Counterparty:
    """Atualiza dados cadastrais com concorrência otimista.

    O documento (CPF/CNPJ) e o tipo de pessoa não mudam: identificam a
    contraparte em contratos já registrados.
    """
    authorize(principal, Permission.COUNTERPARTIES_WRITE)
    plans.ensure_writable()
    unknown = set(changes) - _EDITABLE_FIELDS
    if unknown:
        raise DomainValidationError("Campos não editáveis.", details={"fields": sorted(unknown)})
    current = get_counterparty(principal, counterparty_id)
    before = {k: getattr(current, k) for k in changes}
    updated = Counterparty.objects.filter(
        organization_id=principal.organization_id, id=counterparty_id, version=expected_version
    ).update(**changes, version=F("version") + 1, updated_at=timezone.now())
    if updated == 0:
        current.refresh_from_db(fields=["version"])
        raise StaleVersionError(
            "A contraparte foi alterada por outra pessoa. Recarregue antes de salvar.",
            details={"current_version": current.version},
        )
    audit.record(
        principal,
        action="counterparty.updated",
        resource_type="counterparty",
        resource_id=counterparty_id,
        changes={k: {"before": before[k], "after": v} for k, v in changes.items()},
    )
    return get_counterparty(principal, counterparty_id)
