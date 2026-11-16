"""Both planners read the whole conversation; deep_search carries the request and hands back.

Convo 28b15e17 (2026-11-16): the user asked for a short X article on rescuing a
company from bankruptcy, the router offered to search first, the user said «نعم»,
and the search ran on «نعم» alone — the expanders had no topic and swept the
general rules of evidence. Owner, same day: both planners see the full message
history as the router does, the earlier request is carried into the search word
for word, and a request too general to search goes back to the router to explore.

What is pinned here:

- the planners' history is the router's window (every message after the
  compaction cutoff), with the router's pause and hand-back markers;
- ``request_quotes`` reach the search only when found character for character
  in an earlier message, as a transcript ending in the user's message;
- a fresh dispatch hands a general request back, the router then may only talk,
  and the reply is marked so the same request is not handed back twice;
- every path the router did not choose keeps searching.

Sync tests only: CI installs plain pytest, without pytest-asyncio.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import agents.orchestrator as orch
import agents.router.router as rr
from agents.deep_search_v4.planner import PlannerDecision
from agents.deep_search_v4.planner.agent import (
    PLANNER_DECIDER_LIMITS,
    _QUOTE_RETRIES,
    create_planner_decider,
)
from agents.deep_search_v4.planner.deps import PlannerDeps
from agents.deep_search_v4.planner.prompts import (
    AFTER_HANDBACK_NOTE,
    TRANSCRIPT_ASSISTANT_LABEL,
    TRANSCRIPT_USER_LABEL,
    build_decider_instructions,
    build_decider_user_message,
    compose_request_query,
    locate_request_quotes,
)
from agents.deep_search_v4.planner.runner import handle_planner_turn
from agents.models import ChatMessageSnapshot, ChatResponse, DispatchAgent
from agents.responder.context import HISTORY_LEGEND
from agents.utils.history import HANDBACK_TAG, messages_to_history

CONVO = "conv-A"
USER = "user-A"

REQUEST = "أرغب في كتابة مقالة قانونية قصيرة تنشر عبر اكس عن كيف تنقذ منشأتك من الإفلاس؟"
PROPOSAL = "هل أبدأ بالبحث أولاً؟"
YES = "نعم"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _Query:
    """The supabase chain the history loaders use, over one in-memory table."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = list(rows)
        self.single = False

    def select(self, *_a: Any, **_k: Any) -> "_Query":
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self.rows = [r for r in self.rows if r.get(column) == value]
        return self

    def gt(self, column: str, value: Any) -> "_Query":
        self.rows = [r for r in self.rows if (r.get(column) or "") > value]
        return self

    def in_(self, column: str, values: list[Any]) -> "_Query":
        self.rows = [r for r in self.rows if r.get(column) in values]
        return self

    def is_(self, *_a: Any) -> "_Query":
        return self

    def order(self, column: str, desc: bool = False) -> "_Query":
        self.rows.sort(key=lambda r: r.get(column) or "", reverse=desc)
        return self

    def limit(self, count: int) -> "_Query":
        self.rows = self.rows[:count]
        return self

    def maybe_single(self) -> "_Query":
        self.single = True
        return self

    def execute(self) -> SimpleNamespace:
        if self.single:
            return SimpleNamespace(data=self.rows[0] if self.rows else None)
        return SimpleNamespace(data=self.rows)


class _DB:
    def __init__(self, messages: list[dict], cutoff_index: int | None = None) -> None:
        rows = [
            {
                "message_id": f"m{i}",
                "conversation_id": CONVO,
                "created_at": f"2026-11-16T07:41:{i:02d}",
                "artifact_ids": [],
                **row,
            }
            for i, row in enumerate(messages)
        ]
        self.tables = {
            "messages": rows,
            "conversations": [{
                "conversation_id": CONVO,
                "compacted_through_message_id": (
                    f"m{cutoff_index}" if cutoff_index is not None else None
                ),
            }],
            "workspace_items": [],
            "message_attachments": [],
        }

    def table(self, name: str) -> _Query:
        return _Query(self.tables.get(name, []))


def _user(text: str) -> dict:
    return {"role": "user", "content": text, "metadata": {}}


def _assistant(text: str, **metadata: Any) -> dict:
    return {"role": "assistant", "content": text, "metadata": metadata}


def _snap(role: str, content: str, **kw: Any) -> ChatMessageSnapshot:
    return ChatMessageSnapshot(role=role, content=content, created_at="", **kw)


