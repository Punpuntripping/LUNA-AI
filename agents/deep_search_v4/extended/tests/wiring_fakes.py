"""Fakes for the wiring suites (``test_wiring_*.py``): an in-memory supabase client, a
seeded plan card, and the orchestrator / A / B seams a turn touches. No network, no
model. Not collected by pytest (no ``test_`` prefix)."""
from __future__ import annotations

import itertools
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

from agents.deep_search_v4.extended import wiring as W
from agents.deep_search_v4.extended.card import build_card, build_options, card_text_ar
from agents.deep_search_v4.extended.models import (
    CheaperOption,
    ExtendedPlan,
    ExtendedPlanPause,
    ExtendedRequest,
)

USER = "user-1"
CONVO = "conv-1"
RUN = "run-card-1"
ORIGINAL = "ما موقفي من مطالبة المقاول بقيمة أمر التغيير؟"

_CLOCK = itertools.count()
_T0 = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)


def _stamp() -> str:
    """A strictly increasing ``created_at`` — the DB default ``now()`` of an insert."""
    return (_T0 + timedelta(milliseconds=next(_CLOCK))).isoformat()


# --------------------------------------------------------------------------- fake supabase


class FakeDB:
    """Tables are lists of dict rows. ``fail`` holds ``table`` or ``(table, op)``;
    ``log`` every executed ``(table, op)``. ``col->>key`` filters read jsonb keys."""

    def __init__(self, **tables: list[dict]) -> None:
        self.tables: dict[str, list[dict]] = defaultdict(list)
        for name, rows in tables.items():
            self.tables[name] = [dict(r) for r in rows]
        self.fail: set = set()
        self.log: list[tuple[str, str]] = []

    def table(self, name: str) -> "_Query":
        return _Query(self, name)

    def row(self, table: str, **eq: Any) -> dict | None:
        for r in self.tables[table]:
            if all(r.get(k) == v for k, v in eq.items()):
                return r
        return None


def _value(row: dict, key: str) -> Any:
    if "->>" in key:
        col, sub = key.split("->>", 1)
        inner = row.get(col)
        v = inner.get(sub) if isinstance(inner, dict) else None
        return None if v is None else str(v)
    return row.get(key)


