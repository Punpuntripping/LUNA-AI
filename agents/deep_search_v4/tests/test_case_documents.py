"""Unit tests for ``shared/case_documents.py`` (case_documents_forwarding.md §3.1).

Covers the forwarding filter (which attachments travel), the WI alias parser,
and the ``case_document`` block body — every variant of §3.1 plus the two caps.
"""
from __future__ import annotations

import pytest

from agents.deep_search_v4.shared.case_documents import (
    CASE_DOCUMENT_LABEL,
    CASE_DOCUMENT_TYPES,
    NO_SUMMARY_EXCERPT_CHARS,
    PER_DOC_FULL_TEXT_CAP,
    TOTAL_FULL_TEXT_CAP,
    build_case_document_blocks,
    forwardable_documents,
    parse_wi_aliases,
)
from agents.models import WorkspaceItemSnapshot

FORWARDED_TYPES = (
    "judgment", "hearing_minutes", "claim", "memo", "statement",
    "expert_report", "financial", "contract", "letter",
)
NOT_FORWARDED_TYPES = ("chat", "official_record", "user_notes", "reference", "other")


def _doc(
    seq: int | None = 3,
    *,
    kind: str = "attachment",
    doc_type: str | None = "judgment",
    title: str = "حكم ابتدائي في نزاع عمّالي",
    summary: str = "ملخص الحكم",
    content: str = "نص الحكم",
    item_id: str | None = None,
) -> WorkspaceItemSnapshot:
    return WorkspaceItemSnapshot(
        item_id=item_id or f"id-{seq}",
        kind=kind,
        title=title,
        content_md=content,
        summary=summary,
        metadata={"doc_type": doc_type} if doc_type else {},
        wi_seq=seq,
    )


def _body(blocks, item_id):
    return next(b.body for b in blocks if b.source_item_id == item_id)


# ===========================================================================
# forwardable_documents — which attachments travel (D3)
# ===========================================================================


def test_the_nine_case_document_types_are_exactly_the_planned_set():
    assert CASE_DOCUMENT_TYPES == frozenset(FORWARDED_TYPES)


@pytest.mark.parametrize("doc_type", FORWARDED_TYPES)
def test_case_document_types_are_forwarded(doc_type):
    assert forwardable_documents([_doc(doc_type=doc_type)]) == [_doc(doc_type=doc_type)]


def test_attachment_without_doc_type_is_forwarded():
    """Pre-doc_type uploads, summarizer failures, summary not ready yet."""
    item = _doc(doc_type=None)
    assert forwardable_documents([item]) == [item]


def test_blank_doc_type_counts_as_unset():
    item = _doc(doc_type=None)
    item.metadata["doc_type"] = "  "
    assert forwardable_documents([item]) == [item]


@pytest.mark.parametrize("doc_type", NOT_FORWARDED_TYPES)
def test_non_case_types_stay_planner_only(doc_type):
    assert forwardable_documents([_doc(doc_type=doc_type)]) == []


@pytest.mark.parametrize("kind", ["agent_search", "agent_writing", "note", "statute_package"])
def test_non_attachment_kinds_never_travel(kind):
    # Even a judgment-labelled non-attachment (a defensive case) stays home.
    assert forwardable_documents([_doc(kind=kind)]) == []
    assert forwardable_documents([_doc(kind=kind, doc_type=None)]) == []


def test_attachment_without_wi_seq_is_dropped():
    """No alias → the planner cannot name it, and the heading would be WI-None."""
    assert forwardable_documents([_doc(seq=None, item_id="x")]) == []


def test_router_order_is_preserved_and_repeats_dropped():
    a, b, c = _doc(5), _doc(2, doc_type="contract"), _doc(9, doc_type=None)
    chat = _doc(4, doc_type="chat")
    out = forwardable_documents([a, chat, b, a, c])
    assert [d.wi_seq for d in out] == [5, 2, 9]


def test_empty_and_none_inputs():
    assert forwardable_documents([]) == []
    assert forwardable_documents(None) == []


