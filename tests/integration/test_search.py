"""Pesquisa autorizada, facetas, sugestões, exportação e indexação por revisão."""

from __future__ import annotations

import csv
import io
import uuid

import pytest
from django.utils import timezone

from pactor.access.policies import Role
from pactor.contracts import services as contracts
from pactor.contracts.models import Contract
from pactor.core.models import OutboxEvent
from pactor.core.outbox import EventEnvelope
from pactor.core.tenancy import tenant_context
from pactor.files import services as files
from pactor.files.models import ContractAttachment, FilePurpose, FileStatus, StoredFile
from pactor.identity.models import Organization, User
from pactor.search import tasks as search_tasks
from pactor.search.indexing import index_contract
from pactor.search.models import SearchDocument
from tests import factories, file_samples

pytestmark = pytest.mark.django_db(transaction=True)


def _drain_index_events() -> None:
    """Executa os consumidores de indexação para os eventos pendentes da outbox."""
    consumers = {
        "contracts.created": search_tasks.index_current,
        "contracts.updated": search_tasks.index_current,
        "contracts.revision_published": search_tasks.index_revision,
    }
    for event in OutboxEvent.objects.filter(topic__in=consumers).order_by("created_at", "id"):
        consumers[event.topic].apply(
            kwargs={"message": EventEnvelope.from_model(event).to_message()}
        )


def _publish(org: Organization, owner: User, contract: Contract, content: str) -> None:
    with tenant_context(org.id):
        principal = factories.principal_for_owner(org, owner)
        draft = contracts.get_draft(principal, contract.id)
        contracts.save_draft(
            principal, contract.id, content=content, expected_version=draft.version
        )
        contracts.publish_revision(principal, contract.id, expected_draft_version=draft.version + 1)


def _scenario() -> tuple[Organization, User, dict[str, Contract]]:
    org, _ = factories.organization()
    legal = factories.department(org, "Jurídico")
    manager = factories.member(org, [Role.CONTRACT_MANAGER, Role.FINANCE])
    created = {
        "internal": factories.contract(
            org, manager, content="Cláusula de manutenção preventiva dos geradores."
        ),
        "restricted": factories.contract(
            org,
            manager,
            confidentiality="restricted",
            department=legal,
            content="Manutenção restrita do departamento jurídico.",
        ),
        "confidential": factories.contract(
            org,
            manager,
            confidentiality="confidential",
            content="Manutenção sigilosa de aquisição estratégica.",
        ),
    }
    for key, contract in created.items():
        _publish(org, manager, contract, f"Revisão publicada: manutenção {key} dos equipamentos.")
    _drain_index_events()
    return org, manager, created


def test_search_results_counts_and_facets_respect_visibility() -> None:
    org, manager, created = _scenario()
    reviewer = factories.member(org, [Role.REVIEWER])
    manager_view = factories.client_for(manager, org).get("/api/v1/search?q=manutenção").json()
    reviewer_view = factories.client_for(reviewer, org).get("/api/v1/search?q=manutenção").json()
    assert manager_view["total"] == 3
    assert reviewer_view["total"] == 1
    assert [r["id"] for r in reviewer_view["results"]] == [str(created["internal"].id)]
    assert sum(reviewer_view["facets"]["drafting_status"].values()) == 1
    assert "«" in reviewer_view["results"][0]["snippet"]
    admin = factories.member(org, [Role.ADMIN])
    assert factories.client_for(admin, org).get("/api/v1/search?q=manutenção").json()["total"] == 0


def test_suggestions_and_export_do_not_leak_confidential_contracts() -> None:
    org, manager, created = _scenario()
    reviewer = factories.member(org, [Role.AUDITOR])
    reference = created["confidential"].reference
    assert (
        factories.client_for(reviewer, org).get(f"/api/v1/search/suggest?q={reference}").json()
        == []
    )
    assert (
        factories.client_for(manager, org)
        .get(f"/api/v1/search/suggest?q={reference}")
        .json()[0]["reference"]
        == reference
    )
    export = factories.client_for(reviewer, org).get("/api/v1/reports/contracts.csv")
    assert export.status_code == 200
    rows = list(csv.reader(io.StringIO(export.content.decode()), delimiter=";"))
    assert reference not in {row[0] for row in rows[1:]}
    assert len(rows) == 1 + 2  # interno e restrito (auditor tem visão ampla de restritos)
    dashboard = factories.client_for(reviewer, org).get("/api/v1/reports/dashboard").json()
    assert dashboard["contracts_total"] == 2
    assert (
        factories.client_for(factories.member(org, [Role.REQUESTER]), org)
        .get("/api/v1/reports/contracts.csv")
        .status_code
        == 403
    )


