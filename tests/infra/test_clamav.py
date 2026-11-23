"""Pipeline com o ClamAV real (container clamav): EICAR bloqueado, PDF limpo liberado."""

from __future__ import annotations

import pytest
from django.conf import settings

from pactor.access.policies import Role
from pactor.core.tenancy import tenant_context
from pactor.files import services
from pactor.files.models import FilePurpose, FileStatus, StoredFile
from pactor.files.scanner import ClamdScanner, ScanVerdict
from tests import factories, file_samples

pytestmark = pytest.mark.django_db(transaction=True)


def test_real_clamav_detects_eicar_and_accepts_clean_content() -> None:
    scanner = ClamdScanner(settings.CLAMAV_HOST, settings.CLAMAV_PORT)
    assert scanner.version().startswith("ClamAV")
    assert scanner.scan(file_samples.EICAR).verdict is ScanVerdict.INFECTED
    assert scanner.scan(file_samples.pdf()).verdict is ScanVerdict.CLEAN


def test_pipeline_with_real_scanner() -> None:
    org, _ = factories.organization()
    person = factories.member(org, [Role.CONTRACT_MANAGER])
    with tenant_context(org.id):
        principal = factories.principal(org, person)
        clean = services.receive(
            principal,
            name="limpo.pdf",
            content=file_samples.pdf(),
            purpose=FilePurpose.CONTRACT_ATTACHMENT,
        )
        # O arquivo de teste EICAR só é reconhecido quando ocupa o início do conteúdo.
        infected = services.receive(
            principal,
            name="eicar.pdf",
            content=file_samples.EICAR,
            purpose=FilePurpose.CONTRACT_ATTACHMENT,
        )
    services.process(org.id, clean.id)
    services.process(org.id, infected.id)
    with tenant_context(org.id):
        assert StoredFile.objects.get(id=clean.id).status == FileStatus.RELEASED
        blocked = StoredFile.objects.get(id=infected.id)
    assert blocked.status == FileStatus.INFECTED
    assert "Eicar" in blocked.rejection_detail
    assert blocked.scan_engine.startswith("ClamAV")
