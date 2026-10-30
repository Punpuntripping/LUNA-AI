"""edit_artifact under the WI preview lock (wi_preview_lock.md §1.3).

The artifact editor reads and rewrites the WHOLE stored body, so a router turn
of a free user must not run it on a preview-locked item: it would quote the
hidden part back in its change summary, or move it into the visible preview.
Same rule as a body PATCH from the workspace. Only RouterDeps carries the
policy, so a paid user / the flag off / a non-lockable item edit as before.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import agents.artifact_editor as artifact_editor
from agents.tool_repository import edit_artifact as ea
from shared.workspace.preview_lock import LockPolicy

ITEM = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"

LOCK_FROM = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
POLICY = LockPolicy(lock_from=LOCK_FROM)
AFTER = (LOCK_FROM + timedelta(hours=1)).isoformat()
BEFORE = (LOCK_FROM - timedelta(days=1)).isoformat()


class _Query:
    def __init__(self, rows: list[dict], fail: bool = False) -> None:
        self._rows = list(rows)
        self._filters: list[Any] = []
        self._fail = fail

    def select(self, _cols: str) -> "_Query":
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def is_(self, col: str, _val: str) -> "_Query":
        self._filters.append(lambda r: r.get(col) is None)
        return self

    def maybe_single(self) -> "_Query":
        return self

    def execute(self) -> SimpleNamespace:
        if self._fail:
            raise RuntimeError("postgrest down")
        rows = [r for r in self._rows if all(f(r) for f in self._filters)]
        return SimpleNamespace(data=rows[0] if rows else None)


def _supabase(fail: bool = False, **item_over):
    item = {
        "item_id": ITEM, "user_id": USER, "deleted_at": None,
        "kind": "agent_search", "created_by": "agent", "created_at": AFTER,
        "metadata": {"subtype": "legal_synthesis"}, "created_while_paid": False,
    }
    item.update(item_over)
    return SimpleNamespace(table=lambda _name: _Query([item], fail=fail))


class _CaptureAgent:
    def __init__(self) -> None:
        self.fn = None

    def tool(self, fn):
        self.fn = fn
        return fn


@pytest.fixture
def editor_calls(monkeypatch):
    calls: list[dict] = []

    async def _fake_editor(_supabase, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            status="edited", change_summary="غيّرنا موضعاً واحداً", assumptions=None,
        )

    monkeypatch.setattr(artifact_editor, "run_artifact_editor", _fake_editor)
    return calls


def _run(supabase, policy):
    agent = _CaptureAgent()
    ea.register_edit_artifact(agent)
    deps = SimpleNamespace(
        supabase=supabase, user_id=USER, user_message="انقل الخلاصة إلى البداية",
        conversation_id="c", wi_alias_map={3: ITEM}, pending_sse_events=[],
        wi_lock_policy=policy,
    )
    out = asyncio.run(agent.fn(SimpleNamespace(deps=deps), "WI-3", "انقل الخلاصة"))
    return out, deps


def test_a_locked_item_is_refused_with_the_deliberate_marker(editor_calls):
    out, deps = _run(_supabase(), POLICY)

    assert out.startswith("〔[نظام] PLAN LOCK — DELIBERATE, NOT AN ERROR. WI-3 is preview-locked")
    assert "Nothing was changed" in out
    assert "Never apologise for a technical problem" in out
    assert "http" not in out                     # store rules: no URL in router text
    assert editor_calls == []                    # the editor never saw the full body
    assert deps.pending_sse_events == []


def test_no_policy_edits_as_before(editor_calls):
    out, deps = _run(_supabase(), None)          # paid user / flag off
    assert out == "[WI-3] غيّرنا موضعاً واحداً"
    assert len(editor_calls) == 1
    assert deps.pending_sse_events == [{"type": "workspace_item_updated", "item_id": ITEM}]


def test_an_item_older_than_the_lock_still_edits(editor_calls):
    out, _ = _run(_supabase(created_at=BEFORE), POLICY)
    assert out.startswith("[WI-3]")
    assert len(editor_calls) == 1


def test_an_item_made_while_paid_still_edits(editor_calls):
    out, _ = _run(_supabase(created_while_paid=True), POLICY)
    assert out.startswith("[WI-3]")
    assert len(editor_calls) == 1


def test_a_users_own_note_is_never_locked(editor_calls):
    out, _ = _run(_supabase(kind="note", created_by="user"), POLICY)
    assert out.startswith("[WI-3]")
    assert len(editor_calls) == 1


def test_an_unreadable_row_fails_closed(editor_calls):
    out, _ = _run(_supabase(fail=True), POLICY)
    assert out.startswith("〔[نظام] PLAN LOCK")
    assert editor_calls == []