def test_search_is_isolated_between_organizations() -> None:
    _scenario()
    other_org, _ = factories.organization()
    manager = factories.member(other_org, [Role.CONTRACT_MANAGER])
    assert (
        factories.client_for(manager, other_org).get("/api/v1/search?q=manutenção").json()["total"]
        == 0
    )


def test_delayed_index_job_does_not_overwrite_newer_revision() -> None:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, manager, content="Texto inicial com palavra antiquada.")
    with tenant_context(org.id):
        contracts.publish_revision(
            factories.principal_for_owner(org, manager), contract.id, expected_draft_version=1
        )
    _publish(org, manager, contract, "Texto novo com palavra moderna.")
    with tenant_context(org.id):
        assert index_contract(contract.id, revision_number=2) is True
        # O job da revisão 1 chega atrasado: não pode substituir o índice da revisão 2.
        assert index_contract(contract.id, revision_number=1) is False
        document = SearchDocument.objects.get(contract=contract)
    assert document.revision_number == 2
    client = factories.client_for(manager, org)
    assert client.get("/api/v1/search?q=moderna").json()["total"] == 1
    assert client.get("/api/v1/search?q=antiquada").json()["total"] == 0


def test_only_released_attachment_text_is_indexed() -> None:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, manager)
    with tenant_context(org.id):
        principal = factories.principal_for_owner(org, manager)
        released = files.receive(
            principal,
            name="anexo.pdf",
            content=file_samples.pdf("x"),
            purpose=FilePurpose.CONTRACT_ATTACHMENT,
        )
        StoredFile.objects.filter(id=released.id).update(
            status=FileStatus.RELEASED,
            released_at=timezone.now(),
            extracted_text="cronograma xilofônico liberado",
        )
        files.attach(principal, contract.id, released.id, include_in_submission=False)
        rejected = files.receive(
            principal,
            name="b.pdf",
            content=file_samples.pdf("y"),
            purpose=FilePurpose.CONTRACT_ATTACHMENT,
        )
        # Mesmo que um vínculo exista, arquivo não liberado não entra no índice.
        ContractAttachment.objects.create(
            organization_id=org.id, contract=contract, file=rejected, added_by=manager
        )
        StoredFile.objects.filter(id=rejected.id).update(status=FileStatus.REJECTED)
    _drain_index_events()
    client = factories.client_for(manager, org)
    assert client.get("/api/v1/search?q=xilofônico").json()["total"] == 1
    with tenant_context(org.id):
        body = SearchDocument.objects.get(contract=contract).body
    assert "xilofônico" in body


def test_filters_pagination_and_validation() -> None:
    org, manager, created = _scenario()
    client = factories.client_for(manager, org)
    by_type = client.get("/api/v1/search?contract_type=services&size=2&page=1").json()
    assert by_type["total"] == 3
    assert len(by_type["results"]) == 2
    second = client.get("/api/v1/search?contract_type=services&size=2&page=2").json()
    assert len(second["results"]) == 1
    assert {r["id"] for r in by_type["results"]} | {r["id"] for r in second["results"]} == {
        str(c.id) for c in created.values()
    }
    assert client.get(f"/api/v1/search?q={created['internal'].reference}").json()["total"] == 1
    assert client.get("/api/v1/search?size=500").status_code == 400
    assert client.get("/api/v1/search?campo=x").status_code == 400
    assert client.get("/api/v1/search/suggest?q=a").json() == []
    assert client.get(f"/api/v1/search?owner_id={uuid.uuid4()}").json()["total"] == 0
    assert (
        client.get("/api/v1/search?ends_after=2000-01-01&ends_before=2100-01-01").status_code == 200
    )


def test_export_neutralizes_spreadsheet_formulas() -> None:
    org, _ = factories.organization()
    manager = factories.member(org, [Role.CONTRACT_MANAGER])
    contract = factories.contract(org, manager)
    with tenant_context(org.id):
        Contract.objects.filter(id=contract.id).update(title='=HYPERLINK("http://exemplo.invalid")')
    content = (
        factories.client_for(manager, org).get("/api/v1/reports/contracts.csv").content.decode()
    )
    assert "'=HYPERLINK" in content
