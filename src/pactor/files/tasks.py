"""Consumidor do pipeline de arquivos (fila ``extraction``)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pactor.core.outbox import EventEnvelope
from pactor.core.tasks import SideEffect, side_effect_consumer
from pactor.files import services


@dataclass(frozen=True, slots=True)
class FileRef:
    organization_id: uuid.UUID
    file_id: uuid.UUID


def _ref(envelope: EventEnvelope) -> FileRef:
    if envelope.organization_id is None:
        raise ValueError("evento de arquivo sem organização")
    return FileRef(envelope.organization_id, uuid.UUID(str(envelope.payload["file_id"])))


def _process(ref: FileRef) -> None:
    services.process(ref.organization_id, ref.file_id)


process_file = side_effect_consumer(
    "pactor.files.process_file", SideEffect(prepare=_ref, perform=_process)
)
