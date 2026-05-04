"""Unit tests for ``backend.app.services.workspace_context``.

The helper loads visible workspace_items for a conversation and partitions
them by ``kind`` into the agent-prompt-injection shape. We stub Supabase
end-to-end -- no live DB, no LLM -- and assert:

    1. Mixed-kind row set partitions correctly into the five buckets.
    2. ``convo_context`` keeps only the most-recent row.
    3. Attachments with ``document_id`` resolve to a text excerpt via
       ``case_documents.content_text`` (truncated to 500 chars).
    4. Attachments with only ``storage_path`` come back title-only.
    5. Empty / no-row conversations return the canonical empty shape.
    6. Database errors return the empty shape (no raise).
    7. Pre-migration-026 fallback: when ``workspace_items`` is missing
       the helper falls back to the ``artifacts`` table and maps
       ``is_editable`` -> ``kind``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from backend.app.services.workspace_context import load_workspace_context


# ---------------------------------------------------------------------------
# Fake Supabase double
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    data: Any


class _FakeQuery:
    """Records .select / .eq / .is_ / .order / .limit / .maybe_single
    chains and returns a canned result on .execute().

    The fake is intentionally permissive: every chainable method is a
    no-op that returns self. The plug knob is the per-table response
    map on the parent _FakeSupabase, keyed by table name.
    """

    def __init__(
        self,
        supabase: "_FakeSupabase",
        table_name: str,
    ) -> None:
        self._supabase = supabase
        self._table_name = table_name
        self._is_maybe_single = False

    # Chainable no-ops --------------------------------------------------
    def select(self, *_: Any, **__: Any) -> "_FakeQuery": return self
    def eq(self, *_: Any, **__: Any) -> "_FakeQuery": return self
    def is_(self, *_: Any, **__: Any) -> "_FakeQuery": return self
    def order(self, *_: Any, **__: Any) -> "_FakeQuery": return self
    def limit(self, *_: Any, **__: Any) -> "_FakeQuery": return self

    def maybe_single(self) -> "_FakeQuery":
        self._is_maybe_single = True
        return self

    # Terminal ----------------------------------------------------------
    def execute(self) -> _FakeResult:
        self._supabase.calls.append(self._table_name)

        # Per-table programmable behavior:
        behavior = self._supabase.responses.get(self._table_name)
        if behavior is None:
            return _FakeResult(data=[] if not self._is_maybe_single else None)

        if isinstance(behavior, Exception):
            raise behavior

        if callable(behavior):
            data = behavior(self._is_maybe_single)
        else:
            data = behavior

        return _FakeResult(data=data)


class _FakeSupabase:
    """Programmable Supabase stand-in.

    `responses[table]` may be:
        * a list  -> returned as result.data for non-maybe_single calls
        * a dict  -> returned as result.data for maybe_single calls
        * None    -> empty (default)
        * Exception -> raised on execute()
        * callable(is_maybe_single: bool) -> data
    """

    def __init__(self, responses: Optional[dict[str, Any]] = None) -> None:
        self.responses: dict[str, Any] = responses or {}
        self.calls: list[str] = []

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self, name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partitions_mixed_kinds_correctly():
    """Mixed-kind rows partition into the five buckets."""
    rows = [
        {  # newest -- convo_context
            "item_id": "ctx-new",
            "kind": "convo_context",
            "title": "السياق",
            "content_md": "ملخّص الحوار حتى الآن",
            "metadata": {},
            "created_at": "2026-05-01T03:00:00Z",
        },
        {
            "item_id": "ctx-old",
            "kind": "convo_context",
            "title": "السياق القديم",
            "content_md": "ملخّص قديم",
            "metadata": {},
            "created_at": "2026-05-01T01:00:00Z",
        },
        {
            "item_id": "search-1",
            "kind": "agent_search",
            "title": "بحث عن: نظام العمل",
            "content_md": "## النتائج\n...",
            "metadata": {"subtype": "legal_synthesis"},
            "created_at": "2026-05-01T02:30:00Z",
        },
        {
            "item_id": "writing-1",
            "kind": "agent_writing",
            "title": "مذكّرة دفاع",
            "content_md": "# المذكّرة\n...",
            "metadata": {"subtype": "memo"},
            "created_at": "2026-05-01T02:00:00Z",
        },
        {
            "item_id": "note-1",
            "kind": "note",
            "title": "ملاحظة المحامي",
            "content_md": "تذكير: تحقّق من المادة 5",
            "metadata": {},
            "created_at": "2026-05-01T01:30:00Z",
        },
        {
            "item_id": "ref-1",
            "kind": "references",
            "title": "المراجع",
            "content_md": "روابط",
            "metadata": {},
            "created_at": "2026-05-01T01:00:00Z",
        },
        {
            "item_id": "att-store",
            "kind": "attachment",
            "title": "شهادة.pdf",
            "content_md": None,
            "storage_path": "workspace/abc.pdf",
            "document_id": None,
            "metadata": {"mime_type": "application/pdf"},
            "created_at": "2026-05-01T00:30:00Z",
        },
    ]
    supabase = _FakeSupabase(responses={"workspace_items": rows})

    ctx = await load_workspace_context(supabase, "conv-1")

    # Sanity: shape contract.
    assert set(ctx.keys()) == {
        "attachments", "notes", "agent_outputs", "convo_context", "references",
    }

    # convo_context keeps only the most-recent row (rows arrive newest-first).
    assert ctx["convo_context"] == {
        "item_id": "ctx-new",
        "content_md": "ملخّص الحوار حتى الآن",
    }

    # agent_outputs holds both search + writing rows with subtype.
    out_ids = [a["item_id"] for a in ctx["agent_outputs"]]
    assert out_ids == ["search-1", "writing-1"]
    by_id = {a["item_id"]: a for a in ctx["agent_outputs"]}
    assert by_id["search-1"]["subtype"] == "legal_synthesis"
    assert by_id["writing-1"]["subtype"] == "memo"

    # notes / references are 1-row each.
    assert [n["item_id"] for n in ctx["notes"]] == ["note-1"]
    assert ctx["notes"][0]["content_md"] == "تذكير: تحقّق من المادة 5"
    assert [r["item_id"] for r in ctx["references"]] == ["ref-1"]

    # storage-only attachment: title only, no excerpt.
    assert len(ctx["attachments"]) == 1
    att = ctx["attachments"][0]
    assert att["item_id"] == "att-store"
    assert att["title"] == "شهادة.pdf"
    assert att["kind"] == "attachment"
    assert att["text_excerpt"] is None


@pytest.mark.asyncio
async def test_attachment_with_document_id_pulls_text_excerpt():
    """document_id-backed attachment fetches case_documents.content_text
    and truncates to 500 chars."""
    long_text = "نص طويل جداً " * 300  # >> 500 chars

    rows = [{
        "item_id": "att-doc",
        "kind": "attachment",
        "title": "عقد.pdf",
        "content_md": None,
        "storage_path": None,
        "document_id": "doc-42",
        "metadata": {},
        "created_at": "2026-05-01T01:00:00Z",
    }]

    def doc_response(is_maybe_single: bool):
        # case_documents lookup uses .maybe_single().
        assert is_maybe_single is True
        return {"content_text": long_text}

    supabase = _FakeSupabase(responses={
        "workspace_items": rows,
        "case_documents": doc_response,
    })

    ctx = await load_workspace_context(supabase, "conv-1")

    assert len(ctx["attachments"]) == 1
    att = ctx["attachments"][0]
    assert att["item_id"] == "att-doc"
    assert att["text_excerpt"] is not None
    assert len(att["text_excerpt"]) == 500
    # Excerpt is the prefix of the full text.
    assert long_text.startswith(att["text_excerpt"])

    # Both tables were touched.
    assert "workspace_items" in supabase.calls
    assert "case_documents" in supabase.calls


@pytest.mark.asyncio
async def test_attachment_document_lookup_failure_degrades_to_title_only():
    """If the case_documents lookup raises, the attachment still appears
    (title only) -- the writer turn must NOT fail."""
    rows = [{
        "item_id": "att-doc",
        "kind": "attachment",
        "title": "عقد.pdf",
        "content_md": None,
        "storage_path": None,
        "document_id": "doc-broken",
        "metadata": {},
        "created_at": "2026-05-01T01:00:00Z",
    }]
    supabase = _FakeSupabase(responses={
        "workspace_items": rows,
        "case_documents": RuntimeError("doc lookup down"),
    })

    ctx = await load_workspace_context(supabase, "conv-1")

    assert len(ctx["attachments"]) == 1
    assert ctx["attachments"][0]["text_excerpt"] is None
    assert ctx["attachments"][0]["title"] == "عقد.pdf"


@pytest.mark.asyncio
async def test_empty_conversation_returns_empty_shape():
    """No rows -> all-empty buckets and convo_context=None."""
    supabase = _FakeSupabase(responses={"workspace_items": []})

    ctx = await load_workspace_context(supabase, "conv-empty")

    assert ctx == {
        "attachments": [],
        "notes": [],
        "agent_outputs": [],
        "convo_context": None,
        "references": [],
    }


@pytest.mark.asyncio
async def test_db_error_returns_empty_shape_without_raising():
    """Non-relation-missing DB errors must NOT raise into agent code."""
    supabase = _FakeSupabase(responses={
        "workspace_items": RuntimeError("connection refused"),
    })

    ctx = await load_workspace_context(supabase, "conv-1")

    assert ctx == {
        "attachments": [],
        "notes": [],
        "agent_outputs": [],
        "convo_context": None,
        "references": [],
    }


@pytest.mark.asyncio
async def test_pre_migration_fallback_to_artifacts_table():
    """When workspace_items doesn't exist (pre-migration-026), the
    helper falls back to the artifacts table and translates rows."""
    artifact_rows = [
        {
            "artifact_id": "art-search",
            "conversation_id": "conv-1",
            "title": "synthesis",
            "content_md": "## نتائج البحث",
            "artifact_type": "legal_synthesis",
            "is_editable": False,
            "metadata": {"detail_level": "medium"},
            "created_at": "2026-04-30T00:00:00Z",
        },
        {
            "artifact_id": "art-writing",
            "conversation_id": "conv-1",
            "title": "memo",
            "content_md": "# مسوّدة",
            "artifact_type": "memo",
            "is_editable": True,
            "metadata": {},
            "created_at": "2026-04-29T00:00:00Z",
        },
    ]

    # Toggle: first table('workspace_items') call raises relation-missing,
    # then artifacts call returns rows.
    class _PreMigrationSupabase(_FakeSupabase):
        def table(self, name: str) -> _FakeQuery:
            self.calls.append(f"<table>{name}")
            if name == "workspace_items":
                # Raise on execute.
                self.responses[name] = RuntimeError(
                    'relation "workspace_items" does not exist (SQLSTATE 42P01)'
                )
            return _FakeQuery(self, name)

    supabase = _PreMigrationSupabase(responses={"artifacts": artifact_rows})

    ctx = await load_workspace_context(supabase, "conv-1")

    # Both kinds bucketed under agent_outputs (no notes/attachments
    # representable pre-migration).
    out_ids = [a["item_id"] for a in ctx["agent_outputs"]]
    assert "art-search" in out_ids
    assert "art-writing" in out_ids

    by_id = {a["item_id"]: a for a in ctx["agent_outputs"]}
    # is_editable=False -> agent_search; subtype promoted from artifact_type.
    assert by_id["art-search"]["subtype"] == "legal_synthesis"
    # is_editable=True -> agent_writing.
    assert by_id["art-writing"]["subtype"] == "memo"

    # Buckets that need notes/attachments/convo_context kinds stay empty
    # because pre-migration artifacts can't represent those.
    assert ctx["notes"] == []
    assert ctx["attachments"] == []
    assert ctx["convo_context"] is None
    assert ctx["references"] == []


@pytest.mark.asyncio
async def test_unknown_kind_is_silently_ignored():
    """Forward-compat: an unknown kind shouldn't crash the partitioner."""
    rows = [
        {
            "item_id": "wat-1",
            "kind": "future_kind",
            "title": "?",
            "content_md": "?",
            "metadata": {},
            "created_at": "2026-05-01T00:00:00Z",
        },
        {
            "item_id": "note-1",
            "kind": "note",
            "title": "n",
            "content_md": "...",
            "metadata": {},
            "created_at": "2026-05-01T00:00:00Z",
        },
    ]
    supabase = _FakeSupabase(responses={"workspace_items": rows})

    ctx = await load_workspace_context(supabase, "conv-1")

    assert [n["item_id"] for n in ctx["notes"]] == ["note-1"]
    # Unknown kind didn't end up anywhere.
    flat = (
        ctx["notes"]
        + ctx["agent_outputs"]
        + ctx["attachments"]
        + ctx["references"]
    )
    assert "wat-1" not in [item.get("item_id") for item in flat]
