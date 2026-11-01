"""Conversation memory: the router, the orchestrator and the editor (plan §4.4-§4.10, §8).

Offline: the DB is a small filter-evaluating fake, models are FunctionModel
scripts, masking uses a real ``PrivacyCodec``. No LLM, no network. Sync tests with
``asyncio.run``, because CI has no pytest-asyncio.

The router rules are pinned by SHA-256 of the plan's bytes. Re-capture the digest
deliberately, in the commit that changes the wording.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

import agents.artifact_editor
import agents.artifact_editor.agent as editor_mod
import agents.deep_search_v4.planner as planner_pkg
import agents.deep_search_v4.planner.agent as planner_agent_mod
import agents.orchestrator as orch
import agents.router.context as rctx
import agents.router.router as rr
import agents.simple_search.runner as ss_runner
import agents.tool_repository.conversation_memory as mem_tool
import agents.utils.welcome as welcome_mod
import backend.app.services.conversation_service as conversation_service
import backend.app.services.masking_service as masking
from agents.artifact_editor.agent import EditorDeps, EditorResult, inject_artifact
from agents.models import ChatResponse, DispatchAgent, MajorAgentInput, SpecialistResult
from agents.responder.agent import ResponderDeps
from agents.utils.conversation_memory import MemoryEntry, render_conversation_memory
from agents.utils.tracking import _full_snapshot
from agents.utils.usage_sink import collect_llm_calls

TABLE = "conversation_memory_entries"
USER = "user-A"
CONVO = "conv-A"

REAL_ID = "1098765432"
FAKE_ID = "1098111222"

AMOUNT = "The correct amount is 210,000; 310,000 is a misreading."
SHA_RULES = "4420d1368676238588d624587237566318de00b7a9a8adb095832de7769a7c43"


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


@pytest.fixture
def flag_on(monkeypatch):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "CONVERSATION_MEMORY_ENABLED", True)


@pytest.fixture
def flag_off(monkeypatch):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "CONVERSATION_MEMORY_ENABLED", False)


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


def _dispatch(family: str) -> ToolCallPart:
    return ToolCallPart(
        tool_name="final_result_DispatchAgent",
        args={
            "type": "dispatch", "agent_family": family,
            "task_label": "A subject long enough for the label",
            "target_wi": None, "attached_wis": [], "subtype": None,
        },
    )


def _memory(**ops: Any) -> ToolCallPart:
    return ToolCallPart(tool_name="update_conversation_memory", args=ops)


def _add(content: str = AMOUNT, kind: str = "fact") -> list[dict]:
    return [{"kind": kind, "content": content}]


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
# The tool
# --------------------------------------------------------------------------- #


def test_tool_batched_with_output_in_one_response_still_executes(flag_on):
    """``end_strategy="exhaustive"`` is what runs it, as for a batched save_memo."""
    db = FakeDB()
    script = _Script(_response(_memory(add=_add()), _chat()))
    result = _run_router(db, script)

    assert len(script.infos) == 1
    assert isinstance(result.output, ChatResponse)
    assert db.contents() == [AMOUNT]
    assert AMOUNT in result.conversation_memory_md


def test_tool_batched_with_dispatch_carries_the_entry_on_the_result(flag_on):
    db = FakeDB()
    result = _run_router(db, _Script(_response(_memory(add=_add()), _dispatch("writing"))))

    assert isinstance(result.output, DispatchAgent)
    assert AMOUNT in result.conversation_memory_md


def test_a_db_failure_returns_a_string_and_does_not_block_the_turn(flag_on):
    db = FakeDB()
    db.fail[TABLE] = RuntimeError("connection reset")
    script = _Script(_response(_memory(add=_add())), _response(_chat("The answer itself.")))
    result = _run_router(db, script)

    assert result.output.message == "The answer itself."
    assert "Do not retry" in script.tool_returns()[0]


def test_an_invalid_op_is_a_plain_message_not_a_spent_retry(flag_on):
    """A strict schema would raise ModelRetry here and draw on the tool's retries."""
    db = FakeDB()
    script = _Script(
        _response(_memory(add=[{"kind": "opinion", "content": "x"}])),
        _response(_chat()),
    )
    _run_router(db, script)

    assert "Fix this and call again" in script.tool_returns()[0]
    assert db.contents() == []
    assert len(script.infos) == 2  # the model read it, so no repair follows


