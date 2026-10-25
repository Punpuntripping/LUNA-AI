"""Tests for the Core invariant: the planner LLM never sees raw content_md.

Per `.claude/plans/writer_planner.md` § Core invariant:
  - Dynamic instructions render only (WI-{seq}, kind, title, summary, word_count)
  - No content_md value (even via the WorkspaceItemSnapshot.content_md attribute,
    which exists but must NOT be surfaced in the rendered prompt)

Per `.claude/plans/agent_communication_protocol.md` the line uses the
``WI-{seq}`` alias as the primary handle so the LLM never sees a raw UUID.
The legacy ``item_id={uuid}`` rendering format is gone.

These tests guard against regressions: any future change that exposes content_md
in the planner's prompt context, or that re-introduces raw UUIDs into the
LLM I/O surface, will fail this suite.
"""
from __future__ import annotations

import pytest

from agents.models import ChatMessageSnapshot, WorkspaceItemSnapshot
from agents.writer.models import WriterStyle
from agents.writer_planner.deps import build_writer_planner_deps
from agents.writer_planner.prompts import build_writer_planner_instructions
from backend.app.services.writer_planner_context import ArtifactSummaryView


_SENTINEL_CONTENT = "SENSITIVE_RAW_CONTENT_THAT_MUST_NEVER_LEAK_DEADBEEF"
_SENTINEL_UUID = "11111111-2222-3333-4444-555555555555"


def test_attached_items_content_md_never_appears_in_instructions():
    """A turn-attached item's content_md is NOT rendered into instructions."""
    deps = build_writer_planner_deps(
        supabase=None,
        user_id="u1",
        conversation_id="c1",
        intent="اكتب عقداً",
        attached_items=[
            WorkspaceItemSnapshot(
                item_id=_SENTINEL_UUID,
                kind="attachment",
                title="نموذج عقد",
                summary="ملخّص النموذج",
                content_md=_SENTINEL_CONTENT,
                word_count=400,
                wi_seq=3,
            ),
        ],
        prior_artifacts=[],
        style=WriterStyle(),
    )
    rendered = build_writer_planner_instructions(deps)
    assert _SENTINEL_CONTENT not in rendered, (
        "CORE INVARIANT VIOLATED: content_md leaked into the planner's "
        "dynamic instructions. Inspect prompts.py _render_attached_items "
        "and remove any field that reads .content_md."
    )
    # The new alias surface — WI-{seq} replaces the legacy item_id={uuid} line.
    assert "WI-3" in rendered, (
        "Attached item should be rendered with its WI-{seq} alias (WI-3)."
    )
    assert _SENTINEL_UUID not in rendered, (
        "ALIAS PROTOCOL VIOLATED: the raw UUID leaked into the planner "
        "prompt. Inspect prompts.py — the LLM I/O surface must be alias-only."
    )
    assert "نموذج عقد" in rendered
    assert "ملخّص النموذج" in rendered
    assert "word_count=400" in rendered


def test_prior_artifacts_have_no_content_md_field_to_leak():
    """ArtifactSummaryView is summary-only by construction (defense in depth)."""
    # The view dataclass intentionally has NO content_md attribute at all.
    fields = set(ArtifactSummaryView.__dataclass_fields__.keys())
    assert "content_md" not in fields, (
        "ArtifactSummaryView gained a content_md field — this breaks the "
        "writer_planner Core invariant. Revert that change."
    )
    # The fields it SHOULD have:
    expected_minimum = {"item_id", "kind", "title", "summary", "word_count", "wi_seq"}
    assert expected_minimum.issubset(fields), (
        f"ArtifactSummaryView is missing required fields: "
        f"{expected_minimum - fields}"
    )


def test_prior_artifacts_summary_appears_but_not_content():
    """prior_artifacts are rendered with summary visible — no content_md surface to leak."""
    deps = build_writer_planner_deps(
        supabase=None,
        user_id="u1",
        conversation_id="c1",
        intent="...",
        attached_items=[],
        prior_artifacts=[
            ArtifactSummaryView(
                item_id=_SENTINEL_UUID,
                kind="agent_search",
                title="بحث سابق",
                summary="ملخّص البحث السابق المفيد",
                word_count=2000,
                created_at="2026-05-26T00:00:00Z",
                wi_seq=7,
            ),
        ],
        style=WriterStyle(),
    )
    rendered = build_writer_planner_instructions(deps)
    assert "WI-7" in rendered, (
        "Prior artifact should be rendered with its WI-{seq} alias (WI-7)."
    )
    assert _SENTINEL_UUID not in rendered, (
        "Alias protocol violated: raw UUID leaked into prior_artifacts surface."
    )
    assert "ملخّص البحث السابق المفيد" in rendered
    assert "word_count=2000" in rendered
    # And the sentinel string can't possibly leak (no content_md field exists)
    assert _SENTINEL_CONTENT not in rendered


def test_uploaded_documents_carry_their_doc_type():
    """An attachment's doc_type rides beside its kind on both surfaces."""
    deps = build_writer_planner_deps(
        supabase=None,
        user_id="u1",
        conversation_id="c1",
        intent="اكتب لائحة اعتراضية",
        attached_items=[
            WorkspaceItemSnapshot(
                item_id="a1", kind="attachment", title="صك حكم", summary="ملخّص الحكم",
                content_md="...", word_count=900, wi_seq=4, metadata={"doc_type": "judgment"},
            ),
        ],
        prior_artifacts=[
            ArtifactSummaryView(
                item_id="a2", kind="attachment", title="عقد إيجار", summary="ملخّص العقد",
                word_count=700, created_at="2026-10-25T00:00:00Z", wi_seq=2, doc_type="contract",
            ),
            ArtifactSummaryView(
                item_id="a3", kind="agent_search", title="بحث", summary="ملخّص البحث",
                word_count=300, created_at="2026-10-24T00:00:00Z", wi_seq=1,
            ),
        ],
        style=WriterStyle(),
    )
    rendered = build_writer_planner_instructions(deps)
    assert "WI-4 | kind=attachment | doc_type=judgment | word_count=900" in rendered
    assert "WI-2 | kind=attachment | doc_type=contract | word_count=700" in rendered
    assert "WI-1 | kind=agent_search | word_count=300" in rendered


def test_intent_is_truncated_at_2000_chars():
    """Intent is truncated to avoid prompt-bloat from a pasted megablob."""
    big_intent = "X" * 5000
    deps = build_writer_planner_deps(
        supabase=None,
        user_id="u1",
        conversation_id="c1",
        intent=big_intent,
        style=WriterStyle(),
    )
    rendered = build_writer_planner_instructions(deps)
    # The renderer truncates to 2000 chars; we expect at most ~2000 X's in output.
    x_count = rendered.count("X")
    assert x_count <= 2001, (
        f"Intent truncation broken — found {x_count} X's in rendered output"
    )
