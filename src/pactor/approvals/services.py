"""Casos de uso de aprovação.

Ordem de bloqueio: sempre a linha do contrato primeiro e depois a rodada. A
publicação de revisão também trava o contrato, de modo que voto e nova
revisão são serializados: ou o voto vale para a revisão vigente, ou a rodada
já foi substituída e o voto é recusado.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from django.db.models import Q, QuerySet
from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal, Role
from pactor.approvals.domain import (
    CastVote,
    Decision,
    RoundEvaluation,
    RoundOutcome,
    StepSlots,
    WorkflowDefinitionError,
    evaluate_round,
    facts_from_metadata,
    parse_rules,
)
from pactor.approvals.models import (
    ApprovalRound,
    ApprovalStep,
    Delegation,
    RoundStatus,
    StepApprover,
    Vote,
    WorkflowDefinition,
)
from pactor.audit import services as audit
from pactor.contracts.domain import DraftingStatus
from pactor.contracts.models import Contract, ContractGrant, GrantSource
from pactor.contracts.selectors import get_visible_contract
from pactor.contracts.snapshots import current_fingerprint
from pactor.contracts.transitions import transition
from pactor.core import outbox
from pactor.core.errors import (
    AccessDeniedError,
    ConflictError,
    DomainValidationError,
    InvalidTransitionError,
    NotFoundError,
)
from pactor.core.tenancy import require_tenant
from pactor.identity.models import Membership, MembershipKind, MembershipStatus
from pactor.plans import services as plans
from pactor.plans.models import Module

MAX_DELEGATION_DAYS = 180


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------
def _eligible_voters(user_ids: Iterable[uuid.UUID]) -> frozenset[uuid.UUID]:
    """Usuários com associação interna ativa e papel de aprovador."""
    return frozenset(
        Membership.objects.filter(
            organization_id=require_tenant(),
            user_id__in=list(user_ids),
            status=MembershipStatus.ACTIVE,
            kind=MembershipKind.INTERNAL,
            roles__contains=[Role.APPROVER.value],
        ).values_list("user_id", flat=True)
    )


def publish_workflow(
    principal: Principal, *, name: str, rules: object, prohibit_self_approval: bool = True
) -> WorkflowDefinition:
    """Publica nova versão do workflow e a torna a única ativa da organização."""
    authorize(principal, Permission.WORKFLOWS_MANAGE)
    plans.ensure_module(Module.APPROVALS)
    try:
        parsed = parse_rules(rules)
    except WorkflowDefinitionError as exc:
        raise DomainValidationError(str(exc)) from exc
    referenced = {a for rule in parsed.rules for step in rule.steps for a in step.approver_ids}
    missing = referenced - _eligible_voters(referenced)
    if missing:
        raise DomainValidationError(
            "Aprovadores sem associação ativa ou sem papel de aprovador.",
            details={"approver_ids": sorted(str(u) for u in missing)},
        )
    organization_id = require_tenant()
    clean_name = name.strip()
    if not clean_name:
        raise DomainValidationError("Informe o nome do workflow.")
    latest = (
        WorkflowDefinition.objects.select_for_update()
        .filter(organization_id=organization_id, name=clean_name)
        .order_by("-version")
        .first()
    )
    WorkflowDefinition.objects.filter(organization_id=organization_id, is_active=True).update(
        is_active=False
    )
    workflow = WorkflowDefinition.objects.create(
        organization_id=organization_id,
        name=clean_name,
        version=(latest.version + 1) if latest else 1,
        rules=rules,
        prohibit_self_approval=prohibit_self_approval,
        published_by_id=principal.actor_id,
    )
    audit.record(
        principal,
        action="workflow.published",
        resource_type="workflow",
        resource_id=workflow.id,
        changes={"name": workflow.name, "version": workflow.version, "rules": len(parsed.rules)},
    )
    return workflow


def active_workflow() -> WorkflowDefinition:
    workflow = WorkflowDefinition.objects.filter(
        organization_id=require_tenant(), is_active=True
    ).first()
    if workflow is None:
        raise ConflictError("Nenhum workflow de aprovação publicado para a organização.")
    return workflow


# ---------------------------------------------------------------------------
# Rodadas
# ---------------------------------------------------------------------------
def _lock_contract(contract_id: uuid.UUID) -> Contract:
    contract = (
        Contract.objects.select_for_update()
        .filter(organization_id=require_tenant(), id=contract_id)
        .first()
    )
    if contract is None:
        raise NotFoundError("Contrato não encontrado.")
    return contract


def _forbidden_voters(round_: ApprovalRound) -> frozenset[uuid.UUID]:
    """Autor da revisão e solicitante não aprovam quando a regra proíbe autoaprovação."""
    if not round_.prohibit_self_approval:
        return frozenset()
    contract = round_.contract
    return frozenset(u for u in (round_.revision.author_id, contract.requester_id) if u is not None)


def _step_slots(round_: ApprovalRound) -> list[StepSlots]:
    steps = list(round_.steps.prefetch_related("approvers").order_by("order", "key"))
    active_by_step = {s.id: [a.user_id for a in s.approvers.all() if a.active] for s in steps}
    everyone = {u for users in active_by_step.values() for u in users}
    eligible = _eligible_voters(everyone) - _forbidden_voters(round_)
    return [
        StepSlots(
            key=s.key,
            order=s.order,
            quorum=s.quorum,
            approver_ids=frozenset(active_by_step[s.id]),
            eligible_ids=eligible,
        )
        for s in steps
    ]


def _evaluate(round_: ApprovalRound) -> RoundEvaluation:
    votes = [
        CastVote(step_key=v.step.key, approver_id=v.approver_id, decision=Decision(v.decision))
        for v in round_.votes.select_related("step")
    ]
    evaluation = evaluate_round(_step_slots(round_), votes)
    for step in round_.steps.all():
        state = evaluation.steps.get(step.key)
        if state is not None and step.state != state.value:
            step.state = state.value
            step.save(update_fields=["state"])
    return evaluation


def _grant_approvers(round_: ApprovalRound, user_ids: Iterable[uuid.UUID]) -> None:
    ContractGrant.objects.bulk_create(
        [
            ContractGrant(
                organization_id=round_.organization_id,
                contract=round_.contract,
                user_id=user_id,
                source=GrantSource.APPROVAL,
                source_id=round_.id,
                granted_by_id=round_.opened_by_id,
            )
            for user_id in set(user_ids)
        ],
        ignore_conflicts=True,
    )


def _close(round_: ApprovalRound, status: RoundStatus, reason: str) -> None:
    round_.status = status.value
    round_.closed_at = timezone.now()
    round_.close_reason = reason
    round_.save(update_fields=["status", "closed_at", "close_reason"])
    ContractGrant.objects.filter(
        organization_id=round_.organization_id, source=GrantSource.APPROVAL, source_id=round_.id
    ).delete()
    outbox.emit(
        "approvals.round_closed",
        {
            "round_id": str(round_.id),
            "contract_id": str(round_.contract_id),
            "status": status.value,
        },
        organization_id=round_.organization_id,
    )


def submit_for_approval(
    principal: Principal, contract_id: uuid.UUID, *, expected_fingerprint: str
) -> ApprovalRound:
    """Abre rodada para a revisão vigente, exatamente como o solicitante a viu."""
    authorize(principal, Permission.CONTRACTS_SUBMIT)
    plans.ensure_module(Module.APPROVALS)
    get_visible_contract(principal, contract_id)
    contract = _lock_contract(contract_id)
    revision = contract.current_revision
    if revision is None:
        raise InvalidTransitionError("Publique uma revisão antes de enviar para aprovação.")
    if revision.fingerprint != expected_fingerprint:
        raise ConflictError(
            "A revisão vigente mudou desde a sua leitura.",
            details={"current_fingerprint": revision.fingerprint},
        )
    if current_fingerprint(contract, revision) != revision.fingerprint:
        # Dados ou anexos mudaram depois da publicação: aprovar a revisão
        # aprovaria condições (e escolheria a regra do workflow) que já não
        # são as do contrato.
        raise ConflictError(
            f"Os dados ou anexos do contrato mudaram depois da revisão {revision.number}. "
            "Publique uma nova revisão para enviá-la à aprovação.",
            details={"revision_number": revision.number},
        )
    transition(contract, DraftingStatus.IN_APPROVAL)
    workflow = active_workflow()
    rules = parse_rules(workflow.rules)
    rule_index, rule = rules.select(facts_from_metadata(revision.metadata))
    round_ = ApprovalRound.objects.create(
        organization_id=contract.organization_id,
        contract=contract,
        revision=revision,
        fingerprint=revision.fingerprint,
        workflow=workflow,
        workflow_version=workflow.version,
        rule_index=rule_index,
        rule_snapshot=workflow.rules[rule_index],
        prohibit_self_approval=workflow.prohibit_self_approval,
        opened_by_id=principal.actor_id,
    )
    for step in rule.steps:
        created = ApprovalStep.objects.create(
            organization_id=contract.organization_id,
            round=round_,
            key=step.key,
            name=step.name,
            order=step.order,
            quorum=step.quorum,
        )
        StepApprover.objects.bulk_create(
            StepApprover(organization_id=contract.organization_id, step=created, user_id=user_id)
            for user_id in step.approver_ids
        )
    _grant_approvers(round_, (a for s in rule.steps for a in s.approver_ids))
    evaluation = _evaluate(round_)
    contract.version += 1
    contract.save(update_fields=["drafting_status", "version"])
    outbox.emit(
        "approvals.round_opened",
        {
            "round_id": str(round_.id),
            "contract_id": str(contract.id),
            "revision_id": str(revision.id),
        },
        organization_id=contract.organization_id,
    )
    audit.record(
        principal,
        action="approval.submitted",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "round_id": str(round_.id),
            "revision": revision.number,
            "fingerprint": revision.fingerprint,
            "workflow": f"{workflow.name} v{workflow.version}",
            "rule": rule.name,
            "active_steps": sorted(evaluation.active_steps()),
        },
    )
    return round_


def _valid_delegation(
    delegator_id: uuid.UUID, delegate_id: uuid.UUID, moment: datetime
) -> Delegation | None:
    return (
        Delegation.objects.filter(
            organization_id=require_tenant(),
            delegator_id=delegator_id,
            delegate_id=delegate_id,
            starts_at__lte=moment,
            ends_at__gt=moment,
            revoked_at__isnull=True,
        )
        .order_by("starts_at", "id")
        .first()
    )


def cast_vote(
    principal: Principal,
    round_id: uuid.UUID,
    *,
    step_key: str,
    decision: Decision,
    expected_fingerprint: str,
    on_behalf_of: uuid.UUID | None = None,
    comment: str = "",
    now: datetime | None = None,
) -> Vote:
    authorize(principal, Permission.APPROVALS_VOTE)
    moment = now or timezone.now()
    head = (
        ApprovalRound.objects.filter(organization_id=require_tenant(), id=round_id)
        .only("contract_id")
        .first()
    )
    if head is None:
        raise NotFoundError("Rodada não encontrada.")
    contract = _lock_contract(head.contract_id)
    round_ = (
        ApprovalRound.objects.select_for_update()
        .select_related("revision", "contract")
        .get(id=round_id)
    )
    if round_.status != RoundStatus.OPEN:
        raise ConflictError("Rodada encerrada.", details={"status": round_.status})
    if (
        contract.current_revision_id != round_.revision_id
        or round_.fingerprint != expected_fingerprint
    ):
        raise ConflictError(
            "O voto não corresponde à revisão em aprovação.",
            details={"round_fingerprint": round_.fingerprint},
        )
    approver_id = on_behalf_of or principal.actor_id
    delegation = None
    if on_behalf_of is not None:
        delegation = _valid_delegation(on_behalf_of, principal.actor_id, moment)
        if delegation is None:
            raise AccessDeniedError("Delegação inexistente, expirada ou revogada.")
    forbidden = _forbidden_voters(round_)
    if principal.actor_id in forbidden or approver_id in forbidden:
        raise AccessDeniedError("Autoaprovação não permitida nesta rodada.")
    step = round_.steps.filter(key=step_key).first()
    if step is None:
        raise NotFoundError("Etapa não encontrada.")
    if not StepApprover.objects.filter(step=step, user_id=approver_id, active=True).exists():
        raise AccessDeniedError("Você não é aprovador desta etapa.")
    if approver_id not in _eligible_voters([approver_id]):
        raise AccessDeniedError("Aprovador sem associação ativa ou sem papel de aprovador.")
    evaluation = _evaluate(round_)
    if step_key not in evaluation.active_steps():
        raise ConflictError("Etapa não está aguardando votos.", details={"state": step.state})
    if (
        Vote.objects.filter(step=step)
        .filter(Q(approver_id=approver_id) | Q(cast_by_id=principal.actor_id))
        .exists()
    ):
        raise ConflictError("Voto já registrado para esta vaga ou por você nesta etapa.")
    vote = Vote.objects.create(
        organization_id=round_.organization_id,
        round=round_,
        step=step,
        approver_id=approver_id,
        cast_by_id=principal.actor_id,
        delegation=delegation,
        decision=decision.value,
        comment=comment.strip()[:2000],
        revision=round_.revision,
        fingerprint=round_.fingerprint,
        cast_at=moment,
    )
    outcome = _evaluate(round_).outcome
    _apply_outcome(contract, round_, outcome)
    audit.record(
        principal,
        action="approval.vote_cast",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "round_id": str(round_.id),
            "step": step_key,
            "decision": decision.value,
            "on_behalf_of": str(on_behalf_of) if on_behalf_of else None,
            "outcome": outcome.value,
        },
    )
    return vote


def _apply_outcome(contract: Contract, round_: ApprovalRound, outcome: RoundOutcome) -> None:
    if outcome is RoundOutcome.OPEN:
        return
    if outcome is RoundOutcome.APPROVED:
        transition(contract, DraftingStatus.APPROVED)
        contract.approved_revision = round_.revision
        contract.approved_fingerprint = round_.fingerprint
        _close(round_, RoundStatus.APPROVED, "quorum")
    else:
        transition(contract, DraftingStatus.DRAFT)
        _close(round_, RoundStatus(outcome.value), outcome.value)
    contract.version += 1
    contract.updated_at = timezone.now()
    contract.save(
        update_fields=[
            "drafting_status",
            "approved_revision",
            "approved_fingerprint",
            "version",
            "updated_at",
        ]
    )


def supersede_open_rounds(contract: Contract, *, reason: str) -> int:
    """Encerra rodadas abertas quando o conteúdo submetido deixa de ser o vigente.

    Chamada com o contrato já travado, na mesma transação da mudança.
    """
    rounds = list(
        ApprovalRound.objects.select_for_update().filter(
            organization_id=contract.organization_id, contract=contract, status=RoundStatus.OPEN
        )
    )
    for round_ in rounds:
        _close(round_, RoundStatus.SUPERSEDED, reason)
    return len(rounds)


def replace_approver(
    principal: Principal,
    round_id: uuid.UUID,
    *,
    step_key: str,
    old_user_id: uuid.UUID,
    new_user_id: uuid.UUID,
    reason: str,
) -> StepApprover:
    """Substitui aprovador (perda de acesso, afastamento definitivo) em rodada aberta."""
    authorize(principal, Permission.WORKFLOWS_MANAGE)
    if not reason.strip():
        raise DomainValidationError("Informe a justificativa da substituição.")
    head = ApprovalRound.objects.filter(organization_id=require_tenant(), id=round_id).first()
    if head is None:
        raise NotFoundError("Rodada não encontrada.")
    contract = _lock_contract(head.contract_id)
    round_ = (
        ApprovalRound.objects.select_for_update()
        .select_related("revision", "contract")
        .get(id=round_id)
    )
    if round_.status != RoundStatus.OPEN:
        raise ConflictError("Rodada encerrada.")
    step = round_.steps.filter(key=step_key).first()
    if step is None:
        raise NotFoundError("Etapa não encontrada.")
    slot = (
        StepApprover.objects.select_for_update()
        .filter(step=step, user_id=old_user_id, active=True)
        .first()
    )
    if slot is None:
        raise NotFoundError("Aprovador não encontrado na etapa.")
    if Vote.objects.filter(step=step, approver_id=old_user_id).exists():
        raise ConflictError("Aprovador já votou; o voto registrado é preservado.")
    if new_user_id not in _eligible_voters([new_user_id]) or new_user_id in _forbidden_voters(
        round_
    ):
        raise DomainValidationError(
            "Substituto deve ser aprovador ativo e não pode ser autor ou solicitante."
        )
    if StepApprover.objects.filter(step=step, user_id=new_user_id).exists():
        raise ConflictError("Substituto já ocupa vaga nesta etapa.")
    slot.active = False
    slot.replaced_reason = reason.strip()[:300]
    slot.save(update_fields=["active", "replaced_reason"])
    new_slot = StepApprover.objects.create(
        organization_id=round_.organization_id, step=step, user_id=new_user_id
    )
    ContractGrant.objects.filter(
        organization_id=round_.organization_id,
        source=GrantSource.APPROVAL,
        source_id=round_.id,
        user_id=old_user_id,
    ).delete()
    _grant_approvers(round_, [new_user_id])
    _apply_outcome(contract, round_, _evaluate(round_).outcome)
    audit.record(
        principal,
        action="approval.approver_replaced",
        resource_type="contract",
        resource_id=contract.id,
        changes={
            "round_id": str(round_.id),
            "step": step_key,
            "old": str(old_user_id),
            "new": str(new_user_id),
            "reason": slot.replaced_reason,
        },
    )
    return new_slot


# ---------------------------------------------------------------------------
# Delegações
# ---------------------------------------------------------------------------
def create_delegation(
    principal: Principal,
    *,
    delegate_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    reason: str,
) -> Delegation:
    authorize(principal, Permission.APPROVALS_VOTE)
    if delegate_id == principal.actor_id:
        raise DomainValidationError("Não é possível delegar para si mesmo.")
    if ends_at <= starts_at or (ends_at - starts_at).days > MAX_DELEGATION_DAYS:
        raise DomainValidationError(f"Período inválido (máximo de {MAX_DELEGATION_DAYS} dias).")
    if not reason.strip():
        raise DomainValidationError("Informe o motivo da delegação.")
    if delegate_id not in _eligible_voters([delegate_id]):
        raise DomainValidationError("O delegado deve ser aprovador ativo da organização.")
    overlapping = Delegation.objects.filter(
        organization_id=require_tenant(),
        delegator_id=principal.actor_id,
        revoked_at__isnull=True,
        starts_at__lt=ends_at,
        ends_at__gt=starts_at,
    ).exists()
    if overlapping:
        raise ConflictError("Já existe delegação ativa nesse período.")
    delegation = Delegation.objects.create(
        organization_id=require_tenant(),
        delegator_id=principal.actor_id,
        delegate_id=delegate_id,
        starts_at=starts_at,
        ends_at=ends_at,
        reason=reason.strip()[:300],
    )
    audit.record(
        principal,
        action="delegation.created",
        resource_type="delegation",
        resource_id=delegation.id,
        changes={
            "delegate": str(delegate_id),
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
        },
    )
    return delegation


def revoke_delegation(principal: Principal, delegation_id: uuid.UUID) -> Delegation:
    delegation = (
        Delegation.objects.select_for_update()
        .filter(organization_id=require_tenant(), id=delegation_id, delegator_id=principal.actor_id)
        .first()
    )
    if delegation is None:
        raise NotFoundError("Delegação não encontrada.")
    if delegation.revoked_at is not None:
        raise ConflictError("Delegação já revogada.")
    delegation.revoked_at = timezone.now()
    delegation.save(update_fields=["revoked_at"])
    audit.record(
        principal,
        action="delegation.revoked",
        resource_type="delegation",
        resource_id=delegation.id,
    )
    return delegation


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def rounds_of_contract(principal: Principal, contract_id: uuid.UUID) -> QuerySet[ApprovalRound]:
    contract = get_visible_contract(principal, contract_id)
    return (
        ApprovalRound.objects.filter(organization_id=contract.organization_id, contract=contract)
        .select_related("revision", "workflow")
        .prefetch_related("steps__approvers", "votes")
        .order_by("-created_at", "-id")
    )


def inbox(principal: Principal, *, now: datetime | None = None) -> QuerySet[ApprovalRound]:
    """Rodadas abertas em que o principal pode votar, diretamente ou por delegação."""
    moment = now or timezone.now()
    delegators = Delegation.objects.filter(
        organization_id=principal.organization_id,
        delegate_id=principal.actor_id,
        starts_at__lte=moment,
        ends_at__gt=moment,
        revoked_at__isnull=True,
    ).values("delegator_id")
    return (
        ApprovalRound.objects.filter(
            organization_id=principal.organization_id,
            status=RoundStatus.OPEN,
        )
        .filter(
            Q(steps__approvers__user_id=principal.actor_id, steps__approvers__active=True)
            | Q(steps__approvers__user_id__in=delegators, steps__approvers__active=True)
        )
        .distinct()
        .select_related("revision", "contract", "contract__counterparty")
        .prefetch_related("steps__approvers", "votes")
        .order_by("created_at", "id")
    )


def require_approved_for_signature(contract: Contract) -> None:
    """Bloqueia o envio para assinatura enquanto houver pendências obrigatórias."""
    revision = contract.current_revision
    if (
        DraftingStatus(contract.drafting_status) is not DraftingStatus.APPROVED
        or revision is None
        or contract.approved_revision_id != revision.id
        or contract.approved_fingerprint != revision.fingerprint
    ):
        raise InvalidTransitionError("Contrato sem aprovação vigente para a revisão atual.")
    if ApprovalRound.objects.filter(
        organization_id=contract.organization_id, contract=contract, status=RoundStatus.OPEN
    ).exists():
        raise InvalidTransitionError("Há rodada de aprovação em andamento.")
