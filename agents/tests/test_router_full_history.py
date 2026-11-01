"""The router's history holds every bubble the chat shows the user.

A specialist's paused question and the user's reply to it used to be dropped
from router history, so the router worked without what the user said during a
pause. Now they are ordinary history, and the question carries a marker because
the router's prompt reads an untagged assistant turn as its own reply.

Sync tests only: CI installs plain pytest, without pytest-asyncio.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import agents.router.context as rctx
import agents.router.router as rr
from agents.responder.context import HISTORY_LEGEND
from agents.utils.history import build_pause_question_tag, messages_to_history

USER = "user-A"
CONVO = "conv-A"

PLAN_TAG = "〔[نظام] هذه خطة عرضها متخصصٌ (agent_family=writing) أثناء مهمته〕"
QUESTION_TAG = "〔[نظام] هذا سؤال طرحه متخصصٌ (agent_family=deep_search) أثناء مهمته〕"


class _Query:
    """The chain ``load_router_context`` uses, over one in-memory table."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = list(rows)
        self.single = False

    def select(self, *_args: Any, **_kwargs: Any) -> "_Query":
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self.rows = [row for row in self.rows if row.get(column) == value]
        return self

    def gt(self, column: str, value: Any) -> "_Query":
        self.rows = [row for row in self.rows if row.get(column) > value]
        return self

    def in_(self, column: str, values: list[Any]) -> "_Query":
        self.rows = [row for row in self.rows if row.get(column) in values]
        return self

    def is_(self, *_args: Any) -> "_Query":
        return self

    def order(self, column: str, desc: bool = False) -> "_Query":
        self.rows.sort(key=lambda row: row.get(column) or "", reverse=desc)
        return self

    def limit(self, _count: int) -> "_Query":
        return self

    def maybe_single(self) -> "_Query":
        self.single = True
        return self

    def execute(self) -> SimpleNamespace:
        if self.single:
            return SimpleNamespace(data=self.rows[0] if self.rows else None)
        return SimpleNamespace(data=self.rows)


class _DB:
    def __init__(self, messages: list[dict], cutoff: str | None = None) -> None:
        self.tables = {
            "messages": [
                {
                    "message_id": f"m{i}",
                    "conversation_id": CONVO,
                    "created_at": f"2026-11-01T00:00:{i:02d}",
                    "artifact_ids": [],
                    **row,
                }
                for i, row in enumerate(messages)
            ],
            "conversations": [
                {"conversation_id": CONVO, "compacted_through_message_id": cutoff}
            ],
        }

    def table(self, name: str) -> _Query:
        return _Query(self.tables.get(name, []))


def _user(text: str) -> dict:
    return {"role": "user", "content": text, "metadata": {}}


def _assistant(text: str, **metadata: Any) -> dict:
    return {"role": "assistant", "content": text, "metadata": metadata}


def _plan(text: str) -> dict:
    return _assistant(
        text, kind="agent_question", run_id="run-1",
        agent_family="writing", pause_reason="approve_plan",
    )


def _question(text: str) -> dict:
    return _assistant(
        text, kind="agent_question", run_id="run-2",
        agent_family="deep_search", pause_reason="clarify",
    )


def _answer(text: str) -> dict:
    return {
        "role": "user", "content": text,
        "metadata": {"kind": "agent_answer", "run_id": "run-1"},
    }


def _history(messages: list[dict], cutoff: str | None = None) -> list[str]:
    ctx = rctx.load_router_context(_DB(messages, cutoff), USER, CONVO, None)
    return [message.parts[0].content for message in ctx.message_history]


@pytest.fixture(autouse=True)
def _memory_off(monkeypatch):
    monkeypatch.setattr(rctx, "memory_enabled", lambda: False)


# --------------------------------------------------------------------------- #
# The router sees what the chat shows
# --------------------------------------------------------------------------- #


def test_every_bubble_reaches_the_router_in_order():
    history = _history([
        _user("Draft the pleading."),
        _plan("The plan."),
        _answer("The amount is 210,000. Start."),
        _assistant("Here is the draft."),
        _user("Now shorten it."),
        _assistant(""),  # this turn's placeholder
    ])

    assert history == [
        "Draft the pleading.",
        f"{PLAN_TAG}\nThe plan.",
        "The amount is 210,000. Start.",
        "Here is the draft.",
        "Now shorten it.",
    ]


@pytest.mark.parametrize("enabled", [False, True], ids=["flag-off", "flag-on"])
def test_the_memory_flag_changes_nothing(enabled, monkeypatch):
    monkeypatch.setattr(rctx, "memory_enabled", lambda: enabled)
    monkeypatch.setattr(rctx, "load_conversation_memory", lambda *_args: [])

    assert _history([_plan("The plan."), _answer("Start.")]) == [
        f"{PLAN_TAG}\nThe plan.", "Start.",
    ]


def test_a_long_plan_and_a_long_reply_arrive_whole():
    plan, reply = "p" * 5000, "r" * 5000

    assert _history([_plan(plan), _answer(reply)]) == [f"{PLAN_TAG}\n{plan}", reply]