def test_an_update_and_a_remove_reach_the_rows_and_the_block(flag_on):
    db = FakeDB(**{TABLE: [_row(1, content="wrong"), _row(2, content="stale")]})
    entries = [_entry(1, "wrong"), _entry(2, "stale")]
    script = _Script(
        _response(_memory(update=[{"id": "M-1", "content": AMOUNT}], remove=["M-2"]), _chat())
    )
    result = _run_router(db, script, conversation_memory=entries)

    assert db.contents() == [AMOUNT]
    assert AMOUNT in result.conversation_memory_md
    assert "stale" not in result.conversation_memory_md


def test_the_callers_entry_list_is_not_mutated(flag_on):
    entries = [_entry(1, "kept")]
    db = FakeDB(**{TABLE: [_row(1, content="kept")]})
    _run_router(db, _Script(_response(_memory(add=_add()), _chat())), conversation_memory=entries)

    assert [entry.content for entry in entries] == ["kept"]


def test_tool_telemetry_is_counts_only(flag_on, monkeypatch, caplog):
    secret = "SECRET-ENTRY-TEXT"
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        mem_tool, "_logfire",
        SimpleNamespace(info=lambda name, **attrs: events.append((name, attrs))),
    )
    with caplog.at_level(logging.INFO):
        _run_router(FakeDB(), _Script(_response(_memory(add=_add(secret)), _chat())))

    assert events == [(
        "router.conversation_memory_update",
        {
            "conversation_id": CONVO, "added": 1, "updated": 0, "removed": 0,
            "failed": False, "entry_count": 1,
        },
    )]
    assert secret not in caplog.text


def test_two_memory_calls_in_one_response_do_not_lose_the_earlier_one(flag_on):
    db = FakeDB()
    script = _Script(_response(_memory(add=_add("first")), _memory(add=_add("second")), _chat()))
    result = _run_router(db, script)

    assert db.contents() == ["first", "second"]
    assert "first" in result.conversation_memory_md
    assert "second" in result.conversation_memory_md


def test_a_ninth_tool_call_in_a_turn_keeps_the_answer(flag_on):
    calls = [ToolCallPart(tool_name="list_workspace_items", args={}) for _ in range(8)]
    db = FakeDB()
    result = _run_router(db, _Script(_response(*calls, _memory(add=_add()), _chat("The answer."))))

    assert rr.ROUTER_LIMITS.tool_calls_limit == 9
    assert result.output.message == "The answer."
    assert db.contents() == [AMOUNT]


# --------------------------------------------------------------------------- #
# Repair: a rejected update batched with the final output
# --------------------------------------------------------------------------- #


_BAD_CALLS = [
    pytest.param(lambda: _memory(add=[{"kind": "opinion", "content": "x"}]), id="bad-kind"),
    pytest.param(lambda: _memory(update=[{"id": "M-9", "content": "x"}]), id="bad-alias"),
    pytest.param(lambda: _memory(add=_add("x" * 1501)), id="over-the-limit"),
    pytest.param(lambda: _memory(add="not a list"), id="malformed-args"),
]


@pytest.mark.parametrize("bad_call", _BAD_CALLS)
def test_a_batched_rejection_gets_exactly_one_repair(flag_on, bad_call):
    db = FakeDB()
    script = _Script(
        _response(bad_call(), _chat("The original answer.")),
        _response(_memory(add=_add()), _chat("A different answer.")),
    )
    result = _run_router(db, script)

    assert len(script.infos) == 2
    assert db.contents() == [AMOUNT]
    assert result.output.message == "The original answer."
    assert AMOUNT in result.conversation_memory_md


