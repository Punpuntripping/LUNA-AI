"""Conversation memory: the router, the orchestrator and the editor.

Plans ``conversation_memory.md`` §4 and ``conversation_memory_agent.md`` §4, §7.

Offline: the DB is a small filter-evaluating fake, models are FunctionModel
scripts, masking uses a real ``PrivacyCodec``. No LLM, no network. Sync tests with
``asyncio.run``, because CI has no pytest-asyncio.

The memory agent's model step is scripted (``_MemoryAgent``). Its task, ledger scope
and write are the real ones, and turns go through ``_handle_message_inner``, so
these tests see what a turn sees.
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import importlib.util
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import agents.artifact_editor
import agents.artifact_editor.agent as editor_mod
import agents.deep_search_v4.planner as planner_pkg
import agents.deep_search_v4.planner.agent as planner_agent_mod
import agents.memory.convo_memory_writer.runner as writer_runner
import agents.orchestrator as orch
import agents.router.context as rctx
import agents.router.router as rr
import agents.simple_search.runner as ss_runner
import agents.utils.welcome as welcome_mod
import backend.app.services.conversation_service as conversation_service
import backend.app.services.masking_service as masking
import agents.memory.convo_memory_writer.agent as writer_agent
from agents.artifact_editor.agent import EditorDeps, EditorResult, inject_artifact
from agents.memory.convo_memory_writer.models import MemoryAddOp, MemoryOps
from agents.models import ChatResponse, DispatchAgent, MajorAgentInput, SpecialistResult
from agents.responder.agent import ResponderDeps
from agents.utils.conversation_memory import (
    MemoryEntry,
    render_conversation_memory,
    render_router_memory,
)
from agents.utils.tracking import _full_snapshot

TABLE = "conversation_memory_entries"
USER = "user-A"
CONVO = "conv-A"

REAL_ID = "1098765432"
FAKE_ID = "1098111222"

AMOUNT = "The correct amount is 210,000; 310,000 is a misreading."

RUN_STAGE = "convo_memory_writer.run"

#: ``seen_route`` quiets the window. A test that needs it puts this one back.
_REAL_RECENT_MESSAGES = orch._load_recent_messages


# --------------------------------------------------------------------------- #
# Fake DB: real filters over in-memory tables, and a record of every query.
# --------------------------------------------------------------------------- #


class _Query:
    def __init__(self, db: "FakeDB", table: str) -> None:
        self.db = db
        self.table = table
        self.op = "select"
        self.payload: Any = None
        self.tests: list = []
        self.filters: list[tuple[str, Any]] = []
        self.order_by: tuple[str, bool] | None = None
        self.row_limit: int | None = None
        self.single = False

    def select(self, *_a: Any, **_k: Any) -> "_Query":
        return self

    def insert(self, payload: Any) -> "_Query":
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload: dict) -> "_Query":
        self.op, self.payload = "update", payload
        return self

    def delete(self) -> "_Query":
        self.op = "delete"
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self.filters.append((column, value))
        self.tests.append(lambda row: row.get(column) == value)
        return self

    def neq(self, column: str, value: Any) -> "_Query":
        self.tests.append(lambda row: row.get(column) != value)
        return self

    def is_(self, column: str, _value: Any) -> "_Query":
        self.tests.append(lambda row: row.get(column) is None)
        return self

    def in_(self, column: str, values: list) -> "_Query":
        self.tests.append(lambda row: row.get(column) in values)
        return self

    def gt(self, column: str, value: Any) -> "_Query":
        self.tests.append(lambda row: row.get(column) > value)
        return self

    def order(self, column: str, desc: bool = False) -> "_Query":
        self.order_by = (column, desc)
        return self

    def limit(self, count: int) -> "_Query":
        self.row_limit = count
        return self

    def maybe_single(self) -> "_Query":
        self.single = True
        return self

    def execute(self) -> SimpleNamespace:
        return self.db.run(self)


class FakeDB:
    """``fail[table]`` raises on every query of that table."""

    def __init__(self, **tables: list[dict]) -> None:
        self.tables = {name: [dict(row) for row in rows] for name, rows in tables.items()}
        # The conversation is live: the memory agent checks before it writes.
        self.tables.setdefault(
            "conversations",
            [{"conversation_id": CONVO, "user_id": USER, "deleted_at": None}],
        )
        self.queries: list[_Query] = []
        self.fail: dict[str, Exception] = {}

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def count(self, table: str, op: str = "select") -> int:
        return sum(1 for q in self.queries if q.table == table and q.op == op)

    def contents(self) -> list[str]:
        return [row["content"] for row in self.tables.get(TABLE, [])]

    def run(self, query: _Query) -> SimpleNamespace:
        self.queries.append(query)
        if query.table in self.fail:
            raise self.fail[query.table]
        rows = self.tables.setdefault(query.table, [])
        if query.op == "insert":
            payload = query.payload if isinstance(query.payload, list) else [query.payload]
            rows.extend(dict(row) for row in payload)
            return SimpleNamespace(data=[dict(row) for row in payload])
        matched = [row for row in rows if all(test(row) for test in query.tests)]
        if query.op == "update":
            for row in matched:
                row.update(query.payload)
        if query.op == "delete":
            self.tables[query.table] = [
                row for row in rows if not any(row is m for m in matched)
            ]
        if query.order_by:
            column, desc = query.order_by
            matched = sorted(matched, key=lambda row: row[column], reverse=desc)
        if query.row_limit is not None:
            matched = matched[: query.row_limit]
        data = [dict(row) for row in matched]
        if query.single:
            return SimpleNamespace(data=data[0] if data else None)
        return SimpleNamespace(data=data)


def _row(seq=1, kind="fact", content=AMOUNT, insisted=False, user=USER, convo=CONVO) -> dict:
    return {
        "conversation_id": convo, "user_id": user, "seq": seq,
        "kind": kind, "content": content, "insisted": insisted,
    }


def _entry(seq=1, content=AMOUNT) -> MemoryEntry:
    return MemoryEntry(seq, "fact", content)


@pytest.fixture(autouse=True)
def no_queue_left_over(monkeypatch):
    """Every turn starts a run now, so none may stay queued for the next test."""
    monkeypatch.setattr(writer_runner, "_LATEST", {})


@pytest.fixture
def flag_on(monkeypatch):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "CONVERSATION_MEMORY_ENABLED", True)


@pytest.fixture
def flag_off(monkeypatch):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "CONVERSATION_MEMORY_ENABLED", False)


@pytest.fixture
def allowlist(monkeypatch):
    from shared.config import get_settings

    def _set(user_ids: str) -> None:
        monkeypatch.setattr(get_settings(), "CONVERSATION_MEMORY_USER_IDS", user_ids)

    return _set


@pytest.fixture
def codec():
    from shared.privacy import PrivacyCodec

    active = PrivacyCodec(
        real_to_fake={REAL_ID: FAKE_ID}, fake_to_real={FAKE_ID: REAL_ID}
    )
    token = masking.set_active_codec(active)
    yield active
    masking.reset_active_codec(token)


# --------------------------------------------------------------------------- #
# Scripted router runs
# --------------------------------------------------------------------------- #


def _chat(message: str = "Noted, I will use the corrected amount.") -> ToolCallPart:
    return ToolCallPart(
        tool_name="final_result_ChatResponse",
        args={"type": "chat", "message": message},
    )


def _dispatch(family: str, target_wi: str | None = None) -> ToolCallPart:
    return ToolCallPart(
        tool_name="final_result_DispatchAgent",
        args={
            "type": "dispatch", "agent_family": family,
            "task_label": "A subject long enough for the label",
            "target_wi": target_wi, "attached_wis": [], "subtype": None,
        },
    )


def _response(*parts: Any) -> ModelResponse:
    return ModelResponse(parts=list(parts))


class _Script:
    """A FunctionModel body: one scripted response per request, recording each."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.infos: list[AgentInfo] = []
        self.messages: list[list] = []

    def respond(self, messages: list, info: AgentInfo) -> ModelResponse:
        self.infos.append(info)
        self.messages.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def tool_returns(self) -> list[str]:
        return [
            str(part.content)
            for part in self.messages[-1][-1].parts
            if isinstance(part, ToolReturnPart)
        ]


def _run_router(db: FakeDB, script: _Script, **kwargs: Any) -> Any:
    async def _go() -> Any:
        with rr.router_agent.override(model=FunctionModel(script.respond)):
            return await rr.run_router(
                question="The amount is 210,000.",
                supabase=db,
                user_id=USER,
                conversation_id=CONVO,
                case_id=None,
                case_memory_md=None,
                case_metadata=None,
                user_preferences=None,
                message_history=[],
                **kwargs,
            )

    return asyncio.run(_go())


def _deps(**over: Any) -> rr.RouterDeps:
    base: dict[str, Any] = dict(
        supabase=None, user_id=USER, conversation_id=CONVO, case_id=None,
        case_memory_md=None, case_metadata=None, user_preferences=None,
    )
    base.update(over)
    return rr.RouterDeps(**base)


# --------------------------------------------------------------------------- #
# The router saves nothing
# --------------------------------------------------------------------------- #


