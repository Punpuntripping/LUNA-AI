"""The deep_search planner hands a paused turn back the way the writer planner does.

When the reply to a pause is a different request the decider emits ``aborted`` and
the router takes the turn. Two things must hold for that to look right to the user:
the run is closed before the router opens its own, and no deep_search tracker was
ever started, because the client would seal a "deep search" chip with no search
behind it onto whatever the router answers.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    UserPromptPart,
)

import agents.orchestrator as orch
from agents.deep_search_v4.planner import PlannerDecision

REPLY = "اكتب لي خطاب إنذار للمالك"


def _pause_row() -> dict:
    history = ModelMessagesTypeAdapter.dump_json(
        [ModelRequest(parts=[UserPromptPart(content="<query>سؤال طويل</query>")])]
    )
    return {
        "run_id": "run-ds-1",
        "agent_family": "deep_search",
        "task_label": "نزاع إيجار",
        "message_history": "\\x" + history.hex(),
        "deferred_payload": {"tool_call_id": "call_abc", "tool_name": "ask_user", "args": {}},
        "question_text": "هل فهمت وضعك؟",
    }


class _RouteSpy:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def __call__(self, **kwargs: Any):
        self.kwargs = kwargs

        async def _gen():
            yield {"type": "token", "text": "ROUTED"}

        return _gen()


class _Decider:
    """Stands in for the resumed planner_decider."""

    def __init__(self, output: Any = None, raises: Exception | None = None) -> None:
        self.output = output
        self.raises = raises

    async def run(self, *_args: Any, **_kwargs: Any) -> Any:
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(output=self.output)


@pytest.fixture
def route_spy(monkeypatch) -> _RouteSpy:
    """Stub the resume leg's reads and writes; return the router spy."""
    for name, value in {
        "_load_prior_search_summaries": [],
        "_load_attached_items": [],
        "_load_reply_attachment_ids": [],
        "_load_recent_messages": [],
        "_load_compaction_summary": None,
    }.items():
        monkeypatch.setattr(orch, name, lambda *a, _v=value, **k: _v)
    monkeypatch.setattr(orch, "_encode_attached_for_planner", lambda items: items)
    monkeypatch.setattr(orch, "_feed_resume_decider", lambda *a, **k: None)
    monkeypatch.setattr(orch, "resolve_pause", lambda *a, **k: True)
    monkeypatch.setattr(
        "shared.config.get_settings", lambda: SimpleNamespace(JINA_RERANKER_API_KEY="")
    )
    monkeypatch.setattr(
        "backend.app.services.masking_service.persist_new_mappings", lambda *a, **k: None
    )
    spy = _RouteSpy()
    monkeypatch.setattr(orch, "_route", spy)
    return spy


def _use_decider(monkeypatch, decider: _Decider) -> None:
    monkeypatch.setattr(
        "agents.deep_search_v4.planner.agent.create_planner_decider", lambda: decider
    )


async def _resume() -> list[dict]:
    return [
        ev
        async for ev in orch._resume_major_agent_inner(
            pending=_pause_row(),
            user_reply=REPLY,
            supabase=object(),
            user_id="u1",
            conversation_id="c1",
            case_id=None,
            user_message_id=None,
            assistant_message_id="am1",
        )
    ]


def _assert_clean_handback(events: list[dict]) -> None:
    types = [e.get("type") for e in events]
    assert "agent_progress" not in types, "a hand-back must not start a search tracker"
    finished = types.index("agent_run_finished")
    assert events[finished]["agent_family"] == "deep_search"
    assert finished < types.index("token"), "close the run before the router speaks"


@pytest.mark.asyncio
async def test_a_different_request_goes_to_the_router(monkeypatch, route_spy):
    _use_decider(
        monkeypatch,
        _Decider(PlannerDecision(mode="reg_compliance_led", rationale="طلب كتابة", aborted=True)),
    )

    await _resume()

    assert route_spy.kwargs is not None
    assert route_spy.kwargs["question"] == REPLY
    assert route_spy.kwargs["assistant_message_id"] == "am1"


@pytest.mark.asyncio
async def test_a_handback_closes_the_run_and_opens_no_tracker(monkeypatch, route_spy):
    _use_decider(
        monkeypatch,
        _Decider(PlannerDecision(mode="reg_compliance_led", rationale="طلب كتابة", aborted=True)),
    )

    _assert_clean_handback(await _resume())


@pytest.mark.asyncio
async def test_a_failed_resume_leaves_the_same_way(monkeypatch, route_spy):
    _use_decider(monkeypatch, _Decider(raises=RuntimeError("provider down")))

    _assert_clean_handback(await _resume())
    assert route_spy.kwargs is not None


@pytest.mark.asyncio
async def test_an_answer_still_shows_the_tracker(monkeypatch, route_spy):
    _use_decider(
        monkeypatch,
        _Decider(PlannerDecision(mode="reg_compliance_led", rationale="أجاب المستخدم")),
    )

    async def _no_search(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("retrieval is not under test")

    monkeypatch.setattr(orch, "_run_deep_search", _no_search)

    events = await _resume()

    assert route_spy.kwargs is None
    stages = [e["stage"] for e in events if e.get("type") == "agent_progress"]
    assert stages[0] == "planning"