@pytest.mark.parametrize(
    ("bad_call", "named"),
    [
        (lambda: _memory(add=[{"kind": "opinion", "content": "x"}]), "kind must be one of"),
        (lambda: _memory(add="not a list"), "did not match the tool schema"),
    ],
)
def test_the_repair_prompt_names_the_failure_and_keeps_the_history(flag_on, bad_call, named):
    script = _Script(
        _response(bad_call(), _chat()),
        _response(_memory(add=_add()), _chat("done")),
    )
    _run_router(FakeDB(), script)

    sent = script.messages[1]
    prompts = [
        part.content for message in sent for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert prompts[0] == "The amount is 210,000."
    assert "update_conversation_memory" in prompts[-1]
    assert named in prompts[-1]
    assert any(isinstance(message, ModelResponse) for message in sent)


def test_a_dispatch_is_kept_when_the_repair_ends_on_a_chat(flag_on):
    db = FakeDB()
    script = _Script(
        _response(_memory(add=[{"kind": "opinion", "content": "x"}]), _dispatch("writing")),
        _response(_memory(add=_add()), _chat("done")),
    )
    result = _run_router(db, script)

    assert isinstance(result.output, DispatchAgent)
    assert result.output.agent_family == "writing"
    assert AMOUNT in result.conversation_memory_md


def test_the_repair_may_send_the_tool_and_the_output_apart(flag_on):
    db = FakeDB()
    script = _Script(
        _response(_memory(add=[{"kind": "opinion", "content": "x"}]), _chat("The original.")),
        _response(_memory(add=_add())),
        _response(_chat("done")),
    )
    result = _run_router(db, script)

    assert len(script.infos) == 3
    assert result.output.message == "The original."
    assert db.contents() == [AMOUNT]


def test_the_memory_tool_is_the_only_tool_in_a_repair(flag_on):
    script = _Script(
        _response(_memory(add=[{"kind": "opinion", "content": "x"}]), _chat()),
        _response(_memory(add=_add()), _chat("done")),
    )
    _run_router(FakeDB(), script)

    assert len(script.infos[0].function_tools) > 1
    assert [tool.name for tool in script.infos[1].function_tools] == ["update_conversation_memory"]
    assert {tool.name for tool in script.infos[1].output_tools} == {
        tool.name for tool in script.infos[0].output_tools
    }
    registered = next(
        tool for tool in script.infos[0].function_tools
        if tool.name == "update_conversation_memory"
    )
    assert script.infos[1].function_tools[0] == registered


def test_the_restriction_ends_with_the_repair(flag_on):
    _run_router(
        FakeDB(),
        _Script(
            _response(_memory(add=[{"kind": "opinion", "content": "x"}]), _chat()),
            _response(_memory(add=_add()), _chat("done")),
        ),
    )
    script = _Script(_response(_chat()))
    _run_router(FakeDB(), script)

    assert "edit_artifact" in [tool.name for tool in script.infos[0].function_tools]


def test_a_repair_cannot_run_any_other_tool(flag_on, fake_editor):
    """An edit in a repair would be a real change no reply ever mentions."""
    script = _Script(
        _response(_memory(add=[{"kind": "opinion", "content": "x"}]), _chat("The original.")),
        _response(_edit_call(), _chat("done")),
        _response(_chat("done")),
    )
    result = _run_router(FakeDB(), script, workspace_item_summaries=_ITEMS)

    assert fake_editor == {}
    assert result.sse_events == []
    assert result.force_attach_item_ids == []
    assert result.output.message == "The original."


def test_a_clean_batched_update_makes_no_extra_call(flag_on):
    script = _Script(_response(_memory(add=_add()), _chat()))
    _run_router(FakeDB(), script)

    assert len(script.infos) == 1


def test_a_batched_db_error_does_not_trigger_a_repair(flag_on):
    db = FakeDB()
    db.fail[TABLE] = RuntimeError("connection reset")
    script = _Script(_response(_memory(add=_add()), _chat("The answer.")))
    result = _run_router(db, script)

    assert len(script.infos) == 1
    assert result.output.message == "The answer."


def test_a_failure_the_model_fixed_itself_needs_no_repair(flag_on):
    db = FakeDB()
    script = _Script(
        _response(_memory(add=[{"kind": "opinion", "content": "x"}])),
        _response(_memory(add=_add()), _chat()),
    )
    _run_router(db, script)

    assert len(script.infos) == 2
    assert db.contents() == [AMOUNT]


def test_malformed_args_the_model_retried_need_no_repair(flag_on):
    db = FakeDB()
    script = _Script(
        _response(_memory(add="not a list")),
        _response(_memory(add=_add()), _chat()),
    )
    _run_router(db, script)

    assert len(script.infos) == 2
    assert any(isinstance(part, RetryPromptPart) for part in script.messages[1][-1].parts)


def test_one_bad_call_beside_a_good_one_is_still_repaired(flag_on):
    db = FakeDB()
    script = _Script(
        _response(
            _memory(add=_add("kept")),
            _memory(add=[{"kind": "opinion", "content": "x"}]),
            _chat("The original."),
        ),
        _response(_memory(add=_add("fixed")), _chat("done")),
    )
    result = _run_router(db, script)

    assert len(script.infos) == 2
    assert db.contents() == ["kept", "fixed"]
    assert "kept" in result.conversation_memory_md
    assert "fixed" in result.conversation_memory_md


def test_a_repair_exception_leaves_the_turn_alone(flag_on, caplog):
    secret = "SECRET-IN-THE-ERROR"
    db = FakeDB()
    script = _Script(
        _response(_memory(add=[{"kind": "opinion", "content": "x"}]), _chat("The original.")),
        RuntimeError(secret),
    )
    with caplog.at_level(logging.WARNING):
        result = _run_router(db, script)

    assert result.output.message == "The original."
    assert result.conversation_memory_md is None
    assert "conversation memory repair failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


def test_a_second_rejection_is_logged_and_never_loops(flag_on, caplog):
    bad = {"kind": "opinion", "content": "SECRET-ENTRY"}
    db = FakeDB()
    script = _Script(
        _response(_memory(add=[bad]), _chat("The original.")),
        _response(_memory(add=[bad]), _chat("done")),
    )
    with caplog.at_level(logging.WARNING):
        result = _run_router(db, script)

    assert len(script.infos) == 2
    assert result.output.message == "The original."
    assert db.contents() == []
    assert "conversation memory repair changed nothing" in caplog.text
    assert "SECRET-ENTRY" not in caplog.text


def test_a_repair_that_ignores_the_tool_is_logged(flag_on, caplog):
    script = _Script(
        _response(_memory(add=[{"kind": "opinion", "content": "x"}]), _chat("The original.")),
        _response(_chat("done")),
    )
    with caplog.at_level(logging.WARNING):
        result = _run_router(FakeDB(), script)

    assert result.output.message == "The original."
    assert "conversation memory repair changed nothing" in caplog.text


def test_the_repair_writes_a_ledger_row_of_its_own(flag_on):
    db = FakeDB()
    script = _Script(
        _response(_memory(add=[{"kind": "opinion", "content": "x"}]), _chat()),
        _response(_memory(add=_add()), _chat("done")),
    )

    async def _go() -> None:
        with collect_llm_calls(db, conversation_id=CONVO, user_id=USER):
            with rr.router_agent.override(model=FunctionModel(script.respond)):
                await rr.run_router(
                    question="q", supabase=db, user_id=USER, conversation_id=CONVO,
                    case_id=None, case_memory_md=None, case_metadata=None,
                    user_preferences=None, message_history=[],
                )

    asyncio.run(_go())

    assert sorted(row["agent"] for row in db.tables["llm_calls"]) == [
        "router.classify", "router.memory_repair",
    ]
    repair = next(r for r in db.tables["llm_calls"] if r["agent"] == "router.memory_repair")
    assert repair["agent_family"] == "router"
    assert repair["tokens_in"] > 0
    assert repair["requests"] == 1


def test_a_repair_stopped_by_its_limits_still_reaches_the_ledger(flag_on, caplog):
    """The second memory call passes ``tool_calls_limit``: tokens are already spent."""
    db = FakeDB()
    bad = {"kind": "opinion", "content": "x"}
    script = _Script(
        _response(_memory(add=[bad]), _chat("The original.")),
        _response(_memory(add=[bad])),
        _response(_memory(add=_add())),
    )

    async def _go() -> Any:
        with collect_llm_calls(db, conversation_id=CONVO, user_id=USER):
            with rr.router_agent.override(model=FunctionModel(script.respond)):
                return await rr.run_router(
                    question="q", supabase=db, user_id=USER, conversation_id=CONVO,
                    case_id=None, case_memory_md=None, case_metadata=None,
                    user_preferences=None, message_history=[],
                )

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(_go())

    assert result.output.message == "The original."
    assert "conversation memory repair failed" in caplog.text
    assert "UsageLimitExceeded" in caplog.text
    assert db.contents() == []
    repair = next(r for r in db.tables["llm_calls"] if r["agent"] == "router.memory_repair")
    assert repair["requests"] == 2
    assert repair["tokens_in"] > 0


def test_unread_failure_needs_a_closing_request():
    deps = _deps()

    assert rr._unread_memory_failure(deps, []) is None
    assert rr._unread_memory_failure(deps, [_response(_chat())]) is None


def test_unread_failure_ignores_a_call_that_was_not_refused():
    part = ToolReturnPart(
        tool_name="update_conversation_memory", content="Memory updated.", tool_call_id="call-1",
    )

    assert rr._unread_memory_failure(_deps(), [ModelRequest(parts=[part])]) is None


def test_unread_failure_ignores_a_retry_for_another_tool():
    part = RetryPromptPart(content="bad", tool_name="edit_artifact", tool_call_id="call-1")

    assert rr._unread_memory_failure(_deps(), [ModelRequest(parts=[part])]) is None


# --------------------------------------------------------------------------- #
# Router prompt and result
# --------------------------------------------------------------------------- #


def test_rules_are_the_plans_text():
    assert hashlib.sha256(rr.CONVERSATION_MEMORY_RULES.encode("utf-8")).hexdigest() == SHA_RULES


def test_the_static_prompt_has_no_memory_route_section_and_no_memory_rules():
    assert "When to route to memory" not in rr.SYSTEM_PROMPT
    assert "## Questions about ريحان itself" in rr.SYSTEM_PROMPT
    assert "update_conversation_memory" not in rr.SYSTEM_PROMPT


def test_the_memory_family_stays_in_the_code():
    assert "memory" in DispatchAgent.model_fields["agent_family"].annotation.__args__
    assert callable(orch._run_memory)


def test_instruction_renders_rules_then_block(flag_on):
    deps = _deps(conversation_memory=[_entry()])
    text = rr.inject_conversation_memory(SimpleNamespace(deps=deps))

    block = render_conversation_memory([_entry()])
    assert rr.CONVERSATION_MEMORY_RULES in text
    assert block in text
    assert text.index(rr.CONVERSATION_MEMORY_RULES) < text.index(block)


def test_rules_show_on_a_conversation_with_no_memory(flag_on):
    text = rr.inject_conversation_memory(SimpleNamespace(deps=_deps()))

    assert text.strip() == rr.CONVERSATION_MEMORY_RULES.strip()


def test_the_block_is_rendered_once_with_no_second_escape(flag_on):
    entry = _entry(1, "a < b & c")
    text = rr.inject_conversation_memory(SimpleNamespace(deps=_deps(conversation_memory=[entry])))

    assert "a &lt; b &amp; c" in text
    assert "&amp;lt;" not in text


def test_an_ampersand_survives_a_router_edit_round_trip(flag_on):
    """The model copies entries out of the escaped block."""
    entries = [_entry(1, "A&B")]
    copied = render_conversation_memory(entries).split('">')[-1].split("</entry>")[0]
    db = FakeDB(**{TABLE: [_row(1, content="A&B")]})
    script = _Script(_response(_memory(update=[{"id": "M-1", "content": copied}]), _chat()))
    result = _run_router(db, script, conversation_memory=entries)

    assert copied == "A&amp;B"
    assert db.contents() == ["A&B"]
    assert "A&amp;B</entry>" in result.conversation_memory_md
    assert "&amp;amp;" not in result.conversation_memory_md


def test_a_later_request_in_the_run_sees_the_new_entry(flag_on):
    script = _Script(_response(_memory(add=_add())), _response(_chat()))
    _run_router(FakeDB(), script)

    assert AMOUNT not in script.infos[0].instructions
    assert AMOUNT in script.infos[1].instructions


def test_error_fallback_keeps_the_block(flag_on):
    db = FakeDB()
    script = _Script(_response(_memory(add=_add())), RuntimeError("model down"))
    result = _run_router(db, script)

    assert db.contents() == [AMOUNT]
    assert isinstance(result.output, ChatResponse)
    assert AMOUNT in result.conversation_memory_md


def test_span_carries_the_entry_count_and_no_content(flag_on, monkeypatch):
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
    db = FakeDB(**{TABLE: [_row(1, content="SECRET-A"), _row(2, content="SECRET-B")]})
    _run_router(
        db, _Script(_response(_chat())),
        conversation_memory=[_entry(1, "SECRET-A"), _entry(2, "SECRET-B")],
    )

    assert seen["memory_entry_count"] == 2
    assert "SECRET" not in repr(seen)


# --------------------------------------------------------------------------- #
# Flag off
# --------------------------------------------------------------------------- #


def test_flag_off_hides_the_tool_and_adds_no_rules_or_block(flag_off):
    script = _Script(_response(_chat()))
    _run_router(FakeDB(), script, conversation_memory=[_entry()])

    info = script.infos[0]
    assert "update_conversation_memory" not in [tool.name for tool in info.function_tools]
    assert "Conversation memory." not in info.instructions
    assert "<conversation_memory>" not in info.instructions


def test_flag_on_shows_the_tool(flag_on):
    script = _Script(_response(_chat()))
    _run_router(FakeDB(), script)

    assert "update_conversation_memory" in [t.name for t in script.infos[0].function_tools]


def test_memory_comes_after_the_static_prefix_and_every_other_block(flag_on):
    script = _Script(_response(_chat()))
    _run_router(FakeDB(), script, compaction_summary_md="BACK-STORY")

    instructions = script.infos[0].instructions
    assert instructions.startswith(rr.SYSTEM_PROMPT.strip())
    assert instructions.index("BACK-STORY") < instructions.index(
        rr.CONVERSATION_MEMORY_RULES.strip()
    )
    assert instructions.rstrip().endswith(rr.CONVERSATION_MEMORY_RULES.strip())


def test_flag_off_instruction_is_empty(flag_off):
    deps = _deps(conversation_memory=[_entry()])

    assert rr.inject_conversation_memory(SimpleNamespace(deps=deps)) == ""


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
        conversations=[{"conversation_id": CONVO, "compacted_through_message_id": None}],
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
    """Quiet everything around ``_route`` and capture the turn's context."""
    seen: dict[str, Any] = {}
    real_load = rctx.load_router_context

    def _capture(*args: Any, **kwargs: Any) -> Any:
        seen["ctx"] = real_load(*args, **kwargs)
        return seen["ctx"]

    monkeypatch.setattr(rctx, "load_router_context", _capture)
    monkeypatch.setattr(welcome_mod, "resolve_welcome", lambda *a, **k: None)
    monkeypatch.setattr(welcome_mod, "mark_welcomed", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_count_artifact_kinds", lambda *a, **k: 0)
    monkeypatch.setattr(orch, "_load_recent_messages", lambda *a, **k: [])
    return seen


def _route(db: FakeDB, script: _Script) -> list[dict]:
    async def _go() -> list[dict]:
        with rr.router_agent.override(model=FunctionModel(script.respond)):
            return await _collect(orch._route(
                question="The amount is 210,000.", supabase=db, user_id=USER,
                conversation_id=CONVO, case_id=None,
            ))

    return asyncio.run(_go())


def test_same_turn_update_reaches_major_input_and_ctx(flag_on, seen_route, monkeypatch):
    async def fake_writer(major_input: MajorAgentInput, *_a: Any, **_k: Any) -> Any:
        seen_route["input"] = major_input
        seen_route["ctx_at_dispatch"] = seen_route["ctx"].conversation_memory_md
        return SimpleNamespace(kind="completed", result=_specialist_result())

    monkeypatch.setattr(orch, "_run_writer", fake_writer)
    db = _context_db([_user("hi"), _assistant("")])
    _route(db, _Script(_response(_memory(add=_add()), _dispatch("writing"))))

    assert AMOUNT in seen_route["input"].conversation_memory_md
    assert seen_route["ctx_at_dispatch"] == seen_route["input"].conversation_memory_md
    assert seen_route["input"].router_context is seen_route["ctx"]


def test_a_chat_reply_turn_also_updates_ctx(flag_on, seen_route):
    db = _context_db([_user("hi"), _assistant("")])
    _route(db, _Script(_response(_memory(add=_add()), _chat("Noted."))))

    assert AMOUNT in seen_route["ctx"].conversation_memory_md


def test_an_old_block_is_replaced_not_kept_after_a_removal(flag_on, seen_route, monkeypatch):
    async def fake_writer(major_input: MajorAgentInput, *_a: Any, **_k: Any) -> Any:
        seen_route["input"] = major_input
        return SimpleNamespace(kind="completed", result=_specialist_result())

    monkeypatch.setattr(orch, "_run_writer", fake_writer)
    db = _context_db([_user("hi"), _assistant("")], **{TABLE: [_row()]})
    _route(db, _Script(_response(_memory(remove=["M-1"]), _dispatch("writing"))))

    assert seen_route["input"].conversation_memory_md is None
    assert seen_route["ctx"].conversation_memory_md is None


def test_router_gets_the_loaded_memory_and_the_paused_reply(flag_on, seen_route):
    db = _context_db(_PAUSED_ROWS, **{TABLE: [_row()]})
    script = _Script(_response(_chat("Noted.")))
    _route(db, script)

    assert AMOUNT in script.infos[0].instructions
    assert "The amount is 210,000." in repr(script.messages[0])


def test_simple_search_dispatch_gets_the_block(flag_on, seen_route, monkeypatch):
    async def fake_run(*_a: Any, **kwargs: Any) -> _SSResult:
        seen_route["kwargs"] = kwargs
        return _SSResult(chat_messages=["The text of the article."])

    monkeypatch.setattr(orch, "run_simple_search", fake_run)
    db = _context_db([_user("hi"), _assistant("")])
    _route(db, _Script(_response(_memory(add=_add()), _dispatch("simple_search"))))

    assert AMOUNT in seen_route["kwargs"]["conversation_memory_md"]


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


def _pending(family: str) -> dict:
    return {
        "run_id": "run-1", "agent_family": family, "task_label": "label",
        "message_history": b"[]", "deferred_payload": {"tool_call_id": "call-1"},
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
# Masking: real in the DB, fake in every prompt
# --------------------------------------------------------------------------- #


def test_stored_text_is_real_and_prompts_carry_fakes(flag_on, codec):
    rows = [_user("old"), _question("ok?"), _answer(f"the id is {REAL_ID}"), _user("now")]
    db = _context_db(rows, **{TABLE: [_row(content=f"old id {REAL_ID}")]})
    ctx = rctx.load_router_context(db, USER, CONVO, None)

    assert REAL_ID not in ctx.conversation_memory_md
    assert REAL_ID not in repr(ctx.message_history)
    assert FAKE_ID in repr(ctx.message_history)

    script = _Script(_response(_memory(add=_add(f"new id {FAKE_ID}")), _chat()))
    result = _run_router(db, script, conversation_memory=ctx.conversation_memory)

    instructions = script.infos[0].instructions
    assert FAKE_ID in instructions
    assert REAL_ID not in instructions
    assert db.contents() == [f"old id {REAL_ID}", f"new id {REAL_ID}"]
    assert REAL_ID not in result.conversation_memory_md
    assert f"new id {FAKE_ID}" in result.conversation_memory_md


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


def test_edit_artifact_sees_an_update_batched_before_it(flag_on, fake_editor):
    script = _Script(_response(_memory(add=_add()), _edit_call()), _response(_chat()))
    _run_router(FakeDB(), script, workspace_item_summaries=_ITEMS)

    assert AMOUNT in fake_editor["conversation_memory_md"]


def test_edit_artifact_sees_an_update_batched_after_it(flag_on, fake_editor):
    script = _Script(_response(_edit_call(), _memory(add=_add())), _response(_chat()))
    _run_router(FakeDB(), script, workspace_item_summaries=_ITEMS)

    assert AMOUNT in fake_editor["conversation_memory_md"]


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