_ROUTER_TOOLS = {
    "edit_artifact", "list_workspace_items", "open_rayhan_guide", "open_rayhan_page",
    "save_memo", "unfold_workspace_item",
}


def _tool_names(script: _Script) -> set[str]:
    return {tool.name for tool in script.infos[0].function_tools}


def test_the_router_has_no_memory_tool(flag_on):
    script = _Script(_response(_chat()))
    _run_router(FakeDB(), script)

    assert _tool_names(script) == _ROUTER_TOOLS


def test_the_tool_module_is_gone():
    assert importlib.util.find_spec("agents.tool_repository.remember") is None


def test_the_deps_and_the_result_carry_no_notes():
    assert not hasattr(_deps(), "memory_notes")
    assert "memory_notes" not in {field.name for field in dataclasses.fields(rr.RouterRunResult)}


def test_the_tool_limit_is_nine_again():
    assert rr.ROUTER_LIMITS.tool_calls_limit == 9


def test_a_ninth_tool_call_in_a_turn_keeps_the_answer(flag_on):
    calls = [ToolCallPart(tool_name="list_workspace_items", args={}) for _ in range(9)]
    result = _run_router(FakeDB(), _Script(_response(*calls, _chat("The answer."))))

    assert result.output.message == "The answer."


def test_exhaustive_stays_for_save_memo():
    assert rr.router_agent.end_strategy == "exhaustive"


# --------------------------------------------------------------------------- #
# Router prompt and result
# --------------------------------------------------------------------------- #


def test_the_static_prompt_has_no_memory_route_section_and_no_memory_rules():
    assert "When to route to memory" not in rr.SYSTEM_PROMPT
    assert "## Questions about ريحان itself" in rr.SYSTEM_PROMPT
    assert "`remember`" not in rr.SYSTEM_PROMPT


def test_the_memory_family_stays_in_the_code():
    assert "memory" in DispatchAgent.model_fields["agent_family"].annotation.__args__
    assert callable(orch._run_memory)


def test_with_entries_the_instruction_holds_them_in_the_short_form(flag_on):
    text = rr.inject_conversation_memory(SimpleNamespace(deps=_deps(conversation_memory=[_entry()])))

    assert render_router_memory([_entry()]) in text
    assert AMOUNT in text
    assert "M-1" not in text
    assert "This is the conversation memory." not in text
    assert render_conversation_memory([_entry()]) not in text


def test_with_no_entries_the_rules_still_show(flag_on):
    """The memory agent runs beside the router, so a first correction has no entry yet."""
    text = rr.inject_conversation_memory(SimpleNamespace(deps=_deps()))

    assert render_router_memory([]) in text
    assert "When the user disputes a value from a document, their value stands." in text
    assert "<entry" not in text


def test_the_block_is_rendered_once_with_no_second_escape(flag_on):
    entry = _entry(1, "a < b & c")
    text = rr.inject_conversation_memory(SimpleNamespace(deps=_deps(conversation_memory=[entry])))

    assert "a &lt; b &amp; c" in text
    assert "&amp;lt;" not in text


@pytest.fixture
def span_attrs(monkeypatch):
    """Everything the router puts on its ``router.classify`` span."""
    seen: dict[str, Any] = {}

    class _Span:
        def __enter__(self) -> "_Span":
            return self

        def __exit__(self, *_a: Any) -> bool:
            return False

        def record_run(self, *_a: Any, **_k: Any) -> None:
            pass

        def set(self, **attrs: Any) -> None:
            seen.update(attrs)

        def set_outcome(self, *_a: Any) -> None:
            pass

    def _track(_stage: str, **attrs: Any) -> _Span:
        seen.update(attrs)
        return _Span()

    monkeypatch.setattr(rr, "track_stage", _track)
    return seen


def test_span_carries_the_count_and_no_content(flag_on, span_attrs, caplog):
    db = FakeDB(**{TABLE: [_row(1, content="SECRET-A"), _row(2, content="SECRET-B")]})
    with caplog.at_level(logging.INFO):
        _run_router(
            db, _Script(_response(_chat())),
            conversation_memory=[_entry(1, "SECRET-A"), _entry(2, "SECRET-B")],
        )

    assert span_attrs["memory_entry_count"] == 2
    assert "memory_note_count" not in span_attrs
    assert "SECRET" not in repr(span_attrs)
    assert "SECRET" not in caplog.text


# --------------------------------------------------------------------------- #
# Flag off, and the allowlist
# --------------------------------------------------------------------------- #


def test_flag_off_adds_no_block(flag_off):
    script = _Script(_response(_chat()))
    _run_router(FakeDB(), script, conversation_memory=[_entry()])

    assert "<conversation_memory>" not in script.infos[0].instructions


def _prompt_without_memory() -> str:
    """The static prompt and today's date, which every user gets: nothing else."""
    return f"{rr.SYSTEM_PROMPT.strip()}\n\n{rr.today_line()}"


def test_a_disabled_user_gets_the_prompt_as_it_was(flag_off):
    """Nothing of the feature reaches the model: no block, no rule, no tool."""
    script = _Script(_response(_chat()))
    _run_router(FakeDB(), script, conversation_memory=[_entry()])

    assert script.infos[0].instructions == _prompt_without_memory()
    assert _tool_names(script) == _ROUTER_TOOLS


def test_a_user_outside_the_allowlist_gets_the_prompt_as_it_was(flag_on, allowlist):
    allowlist("user-B")
    script = _Script(_response(_chat()))
    _run_router(FakeDB(), script, conversation_memory=[_entry()])

    assert script.infos[0].instructions == _prompt_without_memory()


def test_an_enabled_user_with_no_entries_gets_the_rules(flag_on):
    script = _Script(_response(_chat()))
    _run_router(FakeDB(), script)

    assert render_router_memory([]) in script.infos[0].instructions


def test_memory_comes_after_the_static_prefix_and_every_other_block(flag_on):
    script = _Script(_response(_chat()))
    _run_router(
        FakeDB(), script, compaction_summary_md="BACK-STORY", conversation_memory=[_entry()]
    )

    instructions = script.infos[0].instructions
    block = render_router_memory([_entry()])
    assert instructions.startswith(rr.SYSTEM_PROMPT.strip())
    assert instructions.index("BACK-STORY") < instructions.index(block)
    assert instructions.rstrip().endswith(block)


def test_flag_off_instruction_is_empty(flag_off):
    deps = _deps(conversation_memory=[_entry()])

    assert rr.inject_conversation_memory(SimpleNamespace(deps=deps)) == ""


def test_a_user_outside_the_allowlist_gets_no_block_and_no_query(flag_on, allowlist):
    allowlist("user-B, user-C")
    db = _context_db(_PAUSED_ROWS, **{TABLE: [_row()]})
    ctx = rctx.load_router_context(db, USER, CONVO, None)

    assert db.count(TABLE) == 0
    assert ctx.conversation_memory == []
    assert ctx.conversation_memory_md is None

    script = _Script(_response(_chat()))
    _run_router(db, script, conversation_memory=[_entry()])

    assert "<conversation_memory>" not in script.infos[0].instructions
    assert rr.inject_conversation_memory(
        SimpleNamespace(deps=_deps(conversation_memory=[_entry()]))
    ) == ""


def test_a_listed_user_gets_the_rules_and_the_entries(flag_on, allowlist):
    allowlist(f"user-B, {USER}")
    db = _context_db(_PAUSED_ROWS, **{TABLE: [_row()]})
    ctx = rctx.load_router_context(db, USER, CONVO, None)

    script = _Script(_response(_chat()))
    _run_router(db, script, conversation_memory=ctx.conversation_memory)

    assert db.count(TABLE) == 1
    assert AMOUNT in script.infos[0].instructions


def _context_db(messages: list[dict], **tables: list[dict]) -> FakeDB:
    stamped = [
        {
            "message_id": f"m{i}", "conversation_id": CONVO,
            "created_at": f"2026-11-01T00:00:{i:02d}",
            "artifact_ids": [], **row,
        }
        for i, row in enumerate(messages)
    ]
    return FakeDB(
        messages=stamped,
        conversations=[{
            "conversation_id": CONVO, "user_id": USER,
            "compacted_through_message_id": None, "deleted_at": None,
        }],
        **tables,
    )


def _user(text: str) -> dict:
    return {"role": "user", "content": text, "metadata": None}


def _assistant(text: str = "") -> dict:
    return {"role": "assistant", "content": text, "metadata": None}


def _question(text: str) -> dict:
    return {
        "role": "assistant", "content": text,
        "metadata": {"kind": "agent_question", "run_id": "run-1"},
    }


def _answer(text: str) -> dict:
    return {
        "role": "user", "content": text,
        "metadata": {"kind": "agent_answer", "run_id": "run-1"},
    }


_PAUSED_ROWS = [
    _user("Draft the pleading."),
    _question("Is the plan right?"),
    _answer("The amount is 210,000."),
    _assistant("Here is the draft."),
    _user("Now shorten it."),
    _assistant(""),
]


