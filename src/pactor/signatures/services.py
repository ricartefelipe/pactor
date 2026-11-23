"""Casos de uso de assinatura eletrônica.

Fronteiras transacionais:

* ``send_for_signature`` roda na transação da requisição: valida a aprovação,
  gera e guarda o PDF da revisão aprovada, cria o envelope e emite o evento na
  outbox. Não fala com o provedor.
* ``plan_dispatch`` (transação curta) lê o envelope e congela o pedido;
  ``perform_dispatch`` fala com o provedor **fora de transação** e grava o
  resultado em nova transação curta. Após timeout, a próxima tentativa
  consulta o provedor pela referência antes de criar qualquer envelope.
* Webhooks entram primeiro na inbox (dedup por chave do provedor) e depois são
  aplicados no tenant, com o contrato e o envelope travados.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.approvals.services import require_approved_for_signature
from pactor.audit import services as audit
from pactor.contracts.domain import DraftingStatus
from pactor.contracts.models import Contract
from pactor.contracts.pdf import PdfDocument, render_pdf
from pactor.contracts.selectors import get_visible_contract
from pactor.contracts.transitions import transition
from pactor.core import outbox
from pactor.core.errors import (
    ConflictError,
    DomainValidationError,
    InvalidTransitionError,
    NotFoundError,
)
from pactor.core.tenancy import require_tenant, tenant_context
from pactor.identity.models import Organization
from pactor.plans import services as plans
from pactor.plans.models import Module
from pactor.signatures import storage
from pactor.signatures.models import (
    DocumentKind,
    Envelope,
    EnvelopeDocument,
    EnvelopeRoute,
    EnvelopeState,
    EventOutcome,
    Recipient,
    SignatureEvent,
)
from pactor.signatures.ports import (
    ENVELOPE_RANK,
    RECIPIENT_RANK,
    EnvelopeHandle,
    EnvelopeRequest,
    EnvelopeStatus,
    ProviderEvent,
    ProviderOutcomeUnknownError,
    ProviderRejectedError,
    ProviderUnavailableError,
    RecipientState,
    RecipientStatus,
    SignatureProvider,
    Signer,
)
from pactor.signatures.ports import EnvelopeState as ProviderEnvelopeState
from pactor.signatures.providers import get_provider

logger = logging.getLogger(__name__)

MAX_SIGNERS = 10
RECONCILE_AFTER = timedelta(minutes=10)


class DispatchRetryError(Exception):
    """Envio sem resultado definitivo; a task deve tentar novamente."""


@dataclass(frozen=True, slots=True)
class SignerInput:
    name: str
    email: str
    routing_order: int


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------
def _validated_signers(signers: Sequence[SignerInput]) -> list[Signer]:
    if not 0 < len(signers) <= MAX_SIGNERS:
        raise DomainValidationError(f"Informe de 1 a {MAX_SIGNERS} signatários.")
    emails = [s.email.strip().lower() for s in signers]
    if len(set(emails)) != len(emails):
        raise DomainValidationError("Signatário repetido.")
    result: list[Signer] = []
    for index, (signer, email) in enumerate(zip(signers, emails, strict=True), start=1):
        if not signer.name.strip() or "@" not in email:
            raise DomainValidationError(
                "Nome e e-mail válidos são obrigatórios para cada signatário."
            )
        if signer.routing_order < 1:
            raise DomainValidationError("Ordem de assinatura deve ser positiva.")
        result.append(Signer(str(index), signer.name.strip(), email, signer.routing_order))
    return result


def render_revision(contract: Contract) -> bytes:
    """PDF da revisão aprovada (nunca da "versão mais recente")."""
    revision = contract.approved_revision
    if revision is None:
        raise InvalidTransitionError("Contrato sem revisão aprovada.")
    metadata = revision.metadata
    document_id = metadata.get("counterparty_document")
    value = metadata.get("total_value") or "não informado"
    currency = metadata.get("currency", "BRL")
    return render_pdf(
        PdfDocument(
            title=str(metadata.get("title", contract.title)),
            header_lines=[
                f"Contrato {contract.reference} — revisão {revision.number}",
                f"Contraparte: {contract.counterparty.name} ({document_id})",
                f"Valor: {value} {currency}",
            ],
            body=revision.content,
            footer_lines=[
                f"Identificação do conteúdo submetido (fingerprint): {revision.fingerprint}",
                "O fingerprint identifica os dados registrados; não substitui assinatura digital.",
            ],
        )
    )


def send_for_signature(
    principal: Principal,
    contract_id: uuid.UUID,
    *,
    signers: Sequence[SignerInput],
    expected_fingerprint: str,
    provider_name: str | None = None,
) -> Envelope:
    authorize(principal, Permission.SIGNATURES_SEND)
    plans.ensure_module(Module.SIGNATURES)
    get_visible_contract(principal, contract_id)
    contract = (
        Contract.objects.select_for_update()
        .filter(organization_id=require_tenant(), id=contract_id)
        .first()
    )
    if contract is None:
        raise NotFoundError("Contrato não encontrado.")
    require_approved_for_signature(contract)
    approved_revision_id = contract.approved_revision_id
    if approved_revision_id is None or contract.approved_fingerprint != expected_fingerprint:
        raise ConflictError(
            "O documento a enviar difere do aprovado.",
            details={"approved_fingerprint": contract.approved_fingerprint},
        )
    validated = _validated_signers(signers)
    provider = get_provider(provider_name)
    transition(contract, DraftingStatus.SIGNING)
    document = render_revision(contract)
    envelope = Envelope.objects.create(
        organization_id=contract.organization_id,
        contract=contract,
        revision_id=approved_revision_id,
        fingerprint=contract.approved_fingerprint,
        provider=provider.name,
        provider_account_id=provider.account_id,
        subject=f"Assinatura: {contract.reference} — {contract.title}"[:200],
        created_by_id=principal.actor_id,
    )
    Recipient.objects.bulk_create(
        Recipient(
            organization_id=contract.organization_id,
            envelope=envelope,
            recipient_key=s.recipient_key,
            name=s.name,
            email=s.email,
            routing_order=s.routing_order,
        )
        for s in validated
    )
    sent = storage.store(
        envelope, DocumentKind.SENT, f"{contract.reference}.pdf", "application/pdf", document
    )
    contract.version += 1
    contract.updated_at = timezone.now()
    contract.save(update_fields=["drafting_status", "version", "updated_at"])
    outbox.emit(
        "signatures.envelope_requested",
        {"envelope_id": str(envelope.id)},
        organization_id=contract.organization_id,
    )
    audit.record(
        principal,
        action="signature.requested",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "envelope_id": str(envelope.id),
            "revision_id": str(envelope.revision_id),
            "fingerprint": envelope.fingerprint,
            "document_sha256": sent.sha256,
            "provider": provider.name,
            "signers": len(validated),
        },
    )
    return envelope


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    organization_id: uuid.UUID
    envelope_id: uuid.UUID
    provider_name: str
    provider_account_id: str
    lookup_first: bool
    request: EnvelopeRequest


def _fail_envelope(envelope: Envelope, reason: str) -> None:
    envelope.state = EnvelopeState.FAILED.value
    envelope.last_error = reason[:2000]
    envelope.updated_at = timezone.now()
    envelope.save(update_fields=["state", "last_error", "updated_at"])
    contract = Contract.objects.select_for_update().get(id=envelope.contract_id)
    if contract.drafting_status == DraftingStatus.SIGNING:
        transition(contract, DraftingStatus.APPROVED)
        contract.save(update_fields=["drafting_status"])


def plan_dispatch(organization_id: uuid.UUID, envelope_id: uuid.UUID) -> DispatchPlan | None:
    """Congela o pedido de envio a partir dos dados persistidos (transação curta)."""
    with tenant_context(organization_id):
        envelope = Envelope.objects.select_for_update().filter(id=envelope_id).first()
        if envelope is None or envelope.state not in {
            EnvelopeState.PENDING_DISPATCH,
            EnvelopeState.DISPATCH_UNKNOWN,
        }:
            return None
        contract = Contract.objects.get(id=envelope.contract_id)
        if (
            contract.approved_revision_id != envelope.revision_id
            or contract.approved_fingerprint != envelope.fingerprint
        ):
            _fail_envelope(envelope, "A aprovação do contrato mudou antes do envio.")
            return None
        sent = EnvelopeDocument.objects.get(envelope=envelope, kind=DocumentKind.SENT)
        try:
            content = storage.read_verified(sent)
        except storage.DocumentIntegrityError:
            _fail_envelope(envelope, "Documento armazenado diverge do hash registrado.")
            return None
        signers = [
            Signer(r.recipient_key, r.name, r.email, r.routing_order)
            for r in envelope.recipients.order_by("routing_order", "recipient_key")
        ]
        return DispatchPlan(
            organization_id=organization_id,
            envelope_id=envelope.id,
            provider_name=envelope.provider,
            provider_account_id=envelope.provider_account_id,
            lookup_first=envelope.state == EnvelopeState.DISPATCH_UNKNOWN,
            request=EnvelopeRequest(
                reference=str(envelope.id),
                subject=envelope.subject,
                document_name=sent.name,
                document=content,
                document_sha256=sent.sha256,
                signers=signers,
            ),
        )


def _provider_for(
    name: str, account_id: str, provider: SignatureProvider | None
) -> SignatureProvider:
    selected = provider or get_provider(name)
    if selected.name != name or selected.account_id != account_id:
        raise ProviderRejectedError("Provedor configurado não corresponde à conta do envelope.")
    return selected


def _record_dispatch_problem(plan: DispatchPlan, state: EnvelopeState, error: str) -> None:
    with tenant_context(plan.organization_id):
        envelope = Envelope.objects.select_for_update().get(id=plan.envelope_id)
        if envelope.state in {EnvelopeState.PENDING_DISPATCH, EnvelopeState.DISPATCH_UNKNOWN}:
            # "Desconhecido" é pegajoso: uma vez que um envio pode ter criado o
            # envelope, toda tentativa seguinte precisa consultar a referência.
            if envelope.state != EnvelopeState.DISPATCH_UNKNOWN:
                envelope.state = state.value
            envelope.dispatch_attempts += 1
            envelope.last_error = error[:2000]
            envelope.updated_at = timezone.now()
            envelope.save(update_fields=["state", "dispatch_attempts", "last_error", "updated_at"])


def _confirm_dispatch(plan: DispatchPlan, handle: EnvelopeHandle) -> None:
    with tenant_context(plan.organization_id):
        envelope = Envelope.objects.select_for_update().get(id=plan.envelope_id)
        EnvelopeRoute.objects.get_or_create(
            provider=plan.provider_name,
            provider_account_id=plan.provider_account_id,
            provider_envelope_id=handle.provider_envelope_id,
            defaults={"organization_id": plan.organization_id, "envelope_id": plan.envelope_id},
        )
        if envelope.state not in {EnvelopeState.PENDING_DISPATCH, EnvelopeState.DISPATCH_UNKNOWN}:
            return
        envelope.provider_envelope_id = handle.provider_envelope_id
        envelope.state = EnvelopeState.SENT.value
        envelope.dispatch_attempts += 1
        envelope.last_error = ""
        envelope.updated_at = timezone.now()
        envelope.save(
            update_fields=[
                "provider_envelope_id",
                "state",
                "dispatch_attempts",
                "last_error",
                "updated_at",
            ]
        )
        audit.record(
            None,
            action="signature.dispatched",
            resource_type="contract",
            resource_id=envelope.contract_id,
            changes={
                "envelope_id": str(envelope.id),
                "provider_envelope_id": handle.provider_envelope_id,
            },
        )


def perform_dispatch(plan: DispatchPlan, *, provider: SignatureProvider | None = None) -> None:
    """Fala com o provedor fora de transação e registra o resultado."""
    if connection.in_atomic_block:
        raise RuntimeError("perform_dispatch não pode rodar dentro de transação")
    selected = _provider_for(plan.provider_name, plan.provider_account_id, provider)
    try:
        handle = selected.find_by_reference(plan.request.reference) if plan.lookup_first else None
        if handle is None:
            handle = selected.create_envelope(plan.request)
    except ProviderOutcomeUnknownError as exc:
        _record_dispatch_problem(plan, EnvelopeState.DISPATCH_UNKNOWN, str(exc))
        raise DispatchRetryError(str(exc)) from exc
    except ProviderUnavailableError as exc:
        _record_dispatch_problem(plan, EnvelopeState.PENDING_DISPATCH, str(exc))
        raise DispatchRetryError(str(exc)) from exc
    except ProviderRejectedError as exc:
        with tenant_context(plan.organization_id):
            _fail_envelope(Envelope.objects.select_for_update().get(id=plan.envelope_id), str(exc))
        return
    _confirm_dispatch(plan, handle)


def dispatch(
    organization_id: uuid.UUID, envelope_id: uuid.UUID, *, provider: SignatureProvider | None = None
) -> None:
    plan = plan_dispatch(organization_id, envelope_id)
    if plan is not None:
        perform_dispatch(plan, provider=provider)


# ---------------------------------------------------------------------------
# Eventos do provedor
# ---------------------------------------------------------------------------
_PROVIDER_TO_STATE: Mapping[EnvelopeStatus, EnvelopeState] = {
    EnvelopeStatus.SENT: EnvelopeState.SENT,
    EnvelopeStatus.DELIVERED: EnvelopeState.DELIVERED,
    EnvelopeStatus.COMPLETED: EnvelopeState.COMPLETED,
    EnvelopeStatus.DECLINED: EnvelopeState.DECLINED,
    EnvelopeStatus.VOIDED: EnvelopeState.VOIDED,
}
_LOCAL_RANK: Mapping[str, int] = {
    EnvelopeState.PENDING_DISPATCH.value: 0,
    EnvelopeState.DISPATCH_UNKNOWN.value: 0,
    EnvelopeState.SENT.value: ENVELOPE_RANK[EnvelopeStatus.SENT],
    EnvelopeState.DELIVERED.value: ENVELOPE_RANK[EnvelopeStatus.DELIVERED],
    EnvelopeState.VOIDING.value: ENVELOPE_RANK[EnvelopeStatus.DELIVERED],
    EnvelopeState.COMPLETED.value: ENVELOPE_RANK[EnvelopeStatus.COMPLETED],
    EnvelopeState.DECLINED.value: ENVELOPE_RANK[EnvelopeStatus.DECLINED],
    EnvelopeState.VOIDED.value: ENVELOPE_RANK[EnvelopeStatus.VOIDED],
    EnvelopeState.FAILED.value: ENVELOPE_RANK[EnvelopeStatus.VOIDED],
}


def _apply_recipients(envelope: Envelope, state: ProviderEnvelopeState) -> bool:
    changed = False
    recipients = {r.recipient_key: r for r in envelope.recipients.select_for_update()}
    for incoming in state.recipients:
        recipient = recipients.get(incoming.recipient_key)
        if recipient is None:
            continue
        if RECIPIENT_RANK[incoming.status] > RECIPIENT_RANK[RecipientStatus(recipient.status)]:
            recipient.status = incoming.status.value
            recipient.changed_at = incoming.changed_at or state.changed_at
            recipient.save(update_fields=["status", "changed_at"])
            changed = True
    return changed


def _is_current_envelope(contract: Contract, envelope: Envelope) -> bool:
    """Envelope vigente: o mais recente do contrato, da revisão aprovada, não anulado.

    Envelopes concluídos ou recusados continuam vigentes (seus eventos tardios
    são apenas fora de ordem); envelopes cancelados, com falha ou substituídos
    por um envio posterior são antigos e nunca alteram o contrato.
    """
    if envelope.state in {EnvelopeState.VOIDED, EnvelopeState.FAILED}:
        return False
    if (
        contract.approved_revision_id != envelope.revision_id
        or contract.approved_fingerprint != envelope.fingerprint
    ):
        return False
    newer = Envelope.objects.filter(contract_id=contract.id, created_at__gt=envelope.created_at)
    return not newer.exists()


def apply_provider_state(envelope: Envelope, state: ProviderEnvelopeState) -> EventOutcome:
    """Aplica um estado do provedor ao envelope e ao contrato (ambos travados).

    Eventos de envelopes que não são o vigente para a revisão aprovada nunca
    alteram o contrato. Situações só avançam: eventos fora de ordem ou
    repetidos não regridem nada.
    """
    contract = Contract.objects.select_for_update().get(id=envelope.contract_id)
    if not _is_current_envelope(contract, envelope):
        return EventOutcome.OLD_ENVELOPE
    recipients_changed = _apply_recipients(envelope, state)
    target = _PROVIDER_TO_STATE[state.status]
    if ENVELOPE_RANK[state.status] <= _LOCAL_RANK[envelope.state]:
        return EventOutcome.APPLIED if recipients_changed else EventOutcome.STALE
    envelope.state = target.value
    envelope.last_event_at = state.changed_at
    envelope.updated_at = timezone.now()
    if target is EnvelopeState.COMPLETED:
        envelope.completed_at = state.changed_at
        transition(contract, DraftingStatus.SIGNED)
        outbox.emit(
            "signatures.envelope_completed",
            {"envelope_id": str(envelope.id), "contract_id": str(contract.id)},
            organization_id=envelope.organization_id,
        )
    elif target in {EnvelopeState.DECLINED, EnvelopeState.VOIDED}:
        transition(contract, DraftingStatus.APPROVED)
    envelope.save(update_fields=["state", "last_event_at", "updated_at", "completed_at"])
    contract.version += 1
    contract.save(update_fields=["drafting_status", "version"])
    audit.record(
        None,
        action=f"signature.{target.value}",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "envelope_id": str(envelope.id),
            "provider_envelope_id": envelope.provider_envelope_id,
        },
    )
    return EventOutcome.APPLIED


def record_event(provider_name: str, event: ProviderEvent) -> tuple[SignatureEvent, bool]:
    """Grava o evento na inbox. Devolve (evento, criado); repetição devolve o existente."""
    route = EnvelopeRoute.objects.filter(
        provider=provider_name, provider_envelope_id=event.provider_envelope_id
    ).first()
    with transaction.atomic():
        stored, created = SignatureEvent.objects.get_or_create(
            provider=provider_name,
            dedup_key=event.dedup_key,
            defaults={
                "provider_envelope_id": event.provider_envelope_id,
                "organization_id": route.organization_id if route else None,
                "envelope_id": route.envelope_id if route else None,
                "status": event.state.status.value,
                "occurred_at": event.occurred_at,
                "state": {
                    "status": event.state.status.value,
                    "changed_at": event.state.changed_at.isoformat(),
                    "recipients": [
                        {
                            "recipient_key": r.recipient_key,
                            "status": r.status.value,
                            "changed_at": r.changed_at.isoformat() if r.changed_at else None,
                        }
                        for r in event.state.recipients
                    ],
                },
                "outcome": EventOutcome.PENDING if route else EventOutcome.UNKNOWN_ENVELOPE,
            },
        )
    return stored, created


def _state_from_event(event: SignatureEvent) -> ProviderEnvelopeState:
    raw = event.state
    return ProviderEnvelopeState(
        provider_envelope_id=event.provider_envelope_id,
        status=EnvelopeStatus(raw["status"]),
        recipients=[
            RecipientState(
                recipient_key=r["recipient_key"],
                status=RecipientStatus(r["status"]),
                changed_at=datetime.fromisoformat(r["changed_at"]) if r["changed_at"] else None,
            )
            for r in raw["recipients"]
        ],
        changed_at=datetime.fromisoformat(raw["changed_at"]),
    )


def process_event(event_id: int) -> EventOutcome:
    """Aplica um evento pendente da inbox no tenant correspondente."""
    event = SignatureEvent.objects.get(id=event_id)
    if (
        event.outcome != EventOutcome.PENDING
        or event.organization_id is None
        or event.envelope_id is None
    ):
        return EventOutcome(event.outcome)
    with tenant_context(event.organization_id):
        locked = SignatureEvent.objects.select_for_update().get(id=event_id)
        if locked.outcome != EventOutcome.PENDING:
            return EventOutcome(locked.outcome)
        envelope = Envelope.objects.select_for_update().get(id=event.envelope_id)
        outcome = apply_provider_state(envelope, _state_from_event(locked))
        locked.outcome = outcome.value
        locked.processed_at = timezone.now()
        locked.save(update_fields=["outcome", "processed_at"])
    return outcome


def ingest_webhook(
    provider_name: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    provider: SignatureProvider | None = None,
) -> tuple[SignatureEvent, EventOutcome, bool]:
    """Verifica, deduplica e aplica um webhook. Devolve (evento, resultado, duplicado)."""
    selected = provider or get_provider(provider_name)
    parsed = selected.parse_webhook(headers, body)
    stored, created = record_event(provider_name, parsed)
    if not created:
        return stored, EventOutcome(stored.outcome), True
    return stored, process_event(stored.id), False


def process_pending_events(*, limit: int = 200) -> int:
    """Recupera eventos gravados cujo processamento não concluiu (queda entre etapas)."""
    pending = list(
        SignatureEvent.objects.filter(outcome=EventOutcome.PENDING)
        .order_by("received_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    for event_id in pending:
        process_event(event_id)
    return len(pending)


# ---------------------------------------------------------------------------
# Documentos concluídos, cancelamento e reconciliação
# ---------------------------------------------------------------------------
def fetch_completed_documents(
    organization_id: uuid.UUID, envelope_id: uuid.UUID, *, provider: SignatureProvider | None = None
) -> int:
    with tenant_context(organization_id):
        envelope = Envelope.objects.get(id=envelope_id)
        already = EnvelopeDocument.objects.filter(
            envelope=envelope, kind=DocumentKind.SIGNED
        ).exists()
        if envelope.state != EnvelopeState.COMPLETED or already:
            return 0
        provider_name, account, provider_id = (
            envelope.provider,
            envelope.provider_account_id,
            envelope.provider_envelope_id,
        )
    selected = _provider_for(provider_name, account, provider)
    returned = selected.download_documents(provider_id)
    with tenant_context(organization_id):
        envelope = Envelope.objects.select_for_update().get(id=envelope_id)
        if EnvelopeDocument.objects.filter(envelope=envelope, kind=DocumentKind.SIGNED).exists():
            return 0
        for document in returned:
            kind = DocumentKind.SIGNED if document.kind == "signed" else DocumentKind.EVIDENCE
            content_type = (
                "application/json" if document.name.endswith(".json") else "application/pdf"
            )
            stored = storage.store(envelope, kind, document.name, content_type, document.content)
            audit.record(
                None,
                action="signature.document_stored",
                resource_type="contract",
                resource_id=envelope.contract_id,
                changes={
                    "envelope_id": str(envelope.id),
                    "kind": kind.value,
                    "sha256": stored.sha256,
                },
            )
    return len(returned)


def request_void(principal: Principal, envelope_id: uuid.UUID, *, reason: str) -> Envelope:
    authorize(principal, Permission.SIGNATURES_SEND)
    if not reason.strip():
        raise DomainValidationError("Informe o motivo do cancelamento.")
    envelope = Envelope.objects.filter(organization_id=require_tenant(), id=envelope_id).first()
    if envelope is None:
        raise NotFoundError("Envelope não encontrado.")
    get_visible_contract(principal, envelope.contract_id, for_update=True)
    envelope = Envelope.objects.select_for_update().get(id=envelope_id)
    if envelope.state not in {EnvelopeState.SENT, EnvelopeState.DELIVERED}:
        raise InvalidTransitionError("Somente envelopes enviados podem ser cancelados.")
    envelope.state = EnvelopeState.VOIDING.value
    envelope.void_reason = reason.strip()[:300]
    envelope.updated_at = timezone.now()
    envelope.save(update_fields=["state", "void_reason", "updated_at"])
    outbox.emit(
        "signatures.void_requested",
        {"envelope_id": str(envelope.id)},
        organization_id=envelope.organization_id,
    )
    audit.record(
        principal,
        action="signature.void_requested",
        resource_type="contract",
        resource_id=envelope.contract_id,
        changes={"envelope_id": str(envelope.id), "reason": envelope.void_reason},
    )
    return envelope


def perform_void(
    organization_id: uuid.UUID, envelope_id: uuid.UUID, *, provider: SignatureProvider | None = None
) -> None:
    with tenant_context(organization_id):
        envelope = Envelope.objects.get(id=envelope_id)
        if envelope.state != EnvelopeState.VOIDING:
            return
        provider_name, account, provider_id, reason = (
            envelope.provider,
            envelope.provider_account_id,
            envelope.provider_envelope_id,
            envelope.void_reason,
        )
    _provider_for(provider_name, account, provider).void(provider_id, reason)
    with tenant_context(organization_id):
        envelope = Envelope.objects.select_for_update().get(id=envelope_id)
        if envelope.state != EnvelopeState.VOIDING:
            return
        contract = Contract.objects.select_for_update().get(id=envelope.contract_id)
        envelope.state = EnvelopeState.VOIDED.value
        envelope.updated_at = timezone.now()
        envelope.save(update_fields=["state", "updated_at"])
        if contract.drafting_status == DraftingStatus.SIGNING:
            transition(contract, DraftingStatus.APPROVED)
            contract.version += 1
            contract.save(update_fields=["drafting_status", "version"])
        audit.record(
            None,
            action="signature.voided",
            resource_type="contract",
            resource_id=contract.id,
            changes={"envelope_id": str(envelope.id)},
        )


def reconcile(*, now: datetime | None = None, provider: SignatureProvider | None = None) -> int:
    """Recupera envelopes sem confirmação ou sem eventos recentes consultando o provedor.

    Percorre as organizações uma a uma (contexto de tenant por organização) e
    fala com o provedor fora de transação. Cobre webhooks perdidos e envios
    cujo resultado ficou desconhecido após todas as retentativas.
    """
    moment = now or timezone.now()
    reconciled = 0
    for organization_id in Organization.objects.order_by("id").values_list("id", flat=True):
        with tenant_context(organization_id):
            stale = list(
                Envelope.objects.filter(
                    state__in=[
                        EnvelopeState.DISPATCH_UNKNOWN,
                        EnvelopeState.PENDING_DISPATCH,
                        EnvelopeState.SENT,
                        EnvelopeState.DELIVERED,
                    ],
                    updated_at__lt=moment - RECONCILE_AFTER,
                ).values_list(
                    "id", "state", "provider", "provider_account_id", "provider_envelope_id"
                )
            )
        for envelope_id, state, provider_name, account, provider_id in stale:
            try:
                if state in {EnvelopeState.DISPATCH_UNKNOWN, EnvelopeState.PENDING_DISPATCH}:
                    dispatch(organization_id, envelope_id, provider=provider)
                else:
                    current = _provider_for(provider_name, account, provider).get_state(provider_id)
                    with tenant_context(organization_id):
                        envelope = Envelope.objects.select_for_update().get(id=envelope_id)
                        apply_provider_state(envelope, current)
                reconciled += 1
            except (DispatchRetryError, ProviderUnavailableError, ProviderOutcomeUnknownError):
                logger.warning(
                    "signature_reconcile_deferred", extra={"envelope_id": str(envelope_id)}
                )
    return reconciled