def test_case_document_types_are_a_subset_of_the_summarizer_labels():
    """The two lists cannot drift: a renamed label there must fail here."""
    from agents.memory.artifact_summarizer.models import DOC_TYPES

    assert CASE_DOCUMENT_TYPES <= set(DOC_TYPES)
    # And the plan's not-forwarded set is the rest of the vocabulary.
    assert set(DOC_TYPES) - CASE_DOCUMENT_TYPES == set(NOT_FORWARDED_TYPES)


# ===========================================================================
# parse_wi_aliases — planner aliases → item_ids of forwarded docs only
# ===========================================================================


def test_alias_resolves_to_item_id():
    docs = [_doc(3), _doc(7, doc_type="contract")]
    assert parse_wi_aliases(["WI-7", "WI-3"], docs) == ["id-7", "id-3"]


def test_unknown_and_foreign_aliases_are_ignored():
    """An alias of a non-forwarded item (a chat), of no item, or garbage → dropped."""
    chat = _doc(4, doc_type="chat")
    docs = forwardable_documents([_doc(3), chat])
    assert parse_wi_aliases(["WI-4", "WI-99", "garbage", "", "3", "WI-3"], docs) == ["id-3"]


def test_alias_parsing_is_lenient_on_case_and_separator_and_dedupes():
    docs = [_doc(3)]
    assert parse_wi_aliases(["wi-3", " WI 3 ", "WI_3", "WI-3"], docs) == ["id-3"]


def test_no_aliases():
    assert parse_wi_aliases([], [_doc(3)]) == []
    assert parse_wi_aliases(None, [_doc(3)]) == []


# ===========================================================================
# build_case_document_blocks — the block contract + body format
# ===========================================================================


def test_one_turn_scoped_block_per_document():
    docs = [_doc(3), _doc(7, doc_type="contract")]
    blocks = build_case_document_blocks(docs, [])
    assert [b.label for b in blocks] == [CASE_DOCUMENT_LABEL] * 2 == ["case_document"] * 2
    assert [b.persistence for b in blocks] == ["turn", "turn"]
    assert [b.source_item_id for b in blocks] == ["id-3", "id-7"]


def test_body_format_with_cut_full_text_is_exact():
    """The §3.1 sample, byte for byte."""
    content = "ن" * 48210
    doc = _doc(3, content=content, summary="ملخص")
    [block] = build_case_document_blocks([doc], ["id-3"])
    assert block.body == (
        "### WI-3 · judgment · حكم ابتدائي في نزاع عمّالي\n"
        "#### الملخص\n"
        "ملخص\n"
        "#### النص الكامل\n"
        + "ن" * 12000 + "\n"
        "[انتهى المعروض: 12000 حرف من أصل 48210 — بقية الوثيقة لم تُعرض]"
    )


def test_full_text_under_the_cap_has_no_marker():
    doc = _doc(3, content="نص قصير")
    [block] = build_case_document_blocks([doc], ["id-3"])
    assert block.body.endswith("#### النص الكامل\nنص قصير")
    assert "انتهى المعروض" not in block.body


def test_summary_only_recipient_gets_no_full_text_section():
    doc = _doc(3, content="نص الحكم الكامل")
    [block] = build_case_document_blocks([doc], [])
    assert block.body == (
        "### WI-3 · judgment · حكم ابتدائي في نزاع عمّالي\n#### الملخص\nملخص الحكم"
    )
    assert "#### النص الكامل" not in block.body
    assert "نص الحكم الكامل" not in block.body


def test_no_doc_type_drops_the_middle_segment():
    [block] = build_case_document_blocks([_doc(3, doc_type=None)], [])
    assert block.body.splitlines()[0] == "### WI-3 · حكم ابتدائي في نزاع عمّالي"


def test_title_newlines_are_flattened_into_the_heading():
    [block] = build_case_document_blocks([_doc(3, title="صحيفة\nدعوى")], [])
    assert block.body.splitlines()[0] == "### WI-3 · judgment · صحيفة دعوى"


def test_no_summary_and_no_full_text_shows_the_opening():
    content = "أ" * 2000 + "ب" * 500
    doc = _doc(3, summary="", content=content)
    [block] = build_case_document_blocks([doc], [])
    assert block.body == (
        "### WI-3 · judgment · حكم ابتدائي في نزاع عمّالي\n"
        "#### الملخص\n"
        "(لم يُكتب ملخص بعد — هذا مطلع النص)\n"
        + "أ" * NO_SUMMARY_EXCERPT_CHARS
    )


