"""A paused run keeps the router's files when the user answers its question.

Prod 2026-11-27 (conversation fa4fd43c, test account): «ابحث» with a judgment the
router attached → the deep-search planner asked a clarifying question (it saw the
judgment) → «ابغى بحث شامل» → the report said «فلم يتضمن ما أُتيح لي نصّ صك الحكم
المرفق». The resume leg loaded only the files attached to the REPLY; the router's
pick lived on deps, and deps do not survive a pause. 9 of 57 resumed deep searches
in the 14 days before likely lost a file the same way.

Pinned here:

- ``_record_deferred`` saves the paused leg's files on the pause
  (``deferred_payload.attached_item_ids``); without them the key is not written;
- ``_resume_attachment_ids`` = saved files first, then the reply's, deduped; a
  pause recorded before the fix (no key) = the reply's files only, as before;
- the deep-search resume gives the decider AND the resumed search the saved +
  reply files, and a chained pause carries them forward;
- the writing resume does the same.

Sync tests only: CI installs plain pytest, without pytest-asyncio.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ToolCallPart

import agents.deep_search_v4.planner.agent as planner_agent
import agents.orchestrator as orch
from agents.deep_search_v4.planner import PlannerDecision
from agents.models import WorkspaceItemSnapshot

USER = "user-A"
CONVO = "conv-A"


class _Call:
    tool_call_id = "tc-1"
    tool_name = "ask_user"

    def args_as_dict(self) -> dict:
        return {"question": "ما الذي تريد أن نبحث عنه؟"}


class _Deferred:
    calls = [_Call()]


class _PlannerResult:
    def __init__(self, output: Any = None) -> None:
        self.output = output

    def all_messages_json(self) -> bytes:
        return b"[]"

    def all_messages(self) -> list:
        return []


def _snap(item_id: str, seq: int) -> WorkspaceItemSnapshot:
    return WorkspaceItemSnapshot(
        item_id=item_id, kind="attachment", title=f"ملف {seq}", content_md="نص", wi_seq=seq,
    )


def _collect(agen) -> list[dict]:
    async def _run() -> list[dict]:
        return [ev async for ev in agen]

    return asyncio.run(_run())


async def _none(*_a: Any, **_k: Any) -> None:
    return None


# --------------------------------------------------------------------------- #
# _record_deferred — the pause saves the files
# --------------------------------------------------------------------------- #


def _record(monkeypatch, **extra: Any) -> dict:
    seen: dict = {}

    def _record_pause(_sb: Any, rec: Any, run_id: Any = None) -> str:
        seen["payload"] = rec.deferred_payload
        return "run-1"

    monkeypatch.setattr(orch, "record_pause", _record_pause)
    orch._record_deferred(
        supabase=object(), planner_result=_PlannerResult(), planner_output=_Deferred(),
        user_id=USER, conversation_id=CONVO, case_id=None, user_message_id="um1",
        agent_family="deep_search", describe_query="ابحث", **extra,
    )
    return seen["payload"]


def test_the_pause_saves_the_files_deduped(monkeypatch):
    payload = _record(monkeypatch, attached_item_ids=["f1", "f1", "", "f2"])
    assert payload["attached_item_ids"] == ["f1", "f2"]
    assert payload["tool_call_id"] == "tc-1"


def test_without_files_the_payload_is_unchanged(monkeypatch):
    payload = _record(monkeypatch)
    assert "attached_item_ids" not in payload
    assert set(payload) == {"tool_call_id", "tool_name", "args", "partial_output", "analyzer"}


def test_an_empty_pick_is_saved_as_empty(monkeypatch):
    assert _record(monkeypatch, attached_item_ids=[])["attached_item_ids"] == []


def test_the_files_sit_beside_the_one_planners_offer(monkeypatch):
    # The one planner saves its «بحث شامل» offer on the same payload; both keys stay.
    from agents.deep_search_v4.extended.offer import PAUSE_KEY

    offer = {"hint": True, "item_ids": ["f9"]}
    payload = _record(monkeypatch, extended=offer, attached_item_ids=["f1"])
    assert payload[PAUSE_KEY] == offer
    assert payload["attached_item_ids"] == ["f1"]


# --------------------------------------------------------------------------- #
# _resume_attachment_ids — saved first, then the reply's
# --------------------------------------------------------------------------- #


def test_resume_ids_are_saved_then_reply(monkeypatch):
    monkeypatch.setattr(orch, "_load_reply_attachment_ids", lambda *_a: ["r1", "f2"])
    pending = {"deferred_payload": {"tool_call_id": "x", "attached_item_ids": ["f1", "f2"]}}
    assert orch._resume_attachment_ids(object(), pending, "um1") == ["f1", "f2", "r1"]


def test_an_old_pause_keeps_the_reply_files_only(monkeypatch):
    monkeypatch.setattr(orch, "_load_reply_attachment_ids", lambda *_a: ["r1", "r2"])
    assert orch._resume_attachment_ids(object(), {"deferred_payload": {"tool_call_id": "x"}}, "um1") == ["r1", "r2"]
    assert orch._resume_attachment_ids(object(), {}, "um1") == ["r1", "r2"]
    assert orch._resume_attachment_ids(object(), None, "um1") == ["r1", "r2"]


def test_a_malformed_saved_list_is_ignored(monkeypatch):
    monkeypatch.setattr(orch, "_load_reply_attachment_ids", lambda *_a: ["r1"])
    pending = {"deferred_payload": {"attached_item_ids": "f1"}}
    assert orch._resume_attachment_ids(object(), pending, "um1") == ["r1"]


# --------------------------------------------------------------------------- #
# The resume legs
# --------------------------------------------------------------------------- #


def _resume_env(monkeypatch) -> dict:
    """Fakes for a resume leg; ``state['loads']`` records every file load."""
    state: dict = {"loads": []}

    def _load(_sb: Any, ids: list[str], _user: str, _convo: str) -> list[WorkspaceItemSnapshot]:
        ids = list(ids)
        state["loads"].append(ids)
        return [_snap(i, n) for n, i in enumerate(ids, start=1)]

    def _record_deferred(**kw: Any) -> tuple[str, str]:
        state["recorded"] = kw
        return "سؤال آخر", "run-2"

    monkeypatch.setattr(orch, "_load_attached_items", _load)
    monkeypatch.setattr(orch, "_load_reply_attachment_ids", lambda *_a: ["reply-file"])
    monkeypatch.setattr(orch, "_load_recent_messages", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_load_compaction_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "_load_conversation_memory", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "_load_prior_search_summaries", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_settle_memory_writer", _none)
    monkeypatch.setattr(orch, "_check_deep_search_gate", _none)
    monkeypatch.setattr(orch, "analyzer_enabled", lambda _u: False)
    monkeypatch.setattr(orch, "_feed_resume_decider", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "_resolve_pause_loud", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "resolve_pause", lambda *_a, **_k: True)
    monkeypatch.setattr(orch, "_record_deferred", _record_deferred)
    return state


def _pending(family: str, **payload: Any) -> dict:
    return {
        "run_id": "run-1",
        "agent_family": family,
        "task_label": "بحث",
        "message_history": b"[]",
        "deferred_payload": {"tool_call_id": "tc-1", **payload},
    }


def _resume(pending: dict) -> list[dict]:
    return _collect(orch._resume_major_agent_inner(
        pending=pending, user_reply="ابغى بحث شامل", supabase=object(), user_id=USER,
        conversation_id=CONVO, case_id=None, user_message_id="um1", assistant_message_id="am1",
    ))


def _decider_returning(output: Any, seen: dict):
    class _Decider:
        async def run(self, *_a: Any, deps: Any = None, **_k: Any) -> Any:
            seen["decider_files"] = [s.item_id for s in deps.attached_items]
            seen["alias_map"] = dict(deps.wi_alias_map)
            return _PlannerResult(output)

    return lambda *_a, **_k: _Decider()


def test_deep_search_resume_keeps_the_routers_files_and_chains_them(monkeypatch):
    state = _resume_env(monkeypatch)
    seen: dict = {}
    again = DeferredToolRequests(calls=[ToolCallPart(
        tool_name="ask_user", args={"question": "ومن موكلك؟"}, tool_call_id="tc-2",
    )])
    monkeypatch.setattr(planner_agent, "create_planner_decider", _decider_returning(again, seen))

    events = _resume(_pending("deep_search", attached_item_ids=["router-file"]))

    assert state["loads"] == [["router-file", "reply-file"]]
    assert seen["decider_files"] == ["router-file", "reply-file"]
    assert seen["alias_map"] == {1: "router-file", 2: "reply-file"}
    # The chained pause carries every file forward.
    assert state["recorded"]["attached_item_ids"] == ["router-file", "reply-file"]
    assert any(e.get("type") == "agent_question" for e in events)


def test_the_resumed_search_gets_the_same_files(monkeypatch):
    state = _resume_env(monkeypatch)
    seen: dict = {}
    decision = PlannerDecision(mode="reg_compliance_led", rationale="r")
    monkeypatch.setattr(planner_agent, "create_planner_decider", _decider_returning(decision, seen))

    async def _no_pins(*_a: Any, **_k: Any) -> tuple[list, list]:
        return [], []

    async def _search(major_input: Any, *_a: Any, **_k: Any) -> Any:
        seen["search_files"] = [s.item_id for s in major_input.attached_items]
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(orch, "_completion_pins_after_resume", _no_pins)
    monkeypatch.setattr(orch, "_run_deep_search", _search)

    _resume(_pending("deep_search", attached_item_ids=["router-file"]))

    assert seen["decider_files"] == ["router-file", "reply-file"]
    assert seen["search_files"] == ["router-file", "reply-file"]
    assert state["loads"] == [["router-file", "reply-file"], ["router-file", "reply-file"]]


def test_an_old_deep_search_pause_resumes_as_before(monkeypatch):
    state = _resume_env(monkeypatch)
    seen: dict = {}
    again = DeferredToolRequests(calls=[ToolCallPart(
        tool_name="ask_user", args={"question": "؟"}, tool_call_id="tc-2",
    )])
    monkeypatch.setattr(planner_agent, "create_planner_decider", _decider_returning(again, seen))

    _resume(_pending("deep_search"))  # no attached_item_ids key

    assert state["loads"] == [["reply-file"]]
    assert seen["decider_files"] == ["reply-file"]


def test_writing_resume_keeps_the_routers_files_and_chains_them(monkeypatch):
    state = _resume_env(monkeypatch)
    seen: dict = {}

    async def _writer(major_input: Any, *_a: Any, **_k: Any) -> Any:
        seen["writer_files"] = [s.item_id for s in major_input.attached_items]
        return SimpleNamespace(
            kind="paused", planner_result=_PlannerResult(), deferred=_Deferred(),
            pause_reason="approve_plan",
        )

    monkeypatch.setattr(orch, "_run_writer", _writer)

    events = _resume(_pending("writing", attached_item_ids=["router-file"]))

    assert state["loads"] == [["router-file", "reply-file"]]
    assert seen["writer_files"] == ["router-file", "reply-file"]
    assert state["recorded"]["attached_item_ids"] == ["router-file", "reply-file"]
    assert any(e.get("type") == "agent_question" for e in events)