class _Query:
    def __init__(self, db: FakeDB, name: str) -> None:
        self.db, self.name = db, name
        self.op = "select"
        self.payload: Any = None
        self.tests: list[Callable[[dict], bool]] = []
        self.orders: list[tuple[str, bool]] = []
        self.lim: int | None = None
        self.single = False

    def select(self, *_a: Any, **_k: Any) -> "_Query":
        return self

    def insert(self, rows: Any) -> "_Query":
        self.op, self.payload = "insert", rows
        return self

    def update(self, values: dict) -> "_Query":
        self.op, self.payload = "update", values
        return self

    def delete(self) -> "_Query":
        self.op = "delete"
        return self

    def eq(self, k: str, v: Any) -> "_Query":
        self.tests.append(lambda r: _value(r, k) is not None and str(_value(r, k)) == str(v))
        return self

    def neq(self, k: str, v: Any) -> "_Query":
        self.tests.append(lambda r: str(_value(r, k)) != str(v))
        return self

    def in_(self, k: str, vals: Any) -> "_Query":
        s = {str(v) for v in vals}
        self.tests.append(lambda r: str(_value(r, k)) in s)
        return self

    def is_(self, k: str, _v: Any) -> "_Query":
        self.tests.append(lambda r: r.get(k) is None)
        return self

    def order(self, k: str, desc: bool = False) -> "_Query":
        self.orders.append((k, desc))
        return self

    def limit(self, n: int) -> "_Query":
        self.lim = n
        return self

    def maybe_single(self) -> "_Query":
        self.single = True
        return self

    def execute(self) -> SimpleNamespace:
        self.db.log.append((self.name, self.op))
        if self.name in self.db.fail or (self.name, self.op) in self.db.fail:
            raise RuntimeError(f"fake failure on {self.name}.{self.op}")
        table = self.db.tables[self.name]
        if self.op == "insert":
            rows = self.payload if isinstance(self.payload, list) else [self.payload]
            for r in rows:
                r = dict(r)
                if self.name == "messages":
                    r.setdefault("created_at", _stamp())
                    r.setdefault("metadata", {})
                table.append(r)
            return SimpleNamespace(data=[dict(r) for r in rows])
        hit = [r for r in table if all(t(r) for t in self.tests)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in hit])
        if self.op == "delete":
            ids = {id(r) for r in hit}
            self.db.tables[self.name] = [r for r in table if id(r) not in ids]
            return SimpleNamespace(data=hit)
        rows = [dict(r) for r in hit]
        for k, desc in reversed(self.orders):
            rows.sort(key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        if self.lim is not None:
            rows = rows[: self.lim]
        if self.single:
            return SimpleNamespace(data=rows[0] if rows else None)
        return SimpleNamespace(data=rows)


# --------------------------------------------------------------------------- a plan and its card


def plan() -> ExtendedPlan:
    return ExtendedPlan(
        understanding="المستخدم هو المقاول ويريد معرفة استحقاقه لقيمة أمر التغيير.",
        client_side="claimant",
        requests=[
            ExtendedRequest(
                title="استحقاق قيمة أمر التغيير", angles=["A1"], sections=["D1.S3"],
                mode="reg_compliance_led", max_sub_queries=8, focus="استحقاق القيمة",
            ),
            ExtendedRequest(
                title="مبلغ 43,700 دون أمر تغيير ثانٍ", angles=["A2"], sections=["D1.S7"],
                mode="case_led", max_sub_queries=8, focus="المبلغ الثاني",
            ),
        ],
        cheaper=CheaperOption(
            note="أدمج البحثين في بحث واحد",
            requests=[ExtendedRequest(
                title="أمر التغيير والمبلغ الثاني", angles=["A1", "A2"], mode="full",
                max_sub_queries=6, focus="الاثنان معاً",
            )],
        ),
    )


def turn_input(**over: Any) -> SimpleNamespace:
    base = dict(
        user_id=USER, conversation_id=CONVO, case_id=None, describe_query=ORIGINAL,
        attached_items=[SimpleNamespace(item_id="wi-doc-1", wi_seq=3, kind="attachment")],
        recent_messages=[], analyzer_handoff=None, analyzer_checked=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def seed_card(db: FakeDB, *, run_id: str = RUN, points_left: float | None = 18.0,
              expires_in: timedelta = timedelta(hours=24)) -> dict:
    """The conversation as a planning turn leaves it: the user's message, then the card
    (written by the real ``record_extended_pause``). Returns the pause row."""
    db.tables["messages"].append({
        "message_id": "m-original", "conversation_id": CONVO, "role": "user",
        "content": ORIGINAL, "metadata": {}, "created_at": _stamp(),
    })
    p = plan()
    options = build_options(p, planner_points=0.5)
    card = build_card(options, p, run_id=run_id, points_left=points_left)
    pause = ExtendedPlanPause(
        run_id=run_id, map_id="map-1", budget_mode="20", points_left=points_left,
        describe_query=ORIGINAL, user_message_id="m-original", attached_item_ids=["wi-doc-1"],
        task_label="أمر التغيير", plan=p, options=options, planner_points=0.5,
    )
    q = W.record_extended_pause(
        db, input=turn_input(), turn=W.ExtendedTurn(run_id=run_id, user_message_id="m-original",
                                                    task_label="أمر التغيير"),
        pause=pause, card=card, text=card_text_ar(card),
    )
    assert q is not None
    row = db.row("paused_runs", run_id=run_id)
    assert row is not None
    if expires_in != timedelta(hours=24):
        row["expires_at"] = (datetime.now(timezone.utc) + expires_in).isoformat()
    return row


def add_click(db: FakeDB, content: str, *, user_message_id: str = "m-click",
              placeholder_id: str = "m-placeholder") -> None:
    """This turn's rows, as message_service writes them before the pipeline."""
    db.tables["messages"].append({
        "message_id": user_message_id, "conversation_id": CONVO, "role": "user",
        "content": content, "metadata": {}, "created_at": _stamp(),
    })
    db.tables["messages"].append({
        "message_id": placeholder_id, "conversation_id": CONVO, "role": "assistant",
        "content": "", "metadata": {}, "created_at": _stamp(),
    })


def card_row(db: FakeDB, run_id: str = RUN) -> dict:
    for r in db.tables["messages"]:
        meta = r.get("metadata") or {}
        if meta.get("kind") == "agent_question" and meta.get("run_id") == run_id:
            return r
    raise AssertionError(f"no card row for {run_id}")


def outcome(db: FakeDB, run_id: str = RUN) -> str | None:
    return (card_row(db, run_id)["metadata"].get("extended_plan") or {}).get("outcome")