def _convo() -> list[ChatMessageSnapshot]:
    """28b15e17's three bubbles before the second «نعم», plus the «نعم» itself."""
    return [
        _snap("user", REQUEST),
        _snap("assistant", f"مقالة كهذه تكون قوية إذا استندت إلى أحكام فعلية.\n\n{PROPOSAL}"),
        _snap("user", YES),
    ]


def _deps(messages: list[ChatMessageSnapshot] | None = None, **kw: Any) -> PlannerDeps:
    async def _embed(_text: str) -> list[float]:
        return []

    return PlannerDeps(supabase=None, embedding_fn=_embed, recent_messages=messages or [], **kw)


def _decision(**kw: Any) -> PlannerDecision:
    return PlannerDecision(mode="case_led", support=False, rationale="اختبار", **kw)


def _scripted_decider(monkeypatch, *decisions: PlannerDecision) -> list[int]:
    """Install a decider model that emits ``decisions`` in turn (the last repeats)."""
    calls: list[int] = []

    def fn(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out = decisions[min(len(calls), len(decisions) - 1)]
        calls.append(1)
        tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=out.model_dump())])

    monkeypatch.setattr(
        "agents.deep_search_v4.planner.agent.get_agent_model",
        lambda *_a, **_k: FunctionModel(fn),
    )
    return calls


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("agents.router.context.memory_enabled", lambda _u: False)


# --------------------------------------------------------------------------- #
# The planners read the router's window
# --------------------------------------------------------------------------- #


def test_full_history_reaches_past_the_old_five_message_tail():
    db = _DB([_user(f"رسالة {i}") if i % 2 == 0 else _assistant(f"ردّ {i}") for i in range(9)])

    full = orch._load_recent_messages(db, CONVO, user_id=USER, full=True)
    tail = orch._load_recent_messages(db, CONVO, user_id=USER)

    assert [m.content for m in full] == [
        f"رسالة {i}" if i % 2 == 0 else f"ردّ {i}" for i in range(9)
    ]
    assert len(tail) == orch._RECENT_MESSAGES_N


def test_full_history_starts_after_the_compaction_cutoff():
    db = _DB([_user("قديم"), _assistant("قديم أيضاً"), _user("جديد"), _assistant("")], cutoff_index=1)

    full = orch._load_recent_messages(db, CONVO, user_id=USER, full=True)

    # The cutoff and the empty placeholder are both gone.
    assert [m.content for m in full] == ["جديد"]


def test_full_history_carries_the_routers_markers():
    db = _DB([
        _user(REQUEST),
        _assistant("ما محور المقالة؟", planner_handback=True),
        _user("الإجراءات الوقائية"),
        _assistant("هل تقصد التسوية الوقائية؟", kind="agent_question", agent_family="deep_search"),
        _user("نعم"),
    ])

    full = orch._load_recent_messages(db, CONVO, user_id=USER, full=True)

    assert full[1].content.startswith(HANDBACK_TAG)
    assert full[1].planner_handback is True
    assert full[3].content.startswith("〔[نظام] هذا سؤال طرحه متخصصٌ (agent_family=deep_search)")
    assert not any(m.planner_handback for m in full if m is not full[1])


def test_the_short_tail_of_the_other_families_is_unchanged():
    db = _DB([_assistant("سؤال", kind="agent_question", agent_family="deep_search"), _user("جواب")])

    tail = orch._load_recent_messages(db, CONVO, user_id=USER)

    assert tail[0].content == "سؤال"


def test_the_router_and_the_responder_read_the_handback_marker():
    history = messages_to_history([_assistant("ما محور المقالة؟", planner_handback=True)])

    assert history[0].parts[0].content.startswith(HANDBACK_TAG)
    assert "أعاد مخطّطُ البحث هذا الطلبَ دون" in HISTORY_LEGEND


def test_last_reply_was_handback_reads_the_reply_being_answered():
    assert orch._last_reply_was_handback([
        _snap("assistant", "x", planner_handback=True), _snap("user", "الأولى"),
    ])
    assert not orch._last_reply_was_handback([
        _snap("assistant", "x", planner_handback=True), _snap("user", "y"),
        _snap("assistant", "ردّ بحث"), _snap("user", "z"),
    ])
    assert not orch._last_reply_was_handback([])


# --------------------------------------------------------------------------- #
# The request travels to the search word for word
# --------------------------------------------------------------------------- #


def test_quotes_compose_a_verbatim_transcript_ending_in_the_message():
    query = compose_request_query([PROPOSAL, REQUEST], _convo(), YES)

    assert query == "\n".join([
        f"{TRANSCRIPT_USER_LABEL} {REQUEST}",
        f"{TRANSCRIPT_ASSISTANT_LABEL} {PROPOSAL}",
        f"{TRANSCRIPT_USER_LABEL} {YES}",
    ])


