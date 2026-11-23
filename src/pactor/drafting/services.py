"""Casos de uso de modelos: edição do rascunho, publicação de versões e geração.

A geração grava o texto renderizado como nova revisão do contrato, com a
versão do modelo e os dados usados. Revisões são imutáveis: publicar uma
nova versão do modelo não altera nenhum contrato já produzido.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from django.db.models import F, QuerySet
from django.utils import timezone

from pactor.access.authorization import authorize
from pactor.access.policies import Permission, Principal
from pactor.audit import services as audit
from pactor.contracts import services as contracts
from pactor.contracts.domain import ContractType
from pactor.contracts.models import Revision, RevisionSource
from pactor.contracts.selectors import get_visible_contract
from pactor.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    StaleVersionError,
)
from pactor.core.tenancy import require_tenant
from pactor.drafting.domain import (
    GenerationDataError,
    TemplateDefinitionError,
    canonical_data,
    parse_definition,
    render,
    validate_data,
)
from pactor.drafting.models import ContractTemplate, TemplateVersion
from pactor.plans import services as plans


def _validated_definition(definition: object) -> None:
    try:
        parse_definition(definition)
    except TemplateDefinitionError as exc:
        raise DomainValidationError(str(exc)) from exc


def templates_for(principal: Principal) -> QuerySet[ContractTemplate]:
    authorize(principal, Permission.CONTRACTS_READ)
    return ContractTemplate.objects.filter(organization_id=principal.organization_id)


def get_template(
    principal: Principal, template_id: uuid.UUID, *, for_update: bool = False
) -> ContractTemplate:
    queryset = templates_for(principal)
    if for_update:
        queryset = queryset.select_for_update()
    template = queryset.filter(id=template_id).first()
    if template is None:
        raise NotFoundError("Modelo não encontrado.")
    return template


def create_template(
    principal: Principal,
    *,
    name: str,
    contract_type: ContractType,
    description: str,
    definition: object,
) -> ContractTemplate:
    authorize(principal, Permission.TEMPLATES_MANAGE)
    plans.ensure_writable()
    clean = name.strip()
    if not clean:
        raise DomainValidationError("Informe o nome do modelo.")
    _validated_definition(definition)
    organization_id = require_tenant()
    if ContractTemplate.objects.filter(
        organization_id=organization_id, name__iexact=clean
    ).exists():
        raise ConflictError("Já existe modelo com este nome.")
    template = ContractTemplate.objects.create(
        organization_id=organization_id,
        name=clean,
        contract_type=contract_type.value,
        description=description.strip(),
        draft_definition=definition,
        updated_by_id=principal.actor_id,
    )
    audit.record(
        principal,
        action="template.created",
        resource_type="template",
        resource_id=template.id,
        changes={"name": clean},
    )
    return template


def save_draft(
    principal: Principal, template_id: uuid.UUID, *, definition: object, expected_version: int
) -> ContractTemplate:
    authorize(principal, Permission.TEMPLATES_MANAGE)
    plans.ensure_writable()
    _validated_definition(definition)
    template = get_template(principal, template_id)
    updated = ContractTemplate.objects.filter(
        id=template.id, draft_version=expected_version
    ).update(
        draft_definition=definition,
        draft_version=F("draft_version") + 1,
        updated_by_id=principal.actor_id,
        updated_at=timezone.now(),
    )
    template.refresh_from_db()
    if updated == 0:
        raise StaleVersionError(
            "O modelo foi alterado por outra pessoa.",
            details={"current_version": template.draft_version},
        )
    return template


def publish(
    principal: Principal, template_id: uuid.UUID, *, expected_version: int, change_note: str = ""
) -> TemplateVersion:
    authorize(principal, Permission.TEMPLATES_MANAGE)
    plans.ensure_writable()
    template = get_template(principal, template_id, for_update=True)
    if template.draft_version != expected_version:
        raise StaleVersionError(
            "O modelo mudou desde a sua leitura.",
            details={"current_version": template.draft_version},
        )
    _validated_definition(template.draft_definition)
    latest = TemplateVersion.objects.filter(template=template).order_by("-number").first()
    if latest is not None and latest.definition == template.draft_definition:
        raise ConflictError("Nenhuma alteração desde a última versão publicada.")
    version = TemplateVersion.objects.create(
        organization_id=template.organization_id,
        template=template,
        number=(latest.number + 1) if latest else 1,
        definition=template.draft_definition,
        change_note=change_note.strip()[:300],
        published_by_id=principal.actor_id,
    )
    audit.record(
        principal,
        action="template.published",
        resource_type="template",
        resource_id=template.id,
        changes={"version": version.number, "note": version.change_note},
    )
    return version


def generate_revision(
    principal: Principal,
    contract_id: uuid.UUID,
    *,
    template_version_id: uuid.UUID,
    data: Mapping[str, object],
    expected_draft_version: int,
) -> Revision:
    """Renderiza a versão do modelo com os dados e publica como nova revisão."""
    authorize(principal, Permission.CONTRACTS_EDIT)
    contract = get_visible_contract(principal, contract_id)
    version = (
        TemplateVersion.objects.select_related("template")
        .filter(organization_id=principal.organization_id, id=template_version_id)
        .first()
    )
    if version is None:
        raise NotFoundError("Versão de modelo não encontrada.")
    if version.template.contract_type != contract.contract_type:
        raise DomainValidationError("O modelo não corresponde ao tipo do contrato.")
    definition = parse_definition(version.definition)
    try:
        values = validate_data(definition, data)
    except GenerationDataError as exc:
        raise DomainValidationError(
            "Dados de preenchimento inválidos.", details=exc.errors
        ) from exc
    content = render(definition, values)
    draft = contracts.save_draft(
        principal, contract_id, content=content, expected_version=expected_draft_version
    )
    return contracts.publish_revision(
        principal,
        contract_id,
        expected_draft_version=draft.version,
        summary=f"Gerada pelo modelo {version.template.name} v{version.number}",
        source=RevisionSource.TEMPLATE,
        template_version_id=version.id,
        generation_data=canonical_data(values),
    )