def test_flag_off_load_adds_no_query_and_no_fields(flag_off):
    db = _context_db(_PAUSED_ROWS, **{TABLE: [_row()]})
    ctx = rctx.load_router_context(db, USER, CONVO, None)

    assert db.count(TABLE) == 0
    assert db.count("messages") == 1
    assert ctx.conversation_memory == []
    assert ctx.conversation_memory_md is None


def test_load_adds_one_memory_query_with_the_flag_on(flag_on):
    db = _context_db(_PAUSED_ROWS, **{TABLE: [_row()]})
    ctx = rctx.load_router_context(db, USER, CONVO, None)

    assert db.count(TABLE) == 1
    assert db.count("messages") == 1
    assert [entry.content for entry in ctx.conversation_memory] == [AMOUNT]
    assert AMOUNT in ctx.conversation_memory_md


def _cut_through(db: FakeDB, message_id: str) -> FakeDB:
    db.tables["conversations"][0]["compacted_through_message_id"] = message_id
    return db


def test_the_cut_still_applies_to_message_history(flag_on):
    ctx = rctx.load_router_context(_cut_through(_context_db(_PAUSED_ROWS), "m3"), USER, CONVO, None)

    assert "Here is the draft." not in repr(ctx.message_history)


@pytest.mark.parametrize("flag", ["flag_off", "flag_on"])
def test_the_flag_does_not_change_message_history(flag, request):
    request.getfixturevalue(flag)
    ctx = rctx.load_router_context(_context_db(_PAUSED_ROWS), USER, CONVO, None)

    assert [part.parts[0].content for part in ctx.message_history] == [
        "Draft the pleading.",
        "〔[نظام] هذا سؤال طرحه متخصصٌ أثناء مهمته〕\nIs the plan right?",
        "The amount is 210,000.",
        "Here is the draft.",
        "Now shorten it.",
    ]


# --------------------------------------------------------------------------- #
# Orchestrator: fresh turn
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class _SSResult:
    chat_messages: list = dataclasses.field(default_factory=list)
    created_item_ids: list = dataclasses.field(default_factory=list)
    sse_events: list = dataclasses.field(default_factory=list)
    paused: bool = False
    question_text: str | None = None
    aborted: bool = False
    abort_reason: str = ""
    unlock_notes: list = dataclasses.field(default_factory=list)
    fallback_reason: str = ""


def _specialist_result() -> SpecialistResult:
    return SpecialistResult(chat_summary="The specialist's answer.", model_used="fake")


async def _collect(generator: Any) -> list[dict]:
    return [event async for event in generator]


@pytest.fixture
def seen_route(monkeypatch):
    """Quiet everything around the turn and capture its context."""
    seen: dict[str, Any] = {}
    real_load = rctx.load_router_context

    def _capture(*args: Any, **kwargs: Any) -> Any:
        seen["ctx"] = real_load(*args, **kwargs)
        return seen["ctx"]

    async def _no_ocr(*_a: Any, **_k: Any) -> SimpleNamespace:
        return SimpleNamespace(filled_item_ids=[], failed=0)

    async def _nothing(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(rctx, "load_router_context", _capture)
    monkeypatch.setattr(welcome_mod, "resolve_welcome", lambda *a, **k: None)
    monkeypatch.setattr(welcome_mod, "mark_welcomed", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *a, **k: 0)
    monkeypatch.setattr(orch, "_load_recent_messages", lambda *a, **k: [])
    monkeypatch.setattr(orch, "run_ocr_extraction", _no_ocr)
    monkeypatch.setattr(orch.memory, "resummarize_dirty_items", _nothing)
    monkeypatch.setattr(orch.memory, "compact_conversation", _nothing)
    return seen


class _MemoryAgent:
    """The memory agent's model step, scripted. Its task, ledger scope and write are real.

    Each run takes the next of ``plans``, then ``ops``. ``gate``, when set, holds it
    until released. ``failure`` is raised instead of deciding.
    """

    def __init__(self) -> None:
        self.ops = MemoryOps(add=[MemoryAddOp(kind="fact", content=AMOUNT)])
        self.plans: list[MemoryOps] = []
        self.gate: asyncio.Event | None = None
        self.failure: BaseException | None = None
        self.inputs: list[Any] = []

    async def plan(self, input: Any) -> MemoryOps:
        self.inputs.append(input)
        await asyncio.sleep(0.01)  # A dispatch that did not wait would start here.
        if self.gate is not None:
            await self.gate.wait()
        if self.failure is not None:
            raise self.failure
        return self.plans.pop(0) if self.plans else self.ops


@pytest.fixture
def memory_agent(monkeypatch):
    fake = _MemoryAgent()
    monkeypatch.setattr(writer_runner, "_plan_ops", fake.plan)
    return fake


@pytest.fixture
def fake_writer(seen_route, monkeypatch):
    """A dispatched writer that records what it starts with.

    ``seen_route["on_dispatch"]``, when set, runs as it starts.
    """

    async def _run(major_input: MajorAgentInput, *_a: Any, **_k: Any) -> Any:
        seen_route["input"] = major_input
        seen_route["block_on_ctx"] = seen_route["ctx"].conversation_memory_md
        if "on_dispatch" in seen_route:
            seen_route["on_dispatch"]()
        return SimpleNamespace(kind="completed", result=_specialist_result())

    monkeypatch.setattr(orch, "_run_writer", _run)


async def _turn(
    db: FakeDB,
    script: _Script,
    *,
    question: str = "The amount is 210,000.",
    user_message_id: str | None = None,
    conversation_id: str = CONVO,
    codec: Any = None,
) -> list[dict]:
    """One turn to the end of its generator.

    The timeout turns a turn that waits for a held run into a failure, not a hang.
    """
    with rr.router_agent.override(model=FunctionModel(script.respond)):
        return await asyncio.wait_for(
            _collect(orch._handle_message_inner(
                question=question, user_id=USER, conversation_id=conversation_id,
                supabase=db, user_message_id=user_message_id, codec=codec,
            )),
            timeout=5,
        )


async def _runs_done() -> None:
    """``asyncio.run`` cancels what is left on exit, so wait for any run still going."""
    if writer_runner._BACKGROUND_TASKS:
        await asyncio.wait(list(writer_runner._BACKGROUND_TASKS))


def _run_turn(
    db: FakeDB,
    script: _Script,
    *,
    when_done: Callable[[], None] | None = None,
    **turn: Any,
) -> list[dict]:
    """One turn, then any run that outlived it. ``when_done`` runs between the two."""

    async def _go() -> list[dict]:
        events = await _turn(db, script, **turn)
        if when_done is not None:
            when_done()
        await _runs_done()
        return events

    return asyncio.run(_go())


def _empty_turn(**tables: list[dict]) -> FakeDB:
    return _context_db([_user("hi"), _assistant("")], **tables)


def _completed(events: list[dict]) -> bool:
    """A dispatch ends on ``agent_run_finished``, with ``done`` just before it."""
    return [event["type"] for event in events][-2:] == ["done", "agent_run_finished"]


def _tokens(events: list[dict]) -> str:
    return "".join(event["text"] for event in events if event["type"] == "token")


def _writing() -> _Script:
    return _Script(_response(_dispatch("writing")))


def _chatting(message: str = "Noted.") -> _Script:
    return _Script(_response(_chat(message)))


def _noting_the_run(script: _Script, running: list[bool]) -> _Script:
    """A router that records whether the run was still going as it answered."""
    plain = script.respond

    def respond(messages: list, info: AgentInfo) -> ModelResponse:
        running.extend(not task.done() for task in writer_runner._BACKGROUND_TASKS)
        return plain(messages, info)

    script.respond = respond
    return script


def _after_the_run(script: _Script, memory_agent: _MemoryAgent) -> _Script:
    """A router that answers only once the run has ended, after the turn loaded its context."""
    plain = script.respond

    async def respond(messages: list, info: AgentInfo) -> ModelResponse:
        memory_agent.gate.set()
        await _runs_done()
        return plain(messages, info)

    script.respond = respond
    return script


# The start point


@pytest.mark.parametrize(
    "output",
    [pytest.param(_chat, id="chat"), pytest.param(lambda: _dispatch("writing"), id="dispatch")],
)
def test_a_normal_turn_starts_one_run(flag_on, seen_route, memory_agent, fake_writer, output):
    _run_turn(_empty_turn(), _Script(_response(output())))

    assert len(memory_agent.inputs) == 1


def test_a_message_with_no_text_starts_no_run(flag_on, seen_route, memory_agent):
    _run_turn(_empty_turn(), _chatting(), question=" \n ")

    assert memory_agent.inputs == []


def test_a_disabled_user_starts_no_run(flag_off, seen_route, memory_agent):
    db = _empty_turn()
    _run_turn(db, _chatting())

    assert memory_agent.inputs == []
    assert db.count(TABLE) == 0


def test_a_user_outside_the_allowlist_has_no_memory_on_a_turn(
    flag_on, allowlist, seen_route, memory_agent
):
    allowlist("user-B")
    db = _context_db(_PAUSED_ROWS, **{TABLE: [_row()]})
    script = _chatting()
    _run_turn(db, script)

    assert memory_agent.inputs == []
    assert db.count(TABLE) == 0
    assert "<conversation_memory>" not in script.infos[0].instructions


def test_a_pinned_plan_starts_no_run(flag_on, memory_agent, monkeypatch):
    async def fake_dispatch(**_kwargs: Any) -> Any:
        yield {"type": "done", "usage": {}}

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)

    async def _go() -> None:
        await _collect(orch._handle_message_inner(
            question="q", user_id=USER, conversation_id=CONVO, supabase=FakeDB(),
            pinned_plan=SimpleNamespace(agent_family="deep_search", task_label="label"),
        ))
        # This turn never suspends, so ``asyncio.run`` would cancel a run before it
        # reached the model, and the check below would pass whatever the code did.
        await _runs_done()

    asyncio.run(_go())

    assert memory_agent.inputs == []