def test_a_reworded_quote_is_not_found_and_never_reaches_the_search():
    reworded = "مقالة عن إنقاذ المنشآت من الإفلاس"

    found, missing = locate_request_quotes([reworded], _convo())

    assert found == [] and missing == [reworded]
    assert compose_request_query([reworded], _convo(), YES) == YES


def test_the_latest_message_cannot_quote_itself():
    _found, missing = locate_request_quotes([YES], [_snap("user", REQUEST), _snap("user", YES)])

    assert missing == [YES]


def test_whitespace_differences_still_match():
    messages = [_snap("user", "كم مدة\n  فترة التجربة؟"), _snap("user", "محدد المدة")]

    query = compose_request_query(["كم مدة فترة التجربة؟"], messages, "محدد المدة")

    assert query.splitlines()[0] == f"{TRANSCRIPT_USER_LABEL} كم مدة فترة التجربة؟"


def test_no_quotes_leaves_the_message_alone():
    assert compose_request_query([], _convo(), YES) == YES


def test_the_decider_is_sent_back_until_its_quote_is_verbatim(monkeypatch):
    calls = _scripted_decider(
        monkeypatch,
        _decision(request_quotes=["مقالة عن إنقاذ المنشآت من الإفلاس"]),
        _decision(request_quotes=[REQUEST]),
    )

    result = asyncio.run(create_planner_decider().run(
        build_decider_user_message(YES), deps=_deps(_convo()), usage_limits=PLANNER_DECIDER_LIMITS,
    ))

    assert len(calls) == 2
    assert result.output.request_quotes == [REQUEST]


def test_a_quote_never_fixed_is_dropped_not_the_turn(monkeypatch):
    calls = _scripted_decider(monkeypatch, _decision(request_quotes=["كلام من عندي"]))

    result = asyncio.run(create_planner_decider().run(
        build_decider_user_message(YES), deps=_deps(_convo()), usage_limits=PLANNER_DECIDER_LIMITS,
    ))

    assert len(calls) == _QUOTE_RETRIES + 1
    assert result.output.request_quotes == []


# --------------------------------------------------------------------------- #
# The runner: hand back, or search on the carried request
# --------------------------------------------------------------------------- #


class _Retrieval:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def __call__(self, query: str, _config: Any, _deps: Any) -> Any:
        self.queries.append(query)
        raise RuntimeError("stop after the query is known")


def test_a_general_request_goes_back_without_a_search(monkeypatch):
    _scripted_decider(monkeypatch, _decision(aborted=True, handback_note="مقالة عامة بلا محور."))
    retrieval = _Retrieval()
    deps = _deps(_convo(), handback_allowed=True)

    turn = asyncio.run(handle_planner_turn(REQUEST, deps, run_retrieval=retrieval))

    assert turn.kind == "handback"
    assert turn.handback_note == "مقالة عامة بلا محور."
    assert retrieval.queries == []


def test_without_the_routers_dispatch_a_handback_still_searches(monkeypatch):
    _scripted_decider(monkeypatch, _decision(aborted=True, handback_note="عام"))
    retrieval = _Retrieval()

    turn = asyncio.run(handle_planner_turn(REQUEST, _deps(_convo()), run_retrieval=retrieval))

    assert turn.kind == "completed"
    assert retrieval.queries == [REQUEST]


def test_the_search_runs_on_the_carried_request(monkeypatch):
    _scripted_decider(monkeypatch, _decision(request_quotes=[REQUEST, PROPOSAL]))
    retrieval = _Retrieval()

    turn = asyncio.run(handle_planner_turn(YES, _deps(_convo()), run_retrieval=retrieval))

    expected = compose_request_query([REQUEST, PROPOSAL], _convo(), YES)
    assert retrieval.queries == [expected]
    assert turn.retrieval_query == expected
    assert REQUEST in expected and expected.endswith(f"{TRANSCRIPT_USER_LABEL} {YES}")


def test_after_a_handback_the_decider_is_told_to_search():
    assert AFTER_HANDBACK_NOTE in build_decider_instructions(_deps(_convo(), after_handback=True))
    assert AFTER_HANDBACK_NOTE not in build_decider_instructions(_deps(_convo()))


# --------------------------------------------------------------------------- #
# The router on the hand-back turn: talk only
# --------------------------------------------------------------------------- #


def _router_ctx(handback: str | None, retry: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        retry=retry, deps=SimpleNamespace(planner_handback=handback, wi_alias_map={}),
    )