def test_no_summary_but_full_text_omits_the_summary_section():
    doc = _doc(3, summary="", content="نص الحكم")
    [block] = build_case_document_blocks([doc], ["id-3"])
    assert block.body == (
        "### WI-3 · judgment · حكم ابتدائي في نزاع عمّالي\n"
        "#### النص الكامل\n"
        "نص الحكم"
    )
    assert "#### الملخص" not in block.body


def test_document_with_nothing_but_a_title_is_just_the_heading():
    [block] = build_case_document_blocks([_doc(3, summary="", content="")], ["id-3"])
    assert block.body == "### WI-3 · judgment · حكم ابتدائي في نزاع عمّالي"


# ===========================================================================
# Ordering + the total cap
# ===========================================================================


def test_planner_named_documents_come_first_in_their_order():
    docs = [_doc(1), _doc(2), _doc(3), _doc(4)]
    blocks = build_case_document_blocks(docs, ["id-3", "id-1"])
    assert [b.source_item_id for b in blocks] == ["id-3", "id-1", "id-2", "id-4"]


def test_full_text_ids_not_in_docs_are_ignored():
    blocks = build_case_document_blocks([_doc(1)], ["id-404", "id-1"])
    assert [b.source_item_id for b in blocks] == ["id-1"]
    assert "#### النص الكامل" in blocks[0].body


def test_total_cap_drops_the_overflowing_document_to_summary_plus_note():
    big = "ن" * 20_000  # cut to 12,000 each → 12k + 12k + (12k > 30k) → third drops
    docs = [_doc(1, content=big), _doc(2, content=big), _doc(3, content=big)]
    stats: dict = {}
    blocks = build_case_document_blocks(docs, ["id-1", "id-2", "id-3"], stats=stats)

    third = _body(blocks, "id-3")
    assert third.endswith(
        "#### الملخص\nملخص الحكم\n"
        "#### النص الكامل\n"
        "(لم يُرفق النص الكامل لتجاوز حد السياق — اعتمد على الملخص)"
    )
    assert "ن" * 100 not in third
    assert stats["full_text"] == ["WI-1", "WI-2"]
    assert stats["over_total"] == ["WI-3"]
    assert stats["truncated"] == ["WI-1", "WI-2"]
    assert stats["full_text_chars"] == 2 * PER_DOC_FULL_TEXT_CAP <= TOTAL_FULL_TEXT_CAP


def test_a_later_shorter_document_still_fits_under_the_total_cap():
    big, small = "ن" * 20_000, "نص قصير"
    docs = [_doc(1, content=big), _doc(2, content=big), _doc(3, content=big),
            _doc(4, content=small)]
    stats: dict = {}
    blocks = build_case_document_blocks(docs, ["id-1", "id-2", "id-3", "id-4"], stats=stats)
    assert _body(blocks, "id-4").endswith("#### النص الكامل\nنص قصير")
    assert stats["full_text"] == ["WI-1", "WI-2", "WI-4"]
    assert stats["over_total"] == ["WI-3"]


def test_over_total_without_a_summary_falls_back_to_the_opening():
    big = "ن" * 20_000
    docs = [_doc(1, content=big), _doc(2, content=big), _doc(3, content=big, summary="")]
    blocks = build_case_document_blocks(docs, ["id-1", "id-2", "id-3"])
    third = _body(blocks, "id-3")
    assert "(لم يُكتب ملخص بعد — هذا مطلع النص)" in third
    assert "(لم يُرفق النص الكامل لتجاوز حد السياق — اعتمد على الملخص)" in third


def test_custom_caps_are_honoured():
    doc = _doc(1, content="ن" * 50)
    [block] = build_case_document_blocks([doc], ["id-1"], per_doc_cap=10, total_cap=100)
    assert "[انتهى المعروض: 10 حرف من أصل 50 — بقية الوثيقة لم تُعرض]" in block.body


def test_no_documents_no_blocks():
    assert build_case_document_blocks([], ["id-1"]) == []