def test_the_run_starts_after_the_attachments_are_read(
    flag_on, seen_route, memory_agent, monkeypatch
):
    """An attachment's summary and title have to exist before it reads them."""
    order: list[str] = []

    async def fake_ocr(*_a: Any, **_k: Any) -> SimpleNamespace:
        order.append("ocr")
        return SimpleNamespace(filled_item_ids=["item-1"], failed=0)

    async def fake_summary(*_a: Any, **_k: Any) -> None:
        order.append("summary")

    real_start = orch._start_memory_writer

    def noting_start(**kwargs: Any) -> Any:
        order.append("start")
        return real_start(**kwargs)

    monkeypatch.setattr(orch, "run_ocr_extraction", fake_ocr)
    monkeypatch.setattr(orch, "summarize_workspace_item", fake_summary)
    monkeypatch.setattr(orch, "_start_memory_writer", noting_start)
    _run_turn(_empty_turn(), _chatting())

    assert order == ["ocr", "summary", "start"]


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param("build_memory_writer_input", id="the-input"),
        pytest.param("schedule_memory_writer", id="the-task"),
    ],
)
@pytest.mark.parametrize(
    "script",
    [pytest.param(_chatting, id="chat"), pytest.param(_writing, id="dispatch")],
)
def test_a_failed_start_leaves_the_turn_intact(
    flag_on, seen_route, fake_writer, monkeypatch, caplog, broken, script
):
    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("SECRET-IN-THE-ERROR")

    monkeypatch.setattr(orch, broken, _boom)
    with caplog.at_level(logging.WARNING):
        events = _run_turn(_empty_turn(), script())

    assert "done" in [event["type"] for event in events]
    assert "convo_memory_writer not started" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "SECRET-IN-THE-ERROR" not in caplog.text


def test_a_run_that_could_not_start_leaves_the_turn_intact(
    flag_on, seen_route, fake_writer, monkeypatch
):
    monkeypatch.setattr(orch, "schedule_memory_writer", lambda *_a: None)
    events = _run_turn(_empty_turn(), _writing())

    assert _completed(events)
    assert "input" in seen_route


# What the run reads


def test_the_run_gets_the_window_and_the_turns_identity(
    flag_on, seen_route, memory_agent, monkeypatch
):
    monkeypatch.setattr(orch, "_load_recent_messages", _REAL_RECENT_MESSAGES)
    db = _context_db(
        [_user("Fell out of the window."), *_PAUSED_ROWS],
        **{TABLE: [_row()]},
        workspace_items=[{
            "item_id": "item-1", "conversation_id": CONVO, "user_id": USER, "kind": "attachment",
            "title": "The decision", "created_at": "2026-11-01T00:00:00", "deleted_at": None,
        }],
    )
    _run_turn(db, _chatting(), question="Now shorten it.", user_message_id="msg-1")

    sent = memory_agent.inputs[0]
    assert sent.user_message == "Now shorten it."
    assert [(row.role, row.content) for row in sent.recent_messages] == [
        ("user", "Draft the pleading."),
        ("assistant", "Is the plan right?"),
        ("user", "The amount is 210,000."),
        ("assistant", "Here is the draft."),
    ]
    assert [entry.content for entry in sent.entries] == [AMOUNT]
    assert sent.item_titles == ["The decision"]
    assert sent.today.startswith("Today is ")
    assert (sent.conversation_id, sent.user_id) == (CONVO, USER)
    assert sent.message_id == "msg-1"


def test_a_failed_window_read_still_gets_the_newest_message(
    flag_on, seen_route, memory_agent, monkeypatch
):
    monkeypatch.setattr(orch, "_load_recent_messages", _REAL_RECENT_MESSAGES)
    db = _empty_turn()
    db.fail["messages"] = RuntimeError("down")
    _run_turn(db, _chatting(), question="Keep the amount.")

    sent = memory_agent.inputs[0]
    assert sent.user_message == "Keep the amount."
    assert sent.recent_messages == []


# A dispatch


def test_a_dispatch_starts_only_after_the_run_ends(
    flag_on, seen_route, memory_agent, fake_writer
):
    memory_agent.gate = asyncio.Event()
    db = _empty_turn()
    seen_route["on_dispatch"] = lambda: seen_route.update(stored=db.contents())

    async def _go() -> None:
        asyncio.get_running_loop().call_later(0.05, memory_agent.gate.set)
        await _turn(db, _writing())

    asyncio.run(_go())

    assert seen_route["stored"] == [AMOUNT]
    assert AMOUNT in seen_route["input"].conversation_memory_md


def test_the_new_block_reaches_ctx_as_well(flag_on, seen_route, memory_agent, fake_writer):
    _run_turn(_empty_turn(), _writing())

    assert seen_route["block_on_ctx"] == seen_route["input"].conversation_memory_md
    assert seen_route["input"].router_context is seen_route["ctx"]
    assert [entry.content for entry in seen_route["ctx"].conversation_memory] == [AMOUNT]


def test_a_run_that_ended_before_the_router_still_reaches_the_dispatch(
    flag_on, seen_route, memory_agent, fake_writer
):
    """The turn loaded its context before the run wrote, and the run is over by the
    dispatch, so a lookup of unfinished runs finds nothing. Only the handle can tell."""
    memory_agent.gate = asyncio.Event()
    script = _after_the_run(_writing(), memory_agent)
    _run_turn(_empty_turn(), script)

    assert AMOUNT not in script.infos[0].instructions
    assert AMOUNT in seen_route["input"].conversation_memory_md
    assert [entry.content for entry in seen_route["ctx"].conversation_memory] == [AMOUNT]


def test_a_run_that_ended_before_the_context_loaded_is_in_it(
    flag_on, seen_route, memory_agent, fake_writer, monkeypatch
):
    monkeypatch.setattr(orch.memory, "resummarize_dirty_items", lambda *_a: _runs_done())
    script = _writing()
    _run_turn(_empty_turn(), script)

    assert AMOUNT in script.infos[0].instructions
    assert AMOUNT in seen_route["input"].conversation_memory_md


@pytest.mark.parametrize(
    "script",
    [pytest.param(_chatting, id="chat"), pytest.param(_writing, id="dispatch")],
)
def test_the_router_runs_beside_the_run_and_never_waits_for_it(
    flag_on, seen_route, memory_agent, fake_writer, script
):
    """An early wait would be on this turn's own run, before the router."""
    memory_agent.gate = asyncio.Event()
    running: list[bool] = []
    router = _noting_the_run(script(), running)

    async def _go() -> None:
        asyncio.get_running_loop().call_later(0.05, memory_agent.gate.set)
        await _turn(_empty_turn(), router)

    asyncio.run(_go())

    assert running == [True]


def test_a_slow_run_does_not_hold_the_dispatch_past_the_ceiling(
    flag_on, seen_route, memory_agent, fake_writer, monkeypatch
):
    monkeypatch.setattr(orch, "MEMORY_WRITER_CEILING_S", 0.05)
    monkeypatch.setattr(orch, "_logfire", MagicMock())
    memory_agent.gate = asyncio.Event()

    def _at_dispatch() -> None:
        seen_route["tasks"] = list(writer_runner._BACKGROUND_TASKS)
        memory_agent.gate.set()

    seen_route["on_dispatch"] = _at_dispatch
    db = _empty_turn(**{TABLE: [_row(1, content="The old entry.")]})
    _run_turn(db, _writing())

    block = seen_route["input"].conversation_memory_md
    assert "The old entry." in block
    assert AMOUNT not in block
    assert seen_route["block_on_ctx"] == block
    orch._logfire.info.assert_any_call(
        "convo_memory_writer.past_ceiling", conversation_id=CONVO
    )

    task = seen_route["tasks"][0]
    assert task.done() and not task.cancelled()
    assert task.result().added == 1
    assert db.contents() == ["The old entry.", AMOUNT]


# A chat reply


def test_a_chat_turn_reaches_done_while_the_run_goes_on(flag_on, seen_route, memory_agent):
    memory_agent.gate = asyncio.Event()
    running: list[bool] = []

    def _release() -> None:
        running.extend(not task.done() for task in writer_runner._BACKGROUND_TASKS)
        memory_agent.gate.set()

    db = _empty_turn()
    events = _run_turn(db, _chatting(), when_done=_release)

    assert events[-1]["type"] == "done"
    assert _tokens(events) == "Noted."
    assert running == [True]
    assert db.contents() == [AMOUNT]