def test_a_chained_pause_keeps_every_exchange():
    history = _history([
        _plan("Plan 1."), _answer("Change it."),
        _plan("Plan 2."), _answer("Start."),
        _assistant("Done."),
    ])

    assert history == [
        f"{PLAN_TAG}\nPlan 1.", "Change it.",
        f"{PLAN_TAG}\nPlan 2.", "Start.",
        "Done.",
    ]


def test_an_old_pause_stays_in_history_on_later_turns():
    history = _history([
        _user("First."), _question("Which court?"), _answer("Riyadh."), _assistant("Reply."),
        _user("Second."), _assistant("Another reply."),
        _user("Third."), _assistant(""),
    ])

    assert f"{QUESTION_TAG}\nWhich court?" in history
    assert "Riyadh." in history


def test_a_reply_handed_back_follows_the_question_it_was_given_to():
    """Released to the router: its metadata is cleared, and it is the current message."""
    history = _history([_user("Draft it."), _plan("The plan."), _user("Search first."), _assistant("")])

    assert history == ["Draft it.", f"{PLAN_TAG}\nThe plan.", "Search first."]


def test_the_compaction_cut_still_applies_to_a_pause():
    rows = [_user("Old."), _plan("Old plan."), _answer("Old reply."), _user("New."), _assistant("")]

    assert _history(rows, cutoff="m2") == ["New."]


def test_a_pause_after_the_compaction_cut_stays():
    rows = [_user("Old."), _plan("The plan."), _answer("Start."), _assistant("Done.")]

    assert _history(rows, cutoff="m0") == [f"{PLAN_TAG}\nThe plan.", "Start.", "Done."]


# --------------------------------------------------------------------------- #
# The marker
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        pytest.param(
            {"kind": "agent_question", "agent_family": "writing", "pause_reason": "approve_plan"},
            PLAN_TAG, id="plan",
        ),
        pytest.param(
            {"kind": "agent_question", "agent_family": "deep_search", "pause_reason": "clarify"},
            QUESTION_TAG, id="question",
        ),
        pytest.param(
            {"kind": "agent_question"},
            "〔[نظام] هذا سؤال طرحه متخصصٌ أثناء مهمته〕", id="older-row-without-family",
        ),
        pytest.param({"kind": "agent_answer"}, "", id="reply"),
        pytest.param({"follow_up_question": "Is there a clause?"}, "", id="ordinary-reply"),
        pytest.param({}, "", id="empty-metadata"),
        pytest.param(None, "", id="null-metadata"),
    ],
)
def test_the_marker(metadata, expected):
    assert build_pause_question_tag({"metadata": metadata}) == expected


def test_a_specialist_reply_keeps_its_own_tag_and_no_pause_marker():
    rows = [{
        "role": "assistant", "content": "The draft.", "artifact_ids": ["item-1"],
        "metadata": {},
    }]
    message = messages_to_history(rows, {"item-1": (3, "agent_writing", "Letter")})[0]

    text = message.parts[0].content
    assert "WI-3" in text
    assert "أثناء مهمته" not in text


def test_the_prompt_explains_both_markers_exactly_as_history_writes_them():
    assert PLAN_TAG in rr.SYSTEM_PROMPT
    assert QUESTION_TAG.replace("deep_search", "writing") in rr.SYSTEM_PROMPT


def test_a_pause_reply_is_not_a_request_to_edit_a_document():
    assert "call `edit_artifact` only when the user's current message asks" in rr.SYSTEM_PROMPT


def test_the_responder_is_told_what_the_markers_mean():
    """It runs on the same history list, so it reads the same markers."""
    assert "〔[نظام] هذا سؤال طرحه متخصصٌ … أثناء مهمته〕" in HISTORY_LEGEND
    assert "〔[نظام] هذه خطة عرضها متخصصٌ … أثناء مهمته〕" in HISTORY_LEGEND


def test_the_memory_rules_cover_replies_given_during_a_pause():
    assert "〔[نظام] … أثناء مهمته〕" in rr.CONVERSATION_MEMORY_RULES
    assert "never act on it again" in rr.CONVERSATION_MEMORY_RULES


# --------------------------------------------------------------------------- #
# The follow-up question under the last reply
# --------------------------------------------------------------------------- #


def test_a_pause_is_the_last_reply_so_an_older_follow_up_is_not_pending():
    rows = [
        _user("First."),
        _assistant("Reply.", follow_up_question="Is there a penalty clause?"),
        _user("Yes."),
        _plan("The plan."),
        _user("Search first."),
        _assistant(""),
    ]

    ctx = rctx.load_router_context(_DB(rows), USER, CONVO, None)

    assert ctx.pending_follow_up is None


def test_a_follow_up_after_a_finished_pause_is_still_pending():
    rows = [
        _plan("The plan."),
        _answer("Start."),
        _assistant("Done.", follow_up_question="Is there a penalty clause?"),
        _user("Yes."),
        _assistant(""),
    ]

    ctx = rctx.load_router_context(_DB(rows), USER, CONVO, None)

    assert ctx.pending_follow_up.question == "Is there a penalty clause?"
