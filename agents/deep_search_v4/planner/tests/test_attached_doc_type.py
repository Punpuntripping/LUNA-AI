"""``<attached_items>`` — an uploaded document carries its doc_type, and the
items that travel downstream as case documents carry ``forwarded="true"``
(case_documents_forwarding.md §3.3 / §5.1)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agents.deep_search_v4.planner.models import PlannerDecision
from agents.deep_search_v4.planner.prompts import (
    _CASE_DOCUMENTS_RULES,
    _PLANNER_BRIEF_DETAIL_RULES,
    PLANNER_DECIDER_SYSTEM_PROMPT,
    _render_attached_items,
    build_decider_instructions,
)
from agents.deep_search_v4.shared.case_documents import (
    PER_DOC_FULL_TEXT_CAP,
    TOTAL_FULL_TEXT_CAP,
)
from agents.models import WorkspaceItemSnapshot


def _snap(**over) -> WorkspaceItemSnapshot:
    base = dict(item_id="i1", kind="attachment", title="صك حكم", content_md="نص الحكم", wi_seq=3)
    base.update(over)
    return WorkspaceItemSnapshot(**base)


def test_doc_type_attribute_on_an_uploaded_document():
    block = _render_attached_items([_snap(metadata={"doc_type": "judgment"})])
    assert (
        '<attached_item wi="WI-3" kind="attachment" doc_type="judgment" forwarded="true">'
        in block
    )


def test_item_without_doc_type_renders_no_doc_type_attribute():
    # A non-attachment kind: neither attribute.
    block = _render_attached_items([_snap(kind="agent_search")])
    assert '<attached_item wi="WI-3" kind="agent_search">' in block
    # An attachment with no doc_type yet still travels (D3) — no doc_type attr.
    block = _render_attached_items([_snap()])
    assert '<attached_item wi="WI-3" kind="attachment" forwarded="true">' in block


# ===========================================================================
# forwarded="true" — only on the items forwardable_documents() keeps
# ===========================================================================


@pytest.mark.parametrize("doc_type", ["chat", "official_record", "user_notes", "reference", "other"])
def test_non_case_document_types_are_not_marked_forwarded(doc_type):
    block = _render_attached_items([_snap(metadata={"doc_type": doc_type})])
    assert f'<attached_item wi="WI-3" kind="attachment" doc_type="{doc_type}">' in block
    assert "forwarded" not in block


def test_forwarded_marks_exactly_the_forwardable_items():
    items = [
        _snap(item_id="a", wi_seq=1, metadata={"doc_type": "contract"}),
        _snap(item_id="b", wi_seq=2, metadata={"doc_type": "chat"}),
        _snap(item_id="c", wi_seq=3, kind="agent_writing"),
        _snap(item_id="d", wi_seq=4),  # no doc_type yet
    ]
    block = _render_attached_items(items)
    assert '<attached_item wi="WI-1" kind="attachment" doc_type="contract" forwarded="true">' in block
    assert '<attached_item wi="WI-2" kind="attachment" doc_type="chat">' in block
    assert '<attached_item wi="WI-3" kind="agent_writing">' in block
    assert '<attached_item wi="WI-4" kind="attachment" forwarded="true">' in block
    assert block.count('forwarded="true"') == 2


# ===========================================================================
# _CASE_DOCUMENTS_RULES — injected only when a forwardable document exists
# ===========================================================================


def _deps(attached=(), prior_searches=()):
    return SimpleNamespace(
        case_brief=None,
        compaction_summary_md=None,
        recent_messages=[],
        prior_searches=list(prior_searches),
        attached_items=list(attached),
    )


def test_case_documents_rules_injected_with_a_forwardable_document():
    rendered = build_decider_instructions(_deps([_snap(metadata={"doc_type": "judgment"})]))
    assert _CASE_DOCUMENTS_RULES in rendered
    # After the brief rules, which an attachment also triggers.
    assert rendered.index(_PLANNER_BRIEF_DETAIL_RULES) < rendered.index(_CASE_DOCUMENTS_RULES)


def test_case_documents_rules_absent_when_no_attachment_travels():
    rendered = build_decider_instructions(
        _deps([_snap(metadata={"doc_type": "chat"}), _snap(item_id="n", kind="note", wi_seq=5)])
    )
    # The brief rules still apply (there ARE attachments) — the full-text rules don't.
    assert _PLANNER_BRIEF_DETAIL_RULES in rendered
    assert _CASE_DOCUMENTS_RULES not in rendered


def test_case_documents_rules_absent_on_a_bare_turn():
    rendered = build_decider_instructions(_deps())
    assert _CASE_DOCUMENTS_RULES not in rendered
    assert "case_full_text_wis" not in rendered


def test_case_documents_rules_quote_the_real_caps():
    assert f"{PER_DOC_FULL_TEXT_CAP:,} characters" in _CASE_DOCUMENTS_RULES
    assert f"{TOTAL_FULL_TEXT_CAP:,} characters" in _CASE_DOCUMENTS_RULES


def test_static_prompt_describes_the_case_document_channel():
    assert "`case_document`" in PLANNER_DECIDER_SYSTEM_PROMPT
    assert 'forwarded="true"' in PLANNER_DECIDER_SYSTEM_PROMPT
    assert '"case_full_text_wis"' in PLANNER_DECIDER_SYSTEM_PROMPT
    assert '"reg_full_text_wis"' in PLANNER_DECIDER_SYSTEM_PROMPT
    # The superseded "attachments reach the search only via the brief" line is gone.
    assert "Only three blocks flow" not in PLANNER_DECIDER_SYSTEM_PROMPT
    assert "their facts go via `planner_brief`" not in PLANNER_DECIDER_SYSTEM_PROMPT


# ===========================================================================
# PlannerDecision — the two new fields
# ===========================================================================


def test_planner_decision_full_text_fields_default_empty():
    d = PlannerDecision(mode="reg_compliance_led", rationale="r")
    assert d.case_full_text_wis == []
    assert d.reg_full_text_wis == []


def test_planner_decision_full_text_fields_round_trip():
    d = PlannerDecision.model_validate({
        "mode": "case_led", "rationale": "r",
        "case_full_text_wis": ["WI-3"], "reg_full_text_wis": ["WI-5", "WI-3"],
    })
    assert d.case_full_text_wis == ["WI-3"]
    assert d.reg_full_text_wis == ["WI-5", "WI-3"]


@pytest.mark.parametrize("raw, expected", [(None, []), ("", []), ("WI-3", ["WI-3"])])
def test_planner_decision_full_text_fields_are_lenient(raw, expected):
    """A ``null`` / bare string from the LLM must not cost a decider retry."""
    d = PlannerDecision.model_validate({
        "mode": "case_led", "rationale": "r",
        "case_full_text_wis": raw, "reg_full_text_wis": raw,
    })
    assert d.case_full_text_wis == expected
    assert d.reg_full_text_wis == expected


def test_schema_descriptions_match_the_prompt():
    """The JSON schema reaches the LLM — it must not contradict §5.1."""
    props = PlannerDecision.model_json_schema()["properties"]
    assert "Empty = summaries only" in props["case_full_text_wis"]["description"]
    assert "RULINGS" in props["case_full_text_wis"]["description"]
    assert "REGULATIONS" in props["reg_full_text_wis"]["description"]
    assert "NOT forwarded downstream), planner_brief is the sole channel" not in (
        props["planner_brief"]["description"]
    )
    assert "forwarded case documents" in props["planner_brief"]["description"]
    assert "forwarded case documents" in props["context_labels"]["description"]


# ===========================================================================
# agents/orchestrator.py::_load_attached_items — the snapshot carries summary
# ===========================================================================


def test_load_attached_items_selects_and_carries_summary():
    from agents.orchestrator import _load_attached_items

    supabase = MagicMock()
    chain = supabase.table.return_value.select.return_value
    chain.eq.return_value = chain
    chain.is_.return_value = chain
    chain.maybe_single.return_value = chain
    chain.execute.return_value = SimpleNamespace(data={
        "item_id": "i1", "wi_seq": 3, "kind": "attachment", "title": "صك حكم",
        "content_md": "نص", "summary": "ملخص الحكم", "metadata": {"doc_type": "judgment"},
    })

    [snap] = _load_attached_items(supabase, ["i1"], "u1", "c1")

    columns = supabase.table.return_value.select.call_args.args[0]
    assert "summary" in {c.strip() for c in columns.split(",")}
    assert snap.summary == "ملخص الحكم"
    assert snap.metadata == {"doc_type": "judgment"}


def test_load_attached_items_null_summary_is_empty_string():
    from agents.orchestrator import _load_attached_items

    supabase = MagicMock()
    chain = supabase.table.return_value.select.return_value
    chain.eq.return_value = chain
    chain.is_.return_value = chain
    chain.maybe_single.return_value = chain
    chain.execute.return_value = SimpleNamespace(data={
        "item_id": "i1", "wi_seq": 3, "kind": "attachment", "title": "t",
        "content_md": "نص", "summary": None, "metadata": None,
    })

    [snap] = _load_attached_items(supabase, ["i1"], "u1", "c1")
    assert snap.summary == ""