def test_a_chat_turn_does_not_wait_at_all(flag_on, seen_route, memory_agent, monkeypatch):
    waits: list[Any] = []

    async def _spy(*args: Any) -> bool:
        waits.append(args)
        return True

    monkeypatch.setattr(orch, "_settle_memory_writer", _spy)
    _run_turn(_empty_turn(), _chatting())

    assert waits == []


# The turn after


def test_a_task_right_after_a_correction_carries_it(
    flag_on, seen_route, memory_agent, fake_writer
):
    """Scenario A: «ابحث» or a chip while the run from the correction is still going.

    The router loads before that run ends, and this turn's own run saves nothing. It
    queues behind the earlier one and reads what that one wrote, so the dispatch has it.
    """
    memory_agent.gate = asyncio.Event()
    memory_agent.plans = [memory_agent.ops, MemoryOps()]
    db = _empty_turn()
    follow_up = _writing()

    async def _go() -> None:
        await _turn(db, _chatting())
        assert db.contents() == []
        asyncio.get_running_loop().call_later(0.05, memory_agent.gate.set)
        await _turn(db, follow_up, question="Search now.")

    asyncio.run(_go())

    assert AMOUNT not in follow_up.infos[0].instructions
    assert AMOUNT in seen_route["input"].conversation_memory_md
    assert [entry.content for entry in seen_route["ctx"].conversation_memory] == [AMOUNT]


def test_an_earlier_run_past_the_ceiling_does_not_block_the_task(
    flag_on, seen_route, memory_agent, fake_writer, monkeypatch
):
    monkeypatch.setattr(orch, "MEMORY_WRITER_CEILING_S", 0.05)
    monkeypatch.setattr(orch, "_logfire", MagicMock())
    memory_agent.gate = asyncio.Event()
    memory_agent.plans = [memory_agent.ops, MemoryOps()]
    db = _empty_turn(**{TABLE: [_row(1, content="The old entry.")]})

    async def _go() -> Any:
        await _turn(db, _chatting())
        task = writer_runner.pending_memory_writer(CONVO)
        await _turn(db, _writing(), question="Search now.")
        assert not task.done()
        memory_agent.gate.set()
        await _runs_done()
        return task

    task = asyncio.run(_go())

    block = seen_route["input"].conversation_memory_md
    assert "The old entry." in block
    assert AMOUNT not in block
    orch._logfire.info.assert_any_call(
        "convo_memory_writer.past_ceiling", conversation_id=CONVO
    )
    assert not task.cancelled()
    assert db.contents() == ["The old entry.", AMOUNT]


# A failed run


_RUN_FAILURES = [
    pytest.param(lambda: RuntimeError("model down"), id="failed"),
    pytest.param(lambda: asyncio.CancelledError(), id="cancelled"),
]


@pytest.mark.parametrize("failure", _RUN_FAILURES)
def test_an_earlier_run_that_fails_leaves_the_next_turn_intact(
    flag_on, seen_route, memory_agent, fake_writer, failure
):
    memory_agent.gate = asyncio.Event()
    memory_agent.failure = failure()
    db = _empty_turn(**{TABLE: [_row()]})

    async def _go() -> list[dict]:
        await _turn(db, _chatting())
        asyncio.get_running_loop().call_later(0.05, memory_agent.gate.set)
        return await _turn(db, _writing(), question="Search now.")

    events = asyncio.run(_go())

    assert _completed(events)
    assert seen_route["input"].conversation_memory_md == render_conversation_memory([_entry()])


@pytest.mark.parametrize("failure", _RUN_FAILURES)
def test_a_failed_run_leaves_a_dispatch_intact(
    flag_on, seen_route, memory_agent, fake_writer, failure
):
    memory_agent.failure = failure()
    db = _empty_turn(**{TABLE: [_row()]})
    events = _run_turn(db, _writing())

    assert _completed(events)
    assert AMOUNT in seen_route["input"].conversation_memory_md
    assert db.contents() == [AMOUNT]


@pytest.mark.parametrize("failure", _RUN_FAILURES)
def test_a_failed_run_leaves_a_chat_turn_intact(flag_on, seen_route, memory_agent, failure):
    memory_agent.failure = failure()
    events = _run_turn(_empty_turn(), _chatting())

    assert events[-1]["type"] == "done"
    assert _tokens(events) == "Noted."


def test_a_failed_write_leaves_a_dispatch_intact(flag_on, seen_route, memory_agent, fake_writer):
    db = _empty_turn()
    db.fail[TABLE] = RuntimeError("connection reset")
    events = _run_turn(db, _writing())

    assert _completed(events)
    assert seen_route["input"].conversation_memory_md is None


def test_a_failed_read_keeps_the_block_the_turn_loaded(
    flag_on, seen_route, memory_agent, fake_writer, monkeypatch
):
    """An unreadable table must not read as an empty memory and wipe the turn's block."""

    def _unreadable(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("down")

    db = _empty_turn(**{TABLE: [_row()]})
    monkeypatch.setattr(writer_runner, "load_conversation_memory", _unreadable)
    events = _run_turn(db, _writing())

    assert _completed(events)
    assert memory_agent.inputs == []
    assert seen_route["input"].conversation_memory_md == render_conversation_memory([_entry()])
    assert [entry.content for entry in seen_route["ctx"].conversation_memory] == [AMOUNT]


def test_a_run_task_that_raises_leaves_the_turn_intact(
    flag_on, seen_route, fake_writer, monkeypatch, caplog
):
    async def _boom() -> None:
        raise RuntimeError("SECRET-IN-THE-ERROR")

    monkeypatch.setattr(orch, "schedule_memory_writer", lambda *_a: asyncio.ensure_future(_boom()))
    with caplog.at_level(logging.WARNING):
        events = _run_turn(_empty_turn(), _writing())

    assert _completed(events)
    assert seen_route["input"].conversation_memory_md is None
    assert "convo_memory_writer ended early" in caplog.text
    assert "SECRET-IN-THE-ERROR" not in caplog.text


def test_a_run_that_saves_nothing_leaves_the_loaded_block(
    flag_on, seen_route, memory_agent, fake_writer
):
    memory_agent.ops = MemoryOps()
    db = _empty_turn(**{TABLE: [_row()]})
    _run_turn(db, _writing())

    assert seen_route["input"].conversation_memory_md == render_conversation_memory([_entry()])


def test_a_removal_by_the_run_clears_the_block(flag_on, seen_route, memory_agent, fake_writer):
    memory_agent.ops = MemoryOps(remove=["M-1"])
    db = _empty_turn(**{TABLE: [_row()]})
    _run_turn(db, _writing())

    assert db.contents() == []
    assert seen_route["input"].conversation_memory_md is None
    assert seen_route["ctx"].conversation_memory_md is None
    assert seen_route["ctx"].conversation_memory == []


def test_router_gets_the_loaded_memory_and_the_paused_reply(flag_on, seen_route, memory_agent):
    memory_agent.ops = MemoryOps()
    db = _context_db(_PAUSED_ROWS, **{TABLE: [_row()]})
    script = _chatting()
    _run_turn(db, script)

    assert AMOUNT in script.infos[0].instructions
    assert "The amount is 210,000." in repr(script.messages[0])


def test_simple_search_dispatch_gets_the_block(flag_on, seen_route, memory_agent, monkeypatch):
    async def fake_run(*_a: Any, **kwargs: Any) -> _SSResult:
        seen_route["kwargs"] = kwargs
        return _SSResult(chat_messages=["The text of the article."])

    monkeypatch.setattr(orch, "run_simple_search", fake_run)
    _run_turn(_empty_turn(), _Script(_response(_dispatch("simple_search"))))

    assert AMOUNT in seen_route["kwargs"]["conversation_memory_md"]


# The ledger, with the real agent on a scripted model


def _good_ops(_messages: list, info: AgentInfo) -> ModelResponse:
    args = {"add": [{"kind": "fact", "content": AMOUNT}]}
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


def _bad_ops(_messages: list, info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"remove": ["M-9"]})])


def _ledger(db: FakeDB) -> list[dict]:
    return [row for row in db.tables.get("llm_calls", []) if row["agent"] == RUN_STAGE]


@pytest.mark.parametrize(
    "model, entries",
    [pytest.param(_good_ops, [AMOUNT], id="a-write"), pytest.param(_bad_ops, [], id="a-failed-run")],
)
@pytest.mark.parametrize(
    "script",
    [pytest.param(_chatting, id="chat"), pytest.param(_writing, id="dispatch")],
)
def test_every_path_leaves_a_ledger_row_with_the_turns_message(
    flag_on, seen_route, fake_writer, monkeypatch, script, model, entries
):
    monkeypatch.setattr(writer_agent, "get_agent_model", lambda _slot: FunctionModel(model))
    db = _empty_turn()
    _run_turn(db, script(), user_message_id="msg-1", question="المبلغ 210,000 مو 310,000")

    rows = _ledger(db)
    assert len(rows) == 1
    assert (rows[0]["message_id"], rows[0]["conversation_id"]) == ("msg-1", CONVO)
    assert db.contents() == entries