def test_the_router_may_not_dispatch_on_the_handback_turn():
    with pytest.raises(ModelRetry):
        rr._validate_and_resolve_dispatch(
            _router_ctx("عام"), DispatchAgent(agent_family="deep_search", task_label="إفلاس"),
        )
    chat = ChatResponse(message="ما الزاوية؟")
    assert rr._validate_and_resolve_dispatch(_router_ctx("عام"), chat) is chat


def test_a_router_that_keeps_dispatching_asks_instead_of_failing():
    out = rr._validate_and_resolve_dispatch(
        _router_ctx("عام", retry=rr._HANDBACK_RETRIES),
        DispatchAgent(agent_family="deep_search", task_label="إفلاس"),
    )
    assert out == ChatResponse(message=rr.HANDBACK_FALLBACK_AR)


def test_the_write_tools_are_hidden_on_the_handback_turn_only():
    from agents.tool_repository.handback_guard import hide_on_planner_handback

    tool = object()
    assert asyncio.run(hide_on_planner_handback(_router_ctx("عام"), tool)) is None
    assert asyncio.run(hide_on_planner_handback(_router_ctx(None), tool)) is tool


def test_any_other_turn_dispatches_as_before():
    out = rr._validate_and_resolve_dispatch(
        _router_ctx(None), DispatchAgent(agent_family="deep_search", task_label="إفلاس"),
    )
    assert out.agent_family == "deep_search"


def test_the_handback_instruction_shows_only_on_that_turn():
    assert rr.inject_planner_handback(_router_ctx(None)) == ""
    text = rr.inject_planner_handback(_router_ctx("مقالة عامة بلا محور."))
    assert "مقالة عامة بلا محور." in text and "ChatResponse" in text


# --------------------------------------------------------------------------- #
# The orchestrator: close the run, let the router talk, mark the reply
# --------------------------------------------------------------------------- #


