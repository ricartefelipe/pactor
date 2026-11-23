"""Regras puras: documentos, transições de elaboração, fingerprint e diff textual."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pactor.contracts.diff import ChangeKind, apply_diff, compare_text
from pactor.contracts.domain import (
    AttachmentDigest,
    DraftingStatus,
    SubmissionSnapshot,
    allows_new_revision,
    can_transition,
    canonical_json,
    sha256_text,
)
from pactor.counterparties.domain import (
    InvalidDocumentError,
    PartyKind,
    cnpj_check_digits,
    cpf_check_digits,
    normalize_document,
)


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        (PartyKind.INDIVIDUAL, "529.982.247-25", "52998224725"),
        (PartyKind.COMPANY, "11.222.333/0001-81", "11222333000181"),
    ],
)
def test_valid_documents_are_normalized(kind: PartyKind, raw: str, expected: str) -> None:
    assert normalize_document(kind, raw) == expected


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        (PartyKind.INDIVIDUAL, "529.982.247-24"),
        (PartyKind.INDIVIDUAL, "111.111.111-11"),
        (PartyKind.INDIVIDUAL, "123"),
        (PartyKind.COMPANY, "11.222.333/0001-80"),
        (PartyKind.COMPANY, "52998224725"),
    ],
)
def test_invalid_documents_are_rejected(kind: PartyKind, raw: str) -> None:
    with pytest.raises(InvalidDocumentError):
        normalize_document(kind, raw)


digits = st.text(alphabet="0123456789", min_size=9, max_size=9)


@given(digits)
def test_property_generated_cpf_is_valid_and_single_digit_change_is_not(base: str) -> None:
    if len(set(base)) == 1:
        return
    document = base + cpf_check_digits(base)
    assert normalize_document(PartyKind.INDIVIDUAL, document) == document
    flipped = document[:-1] + str((int(document[-1]) + 1) % 10)
    with pytest.raises(InvalidDocumentError):
        normalize_document(PartyKind.INDIVIDUAL, flipped)


@given(st.text(alphabet="0123456789", min_size=12, max_size=12))
def test_property_generated_cnpj_is_valid(base: str) -> None:
    if len(set(base)) == 1:
        return
    document = base + cnpj_check_digits(base)
    assert normalize_document(PartyKind.COMPANY, document) == document


def test_drafting_transitions() -> None:
    assert can_transition(DraftingStatus.DRAFT, DraftingStatus.IN_APPROVAL)
    assert can_transition(DraftingStatus.APPROVED, DraftingStatus.SIGNING)
    assert can_transition(DraftingStatus.SIGNING, DraftingStatus.APPROVED)
    assert not can_transition(DraftingStatus.DRAFT, DraftingStatus.SIGNING)
    assert not can_transition(DraftingStatus.IN_APPROVAL, DraftingStatus.SIGNING)
    assert not can_transition(DraftingStatus.SIGNED, DraftingStatus.DRAFT)
    assert not can_transition(DraftingStatus.CANCELLED, DraftingStatus.DRAFT)


@given(st.sampled_from(list(DraftingStatus)), st.sampled_from(list(DraftingStatus)))
def test_property_terminal_states_have_no_exit(
    current: DraftingStatus, target: DraftingStatus
) -> None:
    if current in {DraftingStatus.SIGNED, DraftingStatus.CANCELLED}:
        assert not can_transition(current, target)
    if can_transition(current, target) and target is DraftingStatus.SIGNING:
        assert current is DraftingStatus.APPROVED


def test_new_revisions_only_before_signature() -> None:
    assert allows_new_revision(DraftingStatus.APPROVED)
    assert not allows_new_revision(DraftingStatus.SIGNING)
    assert not allows_new_revision(DraftingStatus.SIGNED)


def _snapshot(
    content: str = "texto", value: str = "100.00", attachments: tuple[AttachmentDigest, ...] = ()
) -> SubmissionSnapshot:
    return SubmissionSnapshot(
        sha256_text(content), {"total_value": value, "title": "T"}, attachments
    )


def test_fingerprint_covers_content_metadata_and_attachments() -> None:
    base = _snapshot().fingerprint()
    assert _snapshot(content="texto alterado").fingerprint() != base
    assert _snapshot(value="100.01").fingerprint() != base
    a = AttachmentDigest("f1", "anexo.pdf", "a" * 64)
    b = AttachmentDigest("f2", "outro.pdf", "b" * 64)
    with_attachments = _snapshot(attachments=(a, b)).fingerprint()
    assert with_attachments != base
    assert _snapshot(attachments=(b, a)).fingerprint() == with_attachments
    changed_bytes = AttachmentDigest("f1", "anexo.pdf", "c" * 64)
    assert _snapshot(attachments=(changed_bytes, b)).fingerprint() != with_attachments


lines = st.lists(st.text(alphabet="abcxyz ,.;:çãéÁ0123", max_size=8), max_size=12)


@given(lines, lines)
def test_property_diff_reconstructs_new_text(old: list[str], new: list[str]) -> None:
    old_text, new_text = "\n".join(old), "\n".join(new)
    diff = compare_text(old_text, new_text)
    assert apply_diff(old_text, diff) == "\n".join(new_text.splitlines())
    assert diff.unchanged == (old_text.splitlines() == new_text.splitlines())


def test_diff_counts_and_kinds() -> None:
    diff = compare_text("a\nb\nc", "a\nB\nc\nd")
    kinds = [h.kind for h in diff.hunks]
    assert kinds == [ChangeKind.EQUAL, ChangeKind.REPLACE, ChangeKind.EQUAL, ChangeKind.INSERT]
    assert (diff.inserted, diff.deleted) == (2, 1)
    assert compare_text("a\nb", "a").hunks[-1].kind is ChangeKind.DELETE


def test_fingerprint_format_is_stable_across_versions() -> None:
    """Fingerprints gravados precisam ser reproduzíveis: o formato canônico é fixo.

    Mudar ordenação de chaves, separadores ou codificação alteraria hashes já
    registrados em revisões, rodadas e votos; este valor de referência impede isso.
    """
    snapshot = SubmissionSnapshot(
        content_sha256=sha256_text("Cláusula única."),
        metadata={"title": "Contrato", "total_value": "1500.00", "parent_id": None},
        attachments=(
            AttachmentDigest("arq-2", "anexo B.pdf", "b" * 64),
            AttachmentDigest("arq-1", "anexo A.pdf", "a" * 64),
        ),
    )
    assert (
        sha256_text("Cláusula única.")
        == "e6c0b4c2d0803fa94b2e623674dd7023e284feb45efbcfac3aa3c5a6a633331e"
    )
    assert (
        snapshot.fingerprint() == "0af71be965612293cff1a904a4a32939d949f26e42cb96d78ac2b030b12ff72b"
    )


def test_canonical_json_is_compact_sorted_and_unescaped() -> None:
    assert canonical_json({"b": 1, "a": ["é", None]}) == '{"a":["é",null],"b":1}'


@pytest.mark.parametrize(
    ("kind", "document"),
    [
        # Referências conhecidas, independentes da implementação, cobrindo
        # dígitos verificadores 0 (restos 0 e 1 do módulo 11).
        (PartyKind.INDIVIDUAL, "12345678909"),
        (PartyKind.INDIVIDUAL, "11144477735"),
        (PartyKind.INDIVIDUAL, "39053344705"),
        (PartyKind.COMPANY, "11444777000161"),
        (PartyKind.COMPANY, "11222333000181"),
        (PartyKind.COMPANY, "04252011000110"),
    ],
)
def test_known_valid_documents(kind: PartyKind, document: str) -> None:
    assert normalize_document(kind, document) == document


def test_document_error_messages_are_specific() -> None:
    with pytest.raises(InvalidDocumentError, match="11 dígitos"):
        normalize_document(PartyKind.INDIVIDUAL, "123")
    with pytest.raises(InvalidDocumentError, match="repetidos"):
        normalize_document(PartyKind.INDIVIDUAL, "00000000000")
    with pytest.raises(InvalidDocumentError, match="verificadores"):
        normalize_document(PartyKind.INDIVIDUAL, "12345678900")