def test_deep_search_passes_the_block_to_planner_deps(monkeypatch):
    class _Stop(Exception):
        pass

    captured: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> None:
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setattr(planner_pkg, "build_planner_deps", _capture)
    major_input = MajorAgentInput(
        describe_query="q", task_label="label",
        conversation_memory_md="THE-BLOCK", user_id=USER, conversation_id=CONVO,
    )
    with pytest.raises(_Stop):
        asyncio.run(orch._run_deep_search(major_input, FakeDB()))

    assert captured["conversation_memory_md"] == "THE-BLOCK"


def test_pinned_dispatch_passes_no_memory(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_dispatch(**kwargs: Any) -> Any:
        captured.update(kwargs)
        yield {"type": "done", "usage": {}}

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)
    asyncio.run(_collect(orch._handle_message_inner(
        question="q", user_id=USER, conversation_id=CONVO, supabase=FakeDB(),
        pinned_plan=SimpleNamespace(agent_family="deep_search", task_label="label"),
    )))

    assert captured["agent_family"] == "deep_search"
    assert captured.get("conversation_memory_md") is None


# --------------------------------------------------------------------------- #
# Orchestrator: the resume loader and every resume leg
# --------------------------------------------------------------------------- #


def test_loader_returns_the_rendered_block(flag_on):
    db = FakeDB(**{TABLE: [_row()]})

    assert orch._load_conversation_memory(db, CONVO, USER) == render_conversation_memory(
        [_entry()]
    )


def test_loader_is_owner_scoped(flag_on):
    db = FakeDB(**{TABLE: [_row(content="mine"), _row(content="theirs", user="user-B")]})
    block = orch._load_conversation_memory(db, CONVO, USER)

    assert "mine" in block
    assert "theirs" not in block


def test_loader_is_none_with_no_entries(flag_on):
    assert orch._load_conversation_memory(FakeDB(), CONVO, USER) is None


def test_loader_is_none_with_no_query_when_off(flag_off):
    db = FakeDB(**{TABLE: [_row()]})

    assert orch._load_conversation_memory(db, CONVO, USER) is None
    assert db.queries == []


def test_loader_never_raises_on_a_db_failure(flag_on):
    db = FakeDB()
    db.fail[TABLE] = RuntimeError("down")

    assert orch._load_conversation_memory(db, CONVO, USER) is None


def test_loader_never_raises_on_a_later_failure(flag_on, monkeypatch):
    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("persist down")

    monkeypatch.setattr(masking, "persist_new_mappings", _boom)

    assert orch._load_conversation_memory(FakeDB(**{TABLE: [_row()]}), CONVO, USER) is None


def test_loader_encodes_and_persists_the_fakes(flag_on, codec, monkeypatch):
    flushed: list[Any] = []
    monkeypatch.setattr(
        masking, "persist_new_mappings", lambda _db, user_id, _codec: flushed.append(user_id)
    )
    db = FakeDB(**{TABLE: [_row(content=f"id {REAL_ID}")]})
    block = orch._load_conversation_memory(db, CONVO, USER)

    assert FAKE_ID in block
    assert REAL_ID not in block
    assert flushed == [USER]


def _pending(family: str, **over: Any) -> dict:
    return {
        "run_id": "run-1", "agent_family": family, "task_label": "label",
        "message_history": b"[]", "deferred_payload": {"tool_call_id": "call-1"},
        **over,
    }


def _resume(db: FakeDB, family: str) -> list[dict]:
    return asyncio.run(_collect(orch._resume_major_agent_inner(
        pending=_pending(family), user_reply="yes", supabase=db, user_id=USER,
        conversation_id=CONVO, case_id=None, user_message_id=None,
    )))


