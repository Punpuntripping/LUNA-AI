"""Router + WI preview lock (wi_preview_lock.md §6).

* the static, deliberate-lock rule is in the cached SYSTEM_PROMPT and carries
  no per-user state;
* the policy rides RouterDeps only, filled by run_router;
* the no-summary fallback of a locked item is cut from its preview, while a
  real summary is kept in full (owner decision §9.5).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from agents.router import router as router_mod
from agents.router.context import _load_workspace_item_summaries
from shared.workspace.preview_lock import LockPolicy, preview_cut

LOCK_FROM = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
POLICY = LockPolicy(lock_from=LOCK_FROM)
AFTER = (LOCK_FROM + timedelta(hours=1)).isoformat()

BODY = "\n\n".join(
    f"الفقرة {k} تشرح حكم المسألة المعروضة بتفصيل كافٍ ومستند إلى النص [{k}]." for k in range(1, 30)
)


def test_system_prompt_carries_the_static_deliberate_lock_rule():
    p = router_mod.SYSTEM_PROMPT
    assert "Preview-locked items (a plan rule, never an error)" in p
    assert "〔[نظام] PLAN LOCK — DELIBERATE, NOT AN ERROR …〕" in p
    assert "never apologise for a technical problem" in p
    assert "Never write either marker yourself" in p
    # Static text only: no percentage, no count, no WI alias of a real item.
    rule = p.split("Preview-locked items", 1)[1].split("\n- ", 1)[0]
    assert "%" not in rule and "WI-" not in rule
    # "full content" softened where the router is told what unfold returns.
    assert "to get the full content along with" not in p


def test_router_deps_default_has_no_policy():
    deps = router_mod.RouterDeps(
        supabase=None, user_id="u", conversation_id="c", case_id=None,
        case_memory_md=None, case_metadata=None, user_preferences=None,
    )
    assert deps.wi_lock_policy is None


def test_run_router_puts_the_policy_on_router_deps(monkeypatch):
    seen: dict[str, Any] = {}

    async def _fake_run(_q, *, deps, **_k):
        seen["deps"] = deps
        return SimpleNamespace(
            output=router_mod.ChatResponse(message="رد قصير كافٍ للاختبار هنا."),
            usage=lambda: SimpleNamespace(requests=1, output_tokens=1),
        )

    monkeypatch.setattr(router_mod.router_agent, "run", _fake_run)

    class _Span:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def record_run(self, *_a, **_k):
            pass

        def set(self, **_k):
            pass

        def set_outcome(self, *_a):
            pass

    monkeypatch.setattr(router_mod, "track_stage", lambda *_a, **_k: _Span())
    for policy in (POLICY, None):
        asyncio.run(router_mod.run_router(
            question="سؤال", supabase=None, user_id="u", conversation_id="c",
            case_id=None, case_memory_md=None, case_metadata=None,
            user_preferences=None, message_history=[], wi_lock_policy=policy,
        ))
        assert seen["deps"].wi_lock_policy is policy


class _Res:
    def __init__(self, data):
        self.data = data


def _sb(rows: list[dict]):
    selects: list[str] = []

    class _Q:
        def select(self, cols, *_a, **_k):
            selects.append(cols)
            return self

        def eq(self, *_a, **_k):
            return self

        def is_(self, *_a, **_k):
            return self

        def order(self, *_a, **_k):
            return self

        def execute(self):
            return _Res(rows)

    return SimpleNamespace(table=lambda _n: _Q(), selects=selects)


def _row(**over) -> dict:
    row = {
        "item_id": "ws-1", "wi_seq": 1, "kind": "agent_search", "title": "بحث",
        "summary": None, "content_md": BODY, "created_at": AFTER, "doc_type": None,
        "created_by": "agent", "created_while_paid": False,
        "metadata": {"subtype": "legal_synthesis"},
    }
    row.update(over)
    return row


def test_summary_fallback_of_a_locked_item_comes_from_its_preview():
    sb = _sb([_row()])
    (s,), _ = _load_workspace_item_summaries(sb, "c", "u", POLICY)
    preview = preview_cut(BODY).visible
    assert preview.startswith(s["summary"])
    assert len(s["summary"]) <= len(preview)
    assert s["summary_truncated"] is True          # never presented as the whole text
    assert "created_while_paid" in sb.selects[-1]


def test_summary_fallback_without_policy_is_unchanged():
    sb = _sb([_row()])
    (s,), _ = _load_workspace_item_summaries(sb, "c", "u")
    # Exactly the pre-lock behaviour: the body clipped to 1500 then rstripped.
    assert s["summary"] == BODY[:1500].rstrip()
    assert "created_while_paid" not in sb.selects[-1]


def test_real_summaries_stay_full_under_the_lock():
    """§9.5: the owner accepted the summary leak for better follow-ups."""
    full = "ملخص كامل للتقرير يغطي جميع نتائجه وتوصياته. " * 20
    (s,), _ = _load_workspace_item_summaries(_sb([_row(summary=full)]), "c", "u", POLICY)
    assert s["summary"] == full
    assert not s.get("summary_is_content")
