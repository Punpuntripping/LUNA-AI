"""unfold_workspace_item under the WI preview lock (wi_preview_lock.md §6).

The router of a free user sees the SAME preview the web/app shows, between two
markers that say the lock is deliberate; planner / writer deps carry no policy
and keep the full content. Self-contained: a small filter-honouring Supabase
fake lives below (same semantics as this directory's conftest one).
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from agents.tool_repository import unfold_workspace_item as uwi
from shared.workspace.preview_lock import LockPolicy, cited_numbers, preview_cut


class _Query:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)
        self._filters: list[Any] = []
        self._single = False
        self._order: tuple[str, bool] | None = None

    def select(self, _cols: str) -> "_Query":
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col: str, vals: list[Any]) -> "_Query":
        wanted = set(vals)
        self._filters.append(lambda r: r.get(col) in wanted)
        return self

    def is_(self, col: str, val: str) -> "_Query":
        self._filters.append(lambda r: r.get(col) is None)
        return self

    def order(self, col: str, desc: bool = False) -> "_Query":
        self._order = (col, desc)
        return self

    def maybe_single(self) -> "_Query":
        self._single = True
        return self

    def execute(self) -> SimpleNamespace:
        rows = [r for r in self._rows if all(f(r) for f in self._filters)]
        if self._order is not None:
            rows = sorted(rows, key=lambda r: r.get(self._order[0]), reverse=self._order[1])
        if self._single:
            return SimpleNamespace(data=rows[0] if rows else None)
        return SimpleNamespace(data=rows)


@pytest.fixture
def make_supabase():
    def _make(tables: dict[str, list[dict]]):
        return SimpleNamespace(table=lambda name: _Query(tables.get(name, [])))

    return _make

ITEM = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"
SVC = "66666666-6666-6666-6666-666666666666"

LOCK_FROM = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
POLICY = LockPolicy(lock_from=LOCK_FROM)
AFTER = (LOCK_FROM + timedelta(hours=1)).isoformat()


def _body() -> str:
    """Ten paragraphs, each citing its own [n] — the hidden part cites the tail."""
    paras = [
        f"الفقرة {k} تبيّن حكم الالتزام التعاقدي في هذه المسألة على نحو مفصّل "
        f"ومستند إلى النص النظامي ذي الصلة بالواقعة المعروضة [{k}]."
        for k in range(1, 11)
    ]
    return "## التحليل\n\n" + "\n\n".join(paras)


def _tables(**item_over) -> dict:
    item = {
        "item_id": ITEM, "user_id": USER, "deleted_at": None,
        "title": "تقرير", "content_md": _body(), "wi_seq": 3,
        "kind": "agent_search", "created_by": "agent", "created_at": AFTER,
        "metadata": {"subtype": "legal_synthesis"}, "created_while_paid": False,
    }
    item.update(item_over)
    return {
        "workspace_items": [item],
        "workspace_item_references": [
            {"wi_id": ITEM, "item_id": SVC, "ref_id": "", "domain": "compliance",
             "n": n, "used": True}
            for n in range(1, 11)
        ],
        "services": [{"id": SVC, "service_name_ar": "وزارة - خدمة"}],
    }


def test_locked_unfold_is_the_preview_between_deliberate_markers(make_supabase):
    sb = make_supabase(_tables())
    parts = uwi.unfold_parts(sb, ITEM, USER, lock_policy=POLICY)
    cut = preview_cut(_body())

    assert parts.lock_header.startswith(
        "〔[نظام] PLAN LOCK — DELIBERATE, NOT AN ERROR. WI-3 is intentionally preview-locked"
    )
    assert f"first {cut.pct}% ({cut.shown_chars} of {cut.total_chars} characters)" in parts.lock_header
    assert parts.lock_footer.startswith(f"〔[نظام] END OF VISIBLE PART — {100 - cut.pct}% of WI-3 ")
    assert "Never apologise for a technical problem" in parts.lock_footer
    assert "«بقية هذا المستند متاحة في الباقات المدفوعة — يمكنك الترقية لعرضه كاملاً»" in parts.lock_footer
    assert "http" not in parts.lock_header + parts.lock_footer      # no URL (store rules)

    # The body is the backend's preview, never more.
    assert parts.body.startswith(cut.visible.strip())
    assert "الفقرة 10" not in parts.body
    # Sources: only those the visible part cites, plus a count of the rest.
    visible = cited_numbers(cut.visible)
    listed = {int(m) for m in re.findall(r"^\[(\d+)\]", parts.body, flags=re.M)}
    assert listed == visible
    hidden = 10 - len(visible)
    assert 3 <= hidden <= 10
    assert parts.body.rstrip().endswith(f"و{hidden} مصادر أخرى في الجزء المقفل")

    joined = uwi.unfold_item(sb, ITEM, USER, lock_policy=POLICY)
    assert joined.startswith(parts.lock_header) and joined.endswith(parts.lock_footer)


@pytest.mark.parametrize(
    "over",
    [
        {"created_at": (LOCK_FROM - timedelta(seconds=1)).isoformat()},   # before launch
        {"created_while_paid": True},                                     # made while paid
        {"kind": "note", "created_by": "user"},                           # the user's note
        {"kind": "agent_writing", "metadata": {"subtype": "summary"}},    # §9.1 exempt
    ],
)
def test_policy_leaves_open_items_byte_identical(make_supabase, over):
    sb = make_supabase(_tables(**over))
    assert uwi.unfold_item(sb, ITEM, USER, lock_policy=POLICY) == uwi.unfold_item(sb, ITEM, USER)
    assert "PLAN LOCK" not in uwi.unfold_item(sb, ITEM, USER)
    assert "الفقرة 10" in uwi.unfold_item(sb, ITEM, USER)


def _deps(sb, **extra):
    return SimpleNamespace(supabase=sb, user_id=USER, wi_alias_map={3: ITEM}, **extra)


def test_tool_locks_only_for_deps_carrying_the_policy(make_supabase, monkeypatch):
    """RouterDeps carries wi_lock_policy; planner / writer deps never do."""
    encoded: list[str] = []
    monkeypatch.setattr(uwi, "_encode_unfold_output", lambda _sb, _u, c: encoded.append(c) or c)
    sb = make_supabase(_tables())

    router_out = asyncio.run(uwi.unfold_for_deps(_deps(sb, wi_lock_policy=POLICY), "WI-3"))
    assert "PLAN LOCK — DELIBERATE, NOT AN ERROR" in router_out
    assert "END OF VISIBLE PART" in router_out and "الفقرة 10" not in router_out
    # The masking codec sees the body only; the system markers wrap it after.
    assert "PLAN LOCK" not in encoded[-1]

    planner_out = asyncio.run(uwi.unfold_for_deps(_deps(sb), "WI-3"))
    assert "PLAN LOCK" not in planner_out and "الفقرة 10" in planner_out
    # A paid user (or the flag off): the router itself carries None.
    assert asyncio.run(uwi.unfold_for_deps(_deps(sb, wi_lock_policy=None), "WI-3")) == planner_out


def test_only_router_deps_declare_the_lock_field():
    from agents.deep_search_v4.planner.deps import PlannerDeps
    from agents.router.router import RouterDeps
    from agents.writer_planner.deps import WriterPlannerDeps

    def names(cls) -> set[str]:
        if dataclasses.is_dataclass(cls):
            return {f.name for f in dataclasses.fields(cls)}
        return set(getattr(cls, "model_fields", {}) or vars(cls))

    assert "wi_lock_policy" in names(RouterDeps)
    assert "wi_lock_policy" not in names(PlannerDeps)
    assert "wi_lock_policy" not in names(WriterPlannerDeps)


def test_tool_docstring_carries_no_lock_state():
    """Per-user lock state lives in the tool RESULT only, never in the schema."""
    # The registration (the tool's name, signature and docstring — what the
    # model's cached tool schema is built from) says nothing about the lock.
    src = inspect.getsource(uwi.register_unfold_workspace_item)
    for word in ("PLAN LOCK", "preview-locked", "free plan", "wi_lock_policy", "locked"):
        assert word not in src


def test_lock_columns_are_selected_only_when_the_lock_can_bite():
    selects: list[str] = []

    class _Q:
        def select(self, cols):
            selects.append(cols)
            return self

        def eq(self, *_a):
            return self

        def is_(self, *_a):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return SimpleNamespace(data=None)

    sb = SimpleNamespace(table=lambda _n: _Q())
    uwi._fetch_item(sb, ITEM, USER)
    uwi._fetch_item(sb, ITEM, USER, with_lock_columns=True)
    assert selects[0] == "title, content_md"
    assert "created_while_paid" in selects[1] and "wi_seq" in selects[1]