@pytest.fixture
def quiet_resume(monkeypatch):
    async def _no_gate(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(orch, "_load_recent_messages", lambda *a, **k: [])
    monkeypatch.setattr(orch, "_resolve_pause_loud", lambda *a, **k: True)
    monkeypatch.setattr(orch, "_check_deep_search_gate", _no_gate)


def test_writing_resume_loads_the_block(flag_on, quiet_resume, monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_writer(major_input: MajorAgentInput, *_a: Any, **_k: Any) -> Any:
        captured["input"] = major_input
        return SimpleNamespace(kind="completed", result=_specialist_result())

    monkeypatch.setattr(orch, "_run_writer", fake_writer)
    db = FakeDB(**{TABLE: [_row()]})
    _resume(db, "writing")

    assert AMOUNT in captured["input"].conversation_memory_md
    assert captured["input"].router_context is None
    assert db.count(TABLE) == 1


def test_deep_search_resume_loads_the_block_once_for_both_phases(
    flag_on, quiet_resume, monkeypatch
):
    captured: dict[str, Any] = {}

    def fake_deps(**kwargs: Any) -> SimpleNamespace:
        captured["deps"] = kwargs
        return SimpleNamespace()

    async def fake_decide(*_a: Any, **_k: Any) -> SimpleNamespace:
        return SimpleNamespace(output=planner_pkg.PlannerDecision.model_construct(aborted=False))

    async def fake_deep_search(major_input: MajorAgentInput, *_a: Any, **_k: Any) -> Any:
        captured["input"] = major_input
        return orch._DeepSearchOutcome(kind="completed", result=_specialist_result())

    monkeypatch.setattr(planner_pkg, "build_planner_deps", fake_deps)
    monkeypatch.setattr(
        planner_agent_mod, "create_planner_decider",
        lambda: SimpleNamespace(run=fake_decide),
    )
    monkeypatch.setattr(orch, "_run_deep_search", fake_deep_search)
    db = FakeDB(**{TABLE: [_row()]})
    _resume(db, "deep_search")

    assert AMOUNT in captured["deps"]["conversation_memory_md"]
    assert AMOUNT in captured["input"].conversation_memory_md
    assert db.count(TABLE) == 1


def test_simple_search_resume_loads_the_block(flag_on, quiet_resume, monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_resume(*_a: Any, **kwargs: Any) -> _SSResult:
        captured.update(kwargs)
        return _SSResult(chat_messages=["An answer."])

    monkeypatch.setattr(ss_runner, "resume_simple_search", fake_resume, raising=False)
    db = FakeDB(**{TABLE: [_row()]})
    _resume(db, "simple_search")

    assert AMOUNT in captured["conversation_memory_md"]
    assert db.count(TABLE) == 1


def test_simple_search_resume_hand_off_keeps_the_block_on_one_read(
    flag_on, quiet_resume, monkeypatch
):
    captured: dict[str, Any] = {}

    async def fake_resume(*_a: Any, **_k: Any) -> _SSResult:
        return _SSResult(aborted=True, abort_reason="not a lookup")

    async def fake_deep_search(major_input: MajorAgentInput, *_a: Any, **_k: Any) -> Any:
        captured["input"] = major_input
        return orch._DeepSearchOutcome(kind="completed", result=_specialist_result())

    monkeypatch.setattr(ss_runner, "resume_simple_search", fake_resume, raising=False)
    monkeypatch.setattr(orch, "_run_deep_search", fake_deep_search)
    db = FakeDB(**{TABLE: [_row()]})
    _resume(db, "simple_search")

    assert AMOUNT in captured["input"].conversation_memory_md
    assert db.count(TABLE) == 1


def test_a_resume_leg_with_the_flag_off_reads_nothing(flag_off, quiet_resume, monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_writer(major_input: MajorAgentInput, *_a: Any, **_k: Any) -> Any:
        captured["input"] = major_input
        return SimpleNamespace(kind="completed", result=_specialist_result())

    monkeypatch.setattr(orch, "_run_writer", fake_writer)
    db = FakeDB(**{TABLE: [_row()]})
    _resume(db, "writing")

    assert captured["input"].conversation_memory_md is None
    assert db.count(TABLE) == 0


# --------------------------------------------------------------------------- #
# Orchestrator: a reply to a specialist goes through the one start point
# --------------------------------------------------------------------------- #


_LEGS = ["writing", "deep_search", "simple_search"]


@pytest.fixture
def paused(monkeypatch):
    """Give the conversation an open pause of one family."""

    def _open(family: str, *, expired: bool = False, **over: Any) -> None:
        monkeypatch.setattr(orch, "_find_awaiting_user", lambda *_a: _pending(family, **over))
        monkeypatch.setattr(orch, "_expired", lambda _pending_row: expired)
        monkeypatch.setattr(orch, "_pause_is_current", lambda *_a: True)

    return _open


@pytest.fixture
def leg(monkeypatch):
    """Stub the model behind one resume leg. Returns what the leg handed it."""

    def _install(family: str) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        async def fake_writer(major_input: MajorAgentInput, *_a: Any, **_k: Any) -> Any:
            captured["block"] = major_input.conversation_memory_md
            return SimpleNamespace(kind="completed", result=_specialist_result())

        def fake_deps(**kwargs: Any) -> SimpleNamespace:
            captured["block"] = kwargs["conversation_memory_md"]
            return SimpleNamespace()

        async def fake_decide(*_a: Any, **_k: Any) -> SimpleNamespace:
            return SimpleNamespace(
                output=planner_pkg.PlannerDecision.model_construct(aborted=False)
            )

        async def fake_deep_search(*_a: Any, **_k: Any) -> Any:
            return orch._DeepSearchOutcome(kind="completed", result=_specialist_result())

        async def fake_resume(*_a: Any, **kwargs: Any) -> _SSResult:
            captured["block"] = kwargs["conversation_memory_md"]
            return _SSResult(chat_messages=["An answer."])

        monkeypatch.setattr(orch, "_run_writer", fake_writer)
        monkeypatch.setattr(planner_pkg, "build_planner_deps", fake_deps)
        monkeypatch.setattr(
            planner_agent_mod, "create_planner_decider",
            lambda: SimpleNamespace(run=fake_decide),
        )
        monkeypatch.setattr(orch, "_run_deep_search", fake_deep_search)
        monkeypatch.setattr(ss_runner, "resume_simple_search", fake_resume, raising=False)
        return captured

    return _install


@pytest.mark.parametrize("family", _LEGS)
def test_a_reply_starts_the_run_and_no_router_is_called(
    flag_on, seen_route, quiet_resume, memory_agent, paused, leg, family
):
    leg(family)
    paused(family)
    script = _Script()
    _run_turn(_empty_turn(), script, question="Yes, and the amount is 210,000.")

    assert [sent.user_message for sent in memory_agent.inputs] == [
        "Yes, and the amount is 210,000."
    ]
    assert script.infos == []


@pytest.mark.parametrize("family", _LEGS)
def test_each_resume_leg_reads_the_memory_after_the_run(
    flag_on, seen_route, quiet_resume, memory_agent, paused, leg, family
):
    memory_agent.gate = asyncio.Event()
    captured = leg(family)
    paused(family)
    db = _empty_turn()

    async def _go() -> None:
        asyncio.get_running_loop().call_later(0.05, memory_agent.gate.set)
        await _turn(db, _Script())

    asyncio.run(_go())

    assert AMOUNT in captured["block"]


@pytest.mark.parametrize("family", _LEGS)
def test_each_resume_leg_reads_a_run_that_ended_before_it_got_there(
    flag_on, seen_route, quiet_resume, memory_agent, paused, leg, family
):
    captured = leg(family)
    paused(family)
    db = _empty_turn()

    async def _go() -> None:
        turn = orch._handle_message_inner(
            question="yes", user_id=USER, conversation_id=CONVO, supabase=db,
        )
        async for event in turn:
            if event["type"] == "agent_resumed":
                await _runs_done()
                assert db.contents() == [AMOUNT]

    asyncio.run(_go())

    assert AMOUNT in captured["block"]


@pytest.mark.parametrize("family", _LEGS)
def test_a_slow_run_does_not_hold_a_resume_leg_past_the_ceiling(
    flag_on, seen_route, quiet_resume, memory_agent, paused, leg, family, monkeypatch
):
    monkeypatch.setattr(orch, "MEMORY_WRITER_CEILING_S", 0.05)
    memory_agent.gate = asyncio.Event()
    captured = leg(family)
    paused(family)
    db = _empty_turn(**{TABLE: [_row(1, content="The old entry.")]})
    _run_turn(db, _Script(), when_done=memory_agent.gate.set)

    assert "The old entry." in captured["block"]
    assert AMOUNT not in captured["block"]
    assert db.contents() == ["The old entry.", AMOUNT]


@pytest.mark.parametrize("failure", _RUN_FAILURES)
@pytest.mark.parametrize("family", _LEGS)
def test_a_failed_run_leaves_a_resume_leg_intact(
    flag_on, seen_route, quiet_resume, memory_agent, paused, leg, family, failure
):
    memory_agent.failure = failure()
    captured = leg(family)
    paused(family)
    db = _empty_turn(**{TABLE: [_row()]})
    _run_turn(db, _Script())

    assert AMOUNT in captured["block"]


def test_a_reply_leaves_a_ledger_row_with_the_turns_message(
    flag_on, seen_route, quiet_resume, paused, leg, monkeypatch
):
    monkeypatch.setattr(writer_agent, "get_agent_model", lambda _slot: FunctionModel(_good_ops))
    leg("writing")
    paused("writing")
    db = _empty_turn()
    _run_turn(db, _Script(), user_message_id="msg-1")

    assert [row["message_id"] for row in _ledger(db)] == ["msg-1"]


def test_an_expired_pause_falls_through_on_the_same_run(
    flag_on, seen_route, quiet_resume, memory_agent, fake_writer, paused
):
    paused("writing", expired=True)
    script = _writing()
    _run_turn(_empty_turn(), script)

    assert len(memory_agent.inputs) == 1
    assert len(script.infos) == 1
    assert AMOUNT in seen_route["input"].conversation_memory_md


def _planner_aborts(monkeypatch: Any) -> None:
    async def _abort(*_a: Any, **_k: Any) -> SimpleNamespace:
        return SimpleNamespace(
            output=planner_pkg.PlannerDecision.model_construct(aborted=True, rationale="")
        )

    monkeypatch.setattr(planner_pkg, "build_planner_deps", lambda **_k: SimpleNamespace())
    monkeypatch.setattr(
        planner_agent_mod, "create_planner_decider", lambda: SimpleNamespace(run=_abort)
    )


def _planner_fails(monkeypatch: Any) -> None:
    async def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("provider down")

    monkeypatch.setattr(planner_pkg, "build_planner_deps", lambda **_k: SimpleNamespace())
    monkeypatch.setattr(
        planner_agent_mod, "create_planner_decider", lambda: SimpleNamespace(run=_boom)
    )


def _writer_hands_off(monkeypatch: Any) -> None:
    real = orch._run_writer
    outcomes = [SimpleNamespace(kind="handoff", handoff_reason="a new request")]

    async def _run(*args: Any, **kwargs: Any) -> Any:
        return outcomes.pop(0) if outcomes else await real(*args, **kwargs)

    monkeypatch.setattr(orch, "_run_writer", _run)


def _simple_search_is_missing(monkeypatch: Any) -> None:
    monkeypatch.delattr(ss_runner, "resume_simple_search", raising=False)


@pytest.mark.parametrize(
    "family, arrange, pause",
    [
        pytest.param("deep_search", _planner_aborts, {}, id="planner-abort"),
        pytest.param("deep_search", _planner_fails, {}, id="planner-failure"),
        pytest.param("writing", _writer_hands_off, {}, id="writer-hand-off"),
        pytest.param("memory", lambda _m: None, {}, id="unsupported-family"),
        pytest.param("writing", lambda _m: None, {"message_history": 7}, id="bad-history"),
        pytest.param("simple_search", _simple_search_is_missing, {}, id="family-missing"),
    ],
)
def test_a_re_route_keeps_the_one_run_and_the_dispatch_carries_it(
    flag_on, seen_route, quiet_resume, memory_agent, fake_writer, paused, monkeypatch,
    family, arrange, pause,
):
    arrange(monkeypatch)
    paused(family, **pause)
    script = _writing()
    _run_turn(_empty_turn(), script)

    assert len(memory_agent.inputs) == 1
    assert len(script.infos) == 1
    assert AMOUNT in seen_route["input"].conversation_memory_md


def test_a_re_route_that_ends_in_a_chat_reply_does_not_wait(
    flag_on, seen_route, quiet_resume, memory_agent, paused
):
    memory_agent.gate = asyncio.Event()
    paused("memory")
    running: list[bool] = []
    router = _noting_the_run(_chatting(), running)
    events = _run_turn(_empty_turn(), router, when_done=memory_agent.gate.set)

    assert running == [True]
    assert events[-1]["type"] == "done"


def _calls_into_a_leg() -> list[ast.Call]:
    source = Path(orch.__file__).read_text(encoding="utf-8")
    legs = {
        "_route", "_resume_major_agent", "_resume_major_agent_inner", "_resume_simple_search_leg",
    }
    return [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in legs
    ]


def test_every_way_into_a_leg_hands_the_run_on():
    """A call that drops it would leave that path with no way to wait for this run."""
    calls = _calls_into_a_leg()

    assert len(calls) == 10
    for call in calls:
        assert "memory_task" in [keyword.arg for keyword in call.keywords], call.lineno


def test_nothing_in_a_leg_starts_a_run():
    source = Path(orch.__file__).read_text(encoding="utf-8")

    assert source.count("_start_memory_writer(") == 2  # The definition, and the one call.
    assert source.count("schedule_memory_writer(") == 1


# --------------------------------------------------------------------------- #
# Masking: real in the DB, fake in every prompt
# --------------------------------------------------------------------------- #


def test_stored_text_is_real_and_prompts_carry_fakes(
    flag_on, codec, seen_route, memory_agent, fake_writer, monkeypatch
):
    monkeypatch.setattr(orch, "_load_recent_messages", _REAL_RECENT_MESSAGES)
    rows = [
        _user("old"), _question("ok?"), _answer(f"the id is {REAL_ID}"), _user("now"),
        _user(f"the new id is {REAL_ID}"), _assistant(""),
    ]
    db = _context_db(rows, **{TABLE: [_row(content=f"old id {REAL_ID}")]})
    ctx = rctx.load_router_context(db, USER, CONVO, None)

    assert REAL_ID not in ctx.conversation_memory_md
    assert REAL_ID not in repr(ctx.message_history)
    assert FAKE_ID in repr(ctx.message_history)

    memory_agent.ops = MemoryOps(add=[MemoryAddOp(kind="fact", content=f"new id {FAKE_ID}")])
    script = _writing()
    _run_turn(db, script, question=f"the new id is {REAL_ID}", codec=codec)

    instructions = script.infos[0].instructions
    assert FAKE_ID in instructions
    assert REAL_ID not in instructions

    sent = memory_agent.inputs[0]
    assert sent.user_message == f"the new id is {FAKE_ID}"
    assert FAKE_ID in sent.recent_messages[2].content
    assert FAKE_ID in sent.entries[0].content
    assert REAL_ID not in repr(sent)
    assert db.contents() == [f"old id {REAL_ID}", f"new id {REAL_ID}"]

    block = seen_route["input"].conversation_memory_md
    assert REAL_ID not in block
    assert f"new id {FAKE_ID}" in block


def test_a_run_that_outlives_the_turn_still_stores_real_text(
    flag_on, codec, seen_route, memory_agent
):
    """The turn resets its codec on the way out. The task copied its context."""
    memory_agent.gate = asyncio.Event()
    memory_agent.ops = MemoryOps(add=[MemoryAddOp(kind="fact", content=f"new id {FAKE_ID}")])

    def _turn_ends() -> None:
        masking.set_active_codec(None)
        memory_agent.gate.set()

    db = _empty_turn()
    _run_turn(db, _chatting(), when_done=_turn_ends)

    assert db.contents() == [f"new id {REAL_ID}"]


# --------------------------------------------------------------------------- #
# The editor
# --------------------------------------------------------------------------- #


def _edit_call() -> ToolCallPart:
    return ToolCallPart(tool_name="edit_artifact", args={"target_wi": "WI-3", "task": "fix it"})


@pytest.fixture
def fake_editor(monkeypatch):
    captured: dict[str, Any] = {}

    async def _run(_supabase: Any, **kwargs: Any) -> EditorResult:
        captured.update(kwargs)
        return EditorResult(status="edited", change_summary="Changed one place.")

    monkeypatch.setattr(agents.artifact_editor, "run_artifact_editor", _run)
    return captured


_ITEMS = [{"item_id": "item-3", "wi_seq": 3, "kind": "agent_writing", "title": "t", "summary": "s"}]


def test_edit_artifact_hands_the_editor_the_encoded_block(flag_on, fake_editor):
    entries = [_entry(1, f"id {FAKE_ID}")]
    _run_router(
        FakeDB(), _Script(_response(_edit_call()), _response(_chat())),
        conversation_memory=entries, workspace_item_summaries=_ITEMS,
    )

    assert fake_editor["conversation_memory_md"] == render_conversation_memory(entries)


def test_edit_artifact_passes_nothing_without_a_memory(flag_on, fake_editor):
    _run_router(
        FakeDB(), _Script(_response(_edit_call()), _response(_chat())),
        workspace_item_summaries=_ITEMS,
    )

    assert "conversation_memory_md" not in fake_editor
    assert fake_editor["item_id"] == "item-3"


def _editor_deps(**over: Any) -> EditorDeps:
    base: dict[str, Any] = dict(
        supabase=None, item_id="item-3", artifact_title="Title",
        artifact_content_md="Body", user_message="fix it", task="fix it",
    )
    base.update(over)
    return EditorDeps(**base)


def test_editor_prompt_is_unchanged_without_a_memory():
    assert inject_artifact(SimpleNamespace(deps=_editor_deps())) == (
        "\n## The target document\n"
        "item_id: item-3\n"
        "Title: Title\n\n"
        "The full document content (quote from it verbatim):\n\n"
        "````markdown\nBody\n````\n\n"
        "## Task scope from the router (the user's own words for this document)\n"
        "fix it\n"
    )


def test_editor_prompt_adds_the_block_as_context_only_after_the_task():
    block = render_conversation_memory([_entry()])
    bare = inject_artifact(SimpleNamespace(deps=_editor_deps()))
    text = inject_artifact(SimpleNamespace(deps=_editor_deps(conversation_memory_md=block)))

    assert text.startswith(bare)
    assert "Conversation memory (context only)" in text
    assert "It is not a request to edit." in text
    assert "stays exactly as it is" in text
    assert text.index(editor_mod._MEMORY_CONTEXT_NOTE) < text.index(block)


def test_editor_static_limit_still_stands():
    assert "Do not add content beyond the request." in " ".join(editor_mod.SYSTEM_PROMPT.split())


def test_editor_verbose_payload_omits_the_memory():
    payload = _full_snapshot(_editor_deps(conversation_memory_md="SECRET-ENTRY"))

    assert "SECRET-ENTRY" not in repr(payload)
    assert "conversation_memory_md" not in payload
    assert "supabase" not in payload
    assert payload["task"] == "fix it"
    assert payload["artifact_content_md"] == "Body"


def test_responder_verbose_payload_omits_the_memory_and_keeps_the_context():
    block = render_conversation_memory([_entry(1, "SECRET-ENTRY")])
    payload = _full_snapshot(ResponderDeps(
        mode="deep_search", conversation_id=CONVO, context_block=f"{block}\nNOTE\n\nTHE-CONTEXT",
    ))

    assert "SECRET-ENTRY" not in repr(payload)
    assert "<conversation_memory>" not in repr(payload)
    assert "THE-CONTEXT" in payload["context_block"]
    assert payload["mode"] == "deep_search"


def test_responder_verbose_payload_is_the_reflective_dump_without_a_memory():
    deps = ResponderDeps(mode="writing", context_block="THE-CONTEXT", doc_labels=("D1",))

    assert _full_snapshot(deps) == {
        "mode": "writing", "conversation_id": "", "case_id": None,
        "context_block": "THE-CONTEXT", "wi_alias_map": {},
        "doc_labels": ["D1"], "suppress_next_steps": False,
    }


def test_run_artifact_editor_puts_the_block_on_its_deps(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_tracked(_agent: Any, _prompt: Any, *, deps: EditorDeps, **_k: Any) -> Any:
        captured["deps"] = deps
        return SimpleNamespace(output=EditorResult(status="no_change", change_summary="x"))

    monkeypatch.setattr(editor_mod, "run_tracked", fake_tracked)
    db = FakeDB(workspace_items=[{
        "item_id": "item-3", "user_id": USER, "conversation_id": CONVO,
        "kind": "agent_writing", "title": "t", "content_md": "body",
    }])
    asyncio.run(editor_mod.run_artifact_editor(
        db, item_id="item-3", user_message="m", task="t", user_id=USER,
        conversation_id=CONVO, conversation_memory_md="THE-BLOCK",
    ))

    assert captured["deps"].conversation_memory_md == "THE-BLOCK"


# --------------------------------------------------------------------------- #
# Deletion
# --------------------------------------------------------------------------- #


_REPO = Path(__file__).resolve().parents[2]


def _migration() -> str:
    text = (_REPO / "shared" / "db" / "migrations" / "200_conversation_memory.sql").read_text(
        encoding="utf-8"
    )
    return " ".join(text.split())


def test_the_migration_deletes_entries_when_a_conversation_is_soft_deleted():
    sql = _migration()

    assert (
        "DROP TRIGGER IF EXISTS trg_conversations_delete_memory ON public.conversations; "
        "CREATE TRIGGER trg_conversations_delete_memory "
        "AFTER UPDATE OF deleted_at ON public.conversations FOR EACH ROW "
        "WHEN (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL) "
        "EXECUTE FUNCTION public.delete_conversation_memory_on_soft_delete();"
    ) in sql
    assert (
        "DELETE FROM public.conversation_memory_entries "
        "WHERE conversation_id = NEW.conversation_id;"
    ) in sql


def test_the_trigger_function_runs_as_definer_and_clients_cannot_call_it():
    """Clients can update conversations and have no grant on the memory table."""
    sql = _migration()

    assert (
        "CREATE OR REPLACE FUNCTION public.delete_conversation_memory_on_soft_delete() "
        "RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public'"
    ) in sql
    assert (
        "REVOKE EXECUTE ON FUNCTION public.delete_conversation_memory_on_soft_delete() "
        "FROM PUBLIC, anon, authenticated;"
    ) in sql


def test_the_self_check_covers_the_trigger():
    sql = _migration()

    assert sql.index("CREATE TRIGGER trg_conversations_delete_memory") < sql.index("DO $$")
    assert "t.tgname = 'trg_conversations_delete_memory'" in sql.split("DO $$")[1]


def test_the_migration_fails_fast_on_the_conversations_lock():
    assert "BEGIN; SET LOCAL lock_timeout = '5s';" in _migration()


def test_delete_conversation_leaves_the_memory_to_the_trigger():
    assert "conversation_memory" not in inspect.getsource(conversation_service)


def test_nuke_script_lists_the_table_before_its_parent():
    source = (_REPO / "scripts" / "nuke_account.py").read_text(encoding="utf-8")

    assert source.index('("conversation_memory_entries", "user_id")') < source.index(
        '("conversations", "user_id")'
    )