class _RouterSpy:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    async def __call__(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return rr.RouterRunResult(output=ChatResponse(message="ما الزاوية التي تريدها؟"))


def _router_context() -> SimpleNamespace:
    return SimpleNamespace(
        case_memory_md=None, case_metadata=None, user_preferences=None,
        user_call_name=None, message_history=[], workspace_item_summaries=[],
        compaction_summary_md=None, conversation_memory=[],
    )


@pytest.fixture
def dispatch_env(monkeypatch):
    seen: dict[str, Any] = {}

    async def _no_gate(*_a: Any, **_k: Any) -> None:
        return None

    async def _no_lock(*_a: Any, **_k: Any) -> None:
        return None

    async def _fake_search(major_input: Any, *_a: Any, **_k: Any) -> Any:
        seen["input"] = major_input
        return orch._DeepSearchOutcome(kind="handback", handback_note="مقالة عامة بلا محور.")

    def _fake_recent(*_a: Any, full: bool = False, **_k: Any) -> list:
        seen["full"] = full
        return []

    async def _no_sleep(*_a: Any) -> None:
        return None

    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *_a: 0)
    monkeypatch.setattr(orch, "_check_deep_search_gate", _no_gate)
    monkeypatch.setattr(orch, "analyzer_enabled", lambda _u: False)
    monkeypatch.setattr(orch, "_load_attached_items", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_load_recent_messages", _fake_recent)
    monkeypatch.setattr(orch, "_run_deep_search", _fake_search)
    monkeypatch.setattr(orch.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("shared.workspace.preview_lock.resolve_lock_policy", _no_lock)
    monkeypatch.setattr("agents.utils.welcome.mark_welcomed", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "backend.app.services.masking_service.persist_new_mappings", lambda *_a, **_k: None
    )
    spy = _RouterSpy()
    monkeypatch.setattr("agents.router.router.run_router", spy)
    seen["router"] = spy
    return seen


def _dispatch(allow_handback: bool) -> list[dict]:
    async def _collect() -> list[dict]:
        return [
            ev async for ev in orch._dispatch(
                agent_family="deep_search", describe_query=REQUEST, task_label="إفلاس",
                target_item_id=None, attached_item_ids=[], subtype=None,
                supabase=object(), user_id=USER, conversation_id=CONVO, case_id=None,
                assistant_message_id="am1", router_context=_router_context(),
                allow_handback=allow_handback,
            )
        ]

    return asyncio.run(_collect())


def test_a_handback_closes_the_run_then_the_router_explores(dispatch_env):
    events = _dispatch(allow_handback=True)
    types = [e["type"] for e in events]

    assert dispatch_env["full"] is True
    assert dispatch_env["input"].allow_handback is True
    assert dispatch_env["router"].kwargs["planner_handback"] == "مقالة عامة بلا محور."
    finished = types.index("agent_run_finished")
    assert finished < types.index("reply_metadata") < types.index("token")
    meta = events[types.index("reply_metadata")]["metadata"]
    assert meta["planner_handback"] is True
    assert "".join(e["text"] for e in events if e["type"] == "token") == "ما الزاوية التي تريدها؟"
    assert types[-1] == "done"
    # No "done" stage: the client must not seal a search chip on this reply.
    assert not any(e.get("stage") == "done" for e in events if e["type"] == "agent_progress")


def test_only_the_routers_dispatch_lets_the_planner_hand_back(dispatch_env):
    _dispatch(allow_handback=False)

    assert dispatch_env["input"].allow_handback is False


# --------------------------------------------------------------------------- #
# simple_search → deep_search: a planner revert answers, never crashes
# (convo 4ea4e212, 2026-11-16: «حدد مكان هذه الافادة من المدعي» ended on
# «عذراً، حدث خطأ أثناء تنفيذ المهمة» after 5m42s)
# --------------------------------------------------------------------------- #


_CRASH_TEXT = "عذراً، حدث خطأ"


class _PriorCheckSpy:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def __call__(self, **kwargs: Any):
        self.kwargs = kwargs

        async def _gen():
            yield {"type": "token", "text": "الموضع في ضبط الجلسة"}
            yield {"type": "done", "usage": {}}

        return _gen()


@pytest.fixture
def revert_env(monkeypatch):
    async def _no_gate(*_a: Any, **_k: Any) -> None:
        return None

    async def _reverted(*_a: Any, **_k: Any) -> Any:
        return orch._DeepSearchOutcome(kind="reverted")

    async def _ss_gives_up(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("searched the library 9 times, found nothing")

    async def _settled(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(orch, "_check_deep_search_gate", _no_gate)
    monkeypatch.setattr(orch, "analyzer_enabled", lambda _u: True)
    monkeypatch.setattr(orch, "_load_attached_items", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_load_recent_messages", lambda *_a, **_k: [])
    monkeypatch.setattr(orch, "_load_compaction_summary", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "_load_conversation_memory", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "_settle_memory_writer", _settled)
    monkeypatch.setattr(orch, "_note_simple_search_handoff", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "_resolve_pause_loud", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "resolve_pause", lambda *_a, **_k: True)
    monkeypatch.setattr(orch, "_run_deep_search", _reverted)
    monkeypatch.setattr(orch, "run_simple_search", _ss_gives_up)
    monkeypatch.setattr("agents.simple_search.runner.resume_simple_search", _ss_gives_up)
    spy = _PriorCheckSpy()
    monkeypatch.setattr(orch, "_run_prior_check", spy)
    return spy


def _texts(events: list[dict]) -> str:
    return "".join(e.get("text", "") for e in events if e.get("type") == "token")


def test_fresh_simple_search_fallback_answers_from_what_we_hold(revert_env):
    async def _collect() -> list[dict]:
        return [
            ev async for ev in orch._dispatch(
                agent_family="simple_search", describe_query="حدد مكان هذه الافادة",
                task_label="موضع إفادة المدعي", target_item_id=None,
                attached_item_ids=["wi-9"], subtype=None, supabase=object(),
                user_id=USER, conversation_id=CONVO, case_id=None,
                assistant_message_id="am1", router_context=_router_context(),
            )
        ]

    events = asyncio.run(_collect())
    types = [e["type"] for e in events]

    assert _CRASH_TEXT not in _texts(events)
    assert revert_env.kwargs is not None and revert_env.kwargs["after_revert"] is True
    assert revert_env.kwargs["attached_item_ids"] == ["wi-9"]
    assert types.index("agent_run_finished") < types.index("token")


def test_resumed_simple_search_fallback_answers_from_what_we_hold(revert_env):
    async def _collect() -> list[dict]:
        return [
            ev async for ev in orch._resume_simple_search_leg(
                pending={"run_id": "run-ss-1", "task_label": "موضع إفادة المدعي"},
                user_reply="في مذكرة المدعي", supabase=object(), user_id=USER,
                conversation_id=CONVO, case_id=None, user_message_id="um1",
                assistant_message_id="am1",
            )
        ]

    events = asyncio.run(_collect())
    types = [e["type"] for e in events]

    assert _CRASH_TEXT not in _texts(events)
    assert revert_env.kwargs is not None and revert_env.kwargs["question"] == "في مذكرة المدعي"
    assert types.index("agent_run_finished") < types.index("token")
