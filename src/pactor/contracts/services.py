"""Casos de uso de contratos, rascunhos, revisões, comentários e compartilhamento.

Limites transacionais: cada função espera a transação da requisição (ou de
``tenant_context``). Operações que mudam a revisão vigente bloqueiam a linha
do contrato (``SELECT ... FOR UPDATE``), o que serializa publicação de
revisões, envio para aprovação, votos e envio para assinatura.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import F
from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Confidentiality, Permission, Principal
from pactor.approvals import services as approvals
from pactor.audit import services as audit
from pactor.contracts.diff import TextDiff, compare_text
from pactor.contracts.domain import (
    Canonical,
    ContractType,
    DraftingStatus,
    PartyRole,
    SubmissionSnapshot,
    allows_new_revision,
    sha256_text,
)
from pactor.contracts.models import (
    AmendmentChange,
    Comment,
    Contract,
    ContractDraft,
    ContractGrant,
    ContractSequence,
    GrantSource,
    Revision,
    RevisionSource,
)
from pactor.contracts.selectors import get_revision, get_visible_contract, require_editable
from pactor.core import outbox
from pactor.core.errors import (
    ConflictError,
    DomainValidationError,
    InvalidTransitionError,
    NotFoundError,
    StaleVersionError,
)
from pactor.core.tenancy import require_tenant
from pactor.counterparties.models import Counterparty
from pactor.files.selectors import submission_attachments
from pactor.identity.models import Department, Membership, MembershipKind, MembershipStatus, Team
from pactor.plans import services as plans

MAX_CONTENT_LENGTH = 500_000
MAX_COMMENT_LENGTH = 5_000
MAX_TAG_LENGTH = 40
MAX_VALUE = Decimal("999999999999.99")


@dataclass(frozen=True, slots=True)
class ContractInput:
    title: str
    contract_type: ContractType
    counterparty_id: uuid.UUID
    organization_role: PartyRole
    counterparty_role: PartyRole
    owner_id: uuid.UUID
    department_id: uuid.UUID | None = None
    requester_id: uuid.UUID | None = None
    confidentiality: Confidentiality = Confidentiality.INTERNAL
    classification: str = ""
    tags: tuple[str, ...] = ()
    total_value: Decimal | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    parent_id: uuid.UUID | None = None
    initial_content: str = ""


def _active_internal_member(user_id: uuid.UUID, *, field: str) -> None:
    exists = Membership.objects.filter(
        organization_id=require_tenant(),
        user_id=user_id,
        status=MembershipStatus.ACTIVE,
        kind=MembershipKind.INTERNAL,
    ).exists()
    if not exists:
        raise DomainValidationError(
            "Usuário não é membro interno ativo.", details={field: [str(user_id)]}
        )


def _validate_value(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if not value.is_finite():
        raise DomainValidationError("Valor deve ser finito.", details={"total_value": [str(value)]})
    if value < 0 or value > MAX_VALUE:
        raise DomainValidationError(
            "Valor fora dos limites.", details={"total_value": [str(value)]}
        )
    if value != value.quantize(Decimal("0.01")):
        raise DomainValidationError("Valor deve ter no máximo duas casas decimais.")
    return value


def _validate_dates(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise DomainValidationError("Término planejado anterior ao início.")


def _normalized_tags(tags: Iterable[str]) -> list[str]:
    cleaned = sorted({t.strip().lower() for t in tags if t.strip()})
    if any(len(t) > MAX_TAG_LENGTH for t in cleaned):
        raise DomainValidationError("Etiquetas têm no máximo 40 caracteres.")
    return cleaned


def _next_reference(today: date) -> str:
    organization_id = require_tenant()
    sequence, _ = ContractSequence.objects.select_for_update().get_or_create(
        organization_id=organization_id, year=today.year
    )
    sequence.last_value += 1
    sequence.save(update_fields=["last_value"])
    return f"CT-{today.year}-{sequence.last_value:05d}"


def _check_content(content: str) -> str:
    if len(content) > MAX_CONTENT_LENGTH:
        raise DomainValidationError(
            "Conteúdo excede o tamanho máximo.", details={"max": MAX_CONTENT_LENGTH}
        )
    return content


def create_contract(principal: Principal, data: ContractInput) -> Contract:
    authorize(principal, Permission.CONTRACTS_CREATE)
    plans.ensure_writable()
    organization_id = require_tenant()
    if not data.title.strip():
        raise DomainValidationError("Informe o título do contrato.")
    counterparty = Counterparty.objects.filter(
        organization_id=organization_id, id=data.counterparty_id, is_active=True
    ).first()
    if counterparty is None:
        raise DomainValidationError("Contraparte inexistente ou inativa.")
    if (
        data.department_id is not None
        and not Department.objects.filter(
            organization_id=organization_id, id=data.department_id, is_active=True
        ).exists()
    ):
        raise DomainValidationError("Departamento inexistente.")
    _active_internal_member(data.owner_id, field="owner_id")
    if data.requester_id is not None:
        _active_internal_member(data.requester_id, field="requester_id")
    parent: Contract | None = None
    if data.contract_type is ContractType.AMENDMENT:
        if data.parent_id is None:
            raise DomainValidationError("Aditivo exige contrato principal.")
        parent = get_visible_contract(principal, data.parent_id)
    elif data.parent_id is not None:
        raise DomainValidationError("Somente aditivos referenciam contrato principal.")
    _validate_dates(data.planned_start, data.planned_end)
    contract = Contract.objects.create(
        organization_id=organization_id,
        reference=_next_reference(timezone.localdate()),
        title=data.title.strip(),
        contract_type=data.contract_type.value,
        counterparty=counterparty,
        organization_role=data.organization_role.value,
        counterparty_role=data.counterparty_role.value,
        department_id=data.department_id,
        owner_id=data.owner_id,
        requester_id=data.requester_id,
        confidentiality=data.confidentiality.value,
        classification=data.classification.strip(),
        tags=_normalized_tags(data.tags),
        total_value=_validate_value(data.total_value),
        planned_start=data.planned_start,
        planned_end=data.planned_end,
        parent=parent,
    )
    ContractDraft.objects.create(
        organization_id=organization_id,
        contract=contract,
        content=_check_content(data.initial_content),
        updated_by_id=principal.actor_id,
    )
    if principal.actor_id not in {data.owner_id, data.requester_id} and (
        data.confidentiality is Confidentiality.CONFIDENTIAL
    ):
        # Quem cria um contrato confidencial em nome de outro responsável
        # mantém acesso de edição explícito e auditado.
        ContractGrant.objects.create(
            organization_id=organization_id,
            contract=contract,
            user_id=principal.actor_id,
            can_edit=True,
            granted_by_id=principal.actor_id,
        )
    outbox.emit(
        "contracts.created", {"contract_id": str(contract.id)}, organization_id=organization_id
    )
    audit.record(
        principal,
        action="contract.created",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "reference": contract.reference,
            "title": contract.title,
            "type": contract.contract_type,
        },
    )
    return contract


_METADATA_FIELDS = frozenset(
    {
        "title",
        "department_id",
        "owner_id",
        "confidentiality",
        "classification",
        "tags",
        "total_value",
        "planned_start",
        "planned_end",
        "organization_role",
        "counterparty_role",
    }
)


def update_metadata(
    principal: Principal,
    contract_id: uuid.UUID,
    *,
    expected_version: int,
    changes: Mapping[str, object],
) -> Contract:
    """Altera metadados. Em contrato aprovado, a aprovação deixa de valer."""
    authorize(principal, Permission.CONTRACTS_EDIT)
    plans.ensure_writable()
    unknown = set(changes) - _METADATA_FIELDS
    if unknown:
        raise DomainValidationError("Campos não editáveis.", details={"fields": sorted(unknown)})
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    if contract.version != expected_version:
        raise StaleVersionError(
            "O contrato foi alterado por outra pessoa. Recarregue antes de salvar.",
            details={"current_version": contract.version},
        )
    if not allows_new_revision(DraftingStatus(contract.drafting_status)):
        raise InvalidTransitionError("Contrato assinado ou cancelado: alterações exigem aditivo.")
    normalized = dict(changes)
    if "tags" in normalized:
        tags = normalized["tags"]
        normalized["tags"] = _normalized_tags(tags if isinstance(tags, list) else [])
    if "total_value" in normalized:
        value = normalized["total_value"]
        normalized["total_value"] = _validate_value(value if isinstance(value, Decimal) else None)
    if "owner_id" in normalized and isinstance(normalized["owner_id"], uuid.UUID):
        _active_internal_member(normalized["owner_id"], field="owner_id")
    before: dict[str, object] = {}
    for field_name, value in normalized.items():
        before[field_name] = getattr(contract, field_name)
        setattr(contract, field_name, value)
    _validate_dates(contract.planned_start, contract.planned_end)
    invalidated = _invalidate_approval(contract, reason="metadata_changed")
    contract.version += 1
    contract.updated_at = timezone.now()
    contract.save()
    audit.record(
        principal,
        action="contract.metadata_updated",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "fields": {
                k: {"before": str(before[k]), "after": str(v)} for k, v in normalized.items()
            },
            "approval_invalidated": invalidated,
        },
    )
    return contract


def _invalidate_approval(contract: Contract, *, reason: str) -> bool:
    """Devolve o contrato à elaboração quando o conteúdo aprovado deixa de valer.

    Rodadas abertas para o conteúdo anterior são encerradas na mesma
    transação; uma aprovação concedida antes não cobre o novo conteúdo.
    """
    status = DraftingStatus(contract.drafting_status)
    if status not in {DraftingStatus.IN_APPROVAL, DraftingStatus.APPROVED}:
        return False
    approvals.supersede_open_rounds(contract, reason=reason)
    contract.drafting_status = DraftingStatus.DRAFT.value
    contract.approved_revision = None
    contract.approved_fingerprint = ""
    return True


def invalidate_for_content_change(contract: Contract, *, reason: str) -> bool:
    """Invalida aprovação/rodada quando dados submetidos mudam fora do rascunho."""
    invalidated = _invalidate_approval(contract, reason=reason)
    if invalidated:
        contract.version += 1
        contract.updated_at = timezone.now()
        contract.save()
    return invalidated


def get_draft(principal: Principal, contract_id: uuid.UUID) -> ContractDraft:
    contract = get_visible_contract(principal, contract_id)
    return ContractDraft.objects.get(organization_id=contract.organization_id, contract=contract)


def save_draft(
    principal: Principal, contract_id: uuid.UUID, *, content: str, expected_version: int
) -> ContractDraft:
    """Grava o rascunho sem sobrescrever alterações concorrentes."""
    authorize(principal, Permission.CONTRACTS_EDIT)
    plans.ensure_writable()
    contract = get_visible_contract(principal, contract_id)
    require_editable(principal, contract)
    if not allows_new_revision(DraftingStatus(contract.drafting_status)):
        raise InvalidTransitionError("Contrato assinado ou cancelado: alterações exigem aditivo.")
    updated = ContractDraft.objects.filter(
        organization_id=contract.organization_id, contract=contract, version=expected_version
    ).update(
        content=_check_content(content),
        version=F("version") + 1,
        updated_by_id=principal.actor_id,
        updated_at=timezone.now(),
    )
    draft = ContractDraft.objects.get(organization_id=contract.organization_id, contract=contract)
    if updated == 0:
        raise StaleVersionError(
            "O rascunho foi alterado por outra pessoa. Recarregue antes de salvar.",
            details={"current_version": draft.version},
        )
    return draft


def metadata_snapshot(contract: Contract) -> dict[str, Canonical]:
    """Metadados relevantes cobertos pela aprovação."""
    return {
        "title": contract.title,
        "contract_type": contract.contract_type,
        "counterparty_id": str(contract.counterparty_id),
        "counterparty_document": contract.counterparty.document,
        "organization_role": contract.organization_role,
        "counterparty_role": contract.counterparty_role,
        "department_id": str(contract.department_id) if contract.department_id else None,
        "confidentiality": contract.confidentiality,
        "classification": contract.classification,
        "total_value": str(contract.total_value) if contract.total_value is not None else None,
        "currency": contract.currency,
        "planned_start": contract.planned_start.isoformat() if contract.planned_start else None,
        "planned_end": contract.planned_end.isoformat() if contract.planned_end else None,
        "parent_id": str(contract.parent_id) if contract.parent_id else None,
        # Alterações de condição de um aditivo fazem parte do que é aprovado e assinado.
        "amendment_changes": [
            {"field": c.field, "value": c.value, "effective_from": c.effective_from.isoformat()}
            for c in AmendmentChange.objects.filter(
                organization_id=contract.organization_id, amendment_id=contract.id
            ).order_by("created_at", "id")
        ],
    }


def publish_revision(
    principal: Principal,
    contract_id: uuid.UUID,
    *,
    expected_draft_version: int,
    summary: str = "",
    source: RevisionSource = RevisionSource.MANUAL,
    template_version_id: uuid.UUID | None = None,
    generation_data: Mapping[str, Canonical] | None = None,
) -> Revision:
    """Publica o rascunho como revisão imutável.

    ``expected_draft_version`` garante que o autor publica exatamente o
    conteúdo que viu. Se houver rodada de aprovação aberta ou aprovação
    vigente para a revisão anterior, ela deixa de valer na mesma transação.
    """
    authorize(principal, Permission.CONTRACTS_EDIT)
    plans.ensure_writable()
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    if not allows_new_revision(DraftingStatus(contract.drafting_status)):
        raise InvalidTransitionError("Contrato assinado ou cancelado: alterações exigem aditivo.")
    draft = ContractDraft.objects.select_for_update().get(
        organization_id=contract.organization_id, contract=contract
    )
    if draft.version != expected_draft_version:
        raise StaleVersionError(
            "O rascunho mudou desde a sua leitura.", details={"current_version": draft.version}
        )
    if not draft.content.strip():
        raise DomainValidationError("Rascunho vazio não pode ser publicado.")
    metadata = metadata_snapshot(contract)
    attachments = submission_attachments(contract)
    content_sha = sha256_text(draft.content)
    fingerprint = SubmissionSnapshot(content_sha, metadata, attachments).fingerprint()
    current = contract.current_revision
    if current is not None and current.fingerprint == fingerprint:
        raise ConflictError("Nenhuma alteração desde a última revisão.")
    revision = Revision.objects.create(
        organization_id=contract.organization_id,
        contract=contract,
        number=(current.number + 1) if current else 1,
        previous=current,
        source=source.value,
        content=draft.content,
        content_sha256=content_sha,
        metadata=metadata,
        attachments=[
            {"file_id": a.file_id, "name": a.name, "sha256": a.sha256} for a in attachments
        ],
        fingerprint=fingerprint,
        author_id=principal.actor_id,
        summary=summary.strip()[:300],
        template_version_id=template_version_id,
        generation_data=dict(generation_data) if generation_data is not None else None,
    )
    invalidated = _invalidate_approval(contract, reason="revision_published")
    contract.current_revision = revision
    contract.version += 1
    contract.updated_at = timezone.now()
    contract.save()
    outbox.emit(
        "contracts.revision_published",
        {
            "contract_id": str(contract.id),
            "revision_id": str(revision.id),
            "number": revision.number,
        },
        organization_id=contract.organization_id,
    )
    audit.record(
        principal,
        action="revision.published",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "revision": revision.number,
            "fingerprint": fingerprint,
            "approval_invalidated": invalidated,
        },
    )
    return revision


def compare_revisions(
    principal: Principal, contract_id: uuid.UUID, old_id: uuid.UUID, new_id: uuid.UUID
) -> TextDiff:
    contract = get_visible_contract(principal, contract_id)
    return compare_text(
        get_revision(contract, old_id).content, get_revision(contract, new_id).content
    )


def add_comment(
    principal: Principal,
    contract_id: uuid.UUID,
    revision_id: uuid.UUID,
    *,
    body: str,
    line: int | None,
) -> Comment:
    authorize(principal, Permission.CONTRACTS_COMMENT)
    plans.ensure_writable()
    contract = get_visible_contract(principal, contract_id)
    revision = get_revision(contract, revision_id)
    text = body.strip()
    if not text or len(text) > MAX_COMMENT_LENGTH:
        raise DomainValidationError("Comentário vazio ou acima de 5000 caracteres.")
    if line is not None and not 1 <= line <= max(1, revision.content.count("\n") + 1):
        raise DomainValidationError("Linha inexistente na revisão.")
    comment = Comment.objects.create(
        organization_id=contract.organization_id,
        revision=revision,
        author_id=principal.actor_id,
        body=text,
        line=line,
    )
    audit.record(
        principal,
        action="comment.created",
        resource_type="contract",
        resource_id=contract.id,
        changes={"revision": revision.number, "comment_id": str(comment.id)},
    )
    return comment


def resolve_comment(principal: Principal, contract_id: uuid.UUID, comment_id: uuid.UUID) -> Comment:
    authorize(principal, Permission.CONTRACTS_COMMENT)
    contract = get_visible_contract(principal, contract_id)
    comment = (
        Comment.objects.select_for_update()
        .filter(
            organization_id=contract.organization_id, revision__contract=contract, id=comment_id
        )
        .first()
    )
    if comment is None:
        raise NotFoundError("Comentário não encontrado.")
    if comment.resolved_at is not None:
        raise ConflictError("Comentário já resolvido.")
    comment.resolved_at = timezone.now()
    comment.resolved_by_id = principal.actor_id
    comment.save(update_fields=["resolved_at", "resolved_by"])
    return comment


def share(
    principal: Principal,
    contract_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    can_edit: bool = False,
) -> ContractGrant:
    authorize(principal, Permission.CONTRACTS_SHARE)
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    if user_id is not None and team_id is not None:
        raise DomainValidationError("Informe um usuário ou uma equipe, não ambos.")
    organization_id = contract.organization_id
    if user_id is not None:
        membership = Membership.objects.filter(
            organization_id=organization_id, user_id=user_id, status=MembershipStatus.ACTIVE
        ).first()
        if membership is None:
            raise DomainValidationError("Usuário não participa da organização.")
        if membership.kind == MembershipKind.EXTERNAL and can_edit:
            raise DomainValidationError("Participantes externos recebem somente leitura.")
        existing = ContractGrant.objects.filter(
            organization_id=organization_id,
            contract=contract,
            user_id=user_id,
            source=GrantSource.MANUAL,
        )
    elif team_id is not None:
        if not Team.objects.filter(organization_id=organization_id, id=team_id).exists():
            raise DomainValidationError("Equipe inexistente.")
        existing = ContractGrant.objects.filter(
            organization_id=organization_id, contract=contract, team_id=team_id
        )
    else:
        raise DomainValidationError("Informe um usuário ou uma equipe.")
    grant = existing.first()
    if grant is None:
        grant = ContractGrant.objects.create(
            organization_id=organization_id,
            contract=contract,
            user_id=user_id,
            team_id=team_id,
            can_edit=can_edit,
            granted_by_id=principal.actor_id,
        )
    elif grant.can_edit != can_edit:
        grant.can_edit = can_edit
        grant.save(update_fields=["can_edit"])
    audit.record(
        principal,
        action="contract.shared",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "user_id": str(user_id) if user_id else None,
            "team_id": str(team_id) if team_id else None,
            "can_edit": can_edit,
        },
    )
    return grant


def unshare(principal: Principal, contract_id: uuid.UUID, grant_id: uuid.UUID) -> None:
    authorize(principal, Permission.CONTRACTS_SHARE)
    contract = get_visible_contract(principal, contract_id, for_update=True)
    require_editable(principal, contract)
    deleted, _ = ContractGrant.objects.filter(
        organization_id=contract.organization_id,
        contract=contract,
        id=grant_id,
        source=GrantSource.MANUAL,
    ).delete()
    if deleted == 0:
        raise NotFoundError("Compartilhamento não encontrado.")
    audit.record(
        principal,
        action="contract.unshared",
        resource_type="contract",
        resource_id=contract.id,
        changes={"grant_id": str(grant_id)},
    )
