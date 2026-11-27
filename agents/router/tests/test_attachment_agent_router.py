"""Router side of the attachment agent (.claude/plans/attachment_agent.md §2.1).

* ``ask_attachment_agent`` is shown only while the flag is on for the user (and
  never on the talk-only hand-back run); it resolves WI-n aliases to the user's
  uploaded files only, passes the user's message word for word, runs once per
  message, and never raises;
* ``unfold_workspace_item`` on an uploaded file returns one line pointing to the
  agent — router deps with the flag on only; reports and planner-style deps are
  unchanged;
* the «own documents» section is chosen per run: flag off = today's text, word
  for word; flag on = the attachment-agent section;
* ``list_workspace_items`` no longer labels an attachment ``agent_search``.

Offline; ``asyncio.run`` (CI has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import agents.attachment_agent.runner as aa_runner
import agents.orchestrator as orch
import agents.router.router as rr
from agents.attachment_agent.models import (
    AttachmentAgentCall,
    AttachmentAgentResult,
    PointOut,
    QuoteOut,
)
from agents.attachment_agent.render import render_for_router
from agents.models import ChatResponse
from agents.tool_repository import attachment_agent_tool as aat
from agents.tool_repository import unfold_workspace_item as uwi

USER = "22222222-2222-2222-2222-222222222222"
CONVO = "33333333-3333-3333-3333-333333333333"
MSG = "44444444-4444-4444-4444-444444444444"
FILE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"   # WI-1, attachment
REPORT = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"   # WI-2, agent_search
FILE_B = "cccccccc-cccc-cccc-cccc-cccccccccccc"   # WI-3, attachment
STRAY = "dddddddd-dddd-dddd-dddd-dddddddddddd"    # an attachment on no card

USER_MESSAGE = "وين قال الخبير إن المدة المتبقية 142 يوم؟ والاسم الصحيح محمود مو محمد"

CARDS = [
    {"item_id": FILE_A, "wi_seq": 1, "kind": "attachment", "title": "تقرير الخبير", "summary": "…"},
    {"item_id": REPORT, "wi_seq": 2, "kind": "agent_search", "title": "بحث", "summary": "…"},
    {"item_id": FILE_B, "wi_seq": 3, "kind": "attachment", "title": "العقد", "summary": "…"},
]
ALIASES = {1: FILE_A, 2: REPORT, 3: FILE_B}


def _router_deps(**over: Any) -> rr.RouterDeps:
    base: dict[str, Any] = dict(
        supabase=None, user_id=USER, conversation_id=CONVO, case_id=None,
        case_memory_md=None, case_metadata=None, user_preferences=None,
        workspace_item_summaries=[dict(c) for c in CARDS], wi_alias_map=dict(ALIASES),
        user_message=USER_MESSAGE, message_id=MSG, attachment_agent_on=True,
    )
    base.update(over)
    return rr.RouterDeps(**base)


@pytest.fixture
def agent_calls(monkeypatch) -> list[AttachmentAgentCall]:
    """The runner, faked: records each call and answers with one found quote."""
    calls: list[AttachmentAgentCall] = []

    async def _run(call: AttachmentAgentCall, *, supabase: Any) -> AttachmentAgentResult:
        calls.append(call)
        return FOUND

    monkeypatch.setattr(aa_runner, "run_attachment_agent", _run)
    return calls


FOUND = AttachmentAgentResult(
    outcome="found",
    points=[PointOut(
        statement="ذكر الخبير المدة المتبقية",
        quotes=[QuoteOut(
            text="المدة المتبقية 142 يوماً", item_id=FILE_A, file_title="تقرير الخبير",
            section_what="النتيجة", owner="الخبير", page=4,
        )],
    )],
)


# --------------------------------------------------------------------------- #
# Scripted router runs
# --------------------------------------------------------------------------- #


class _Script:
    def __init__(self, *responses: ModelResponse) -> None:
        self.responses = list(responses)
        self.infos: list[AgentInfo] = []
        self.messages: list[list] = []

    def respond(self, messages: list, info: AgentInfo) -> ModelResponse:
        self.infos.append(info)
        self.messages.append(list(messages))
        return self.responses.pop(0)

    def tools(self) -> set[str]:
        return {t.name for t in self.infos[0].function_tools}

    def tool_returns(self) -> list[str]:
        return [
            str(p.content) for p in self.messages[-1][-1].parts if isinstance(p, ToolReturnPart)
        ]


def _chat(text: str = "هذا رد كافٍ وطويل بما يكفي للاختبار.") -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(
        tool_name="final_result_ChatResponse", args={"type": "chat", "message": text},
    )])


def _call(tool: str, **args: Any) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=tool, args=args)])


def _run(script: _Script, deps: rr.RouterDeps) -> Any:
    async def _go() -> Any:
        with rr.router_agent.override(model=FunctionModel(script.respond)):
            return await rr.router_agent.run(USER_MESSAGE, deps=deps)

    return asyncio.run(_go())


# --------------------------------------------------------------------------- #
# The tool is shown per user
# --------------------------------------------------------------------------- #


def test_tool_is_hidden_while_the_flag_is_off():
    script = _Script(_chat())
    _run(script, _router_deps(attachment_agent_on=False))
    assert "ask_attachment_agent" not in script.tools()
    assert "unfold_workspace_item" in script.tools()


def test_tool_is_shown_when_the_flag_is_on():
    script = _Script(_chat())
    _run(script, _router_deps())
    assert "ask_attachment_agent" in script.tools()


def test_tool_is_hidden_on_the_hand_back_run():
    script = _Script(_chat())
    _run(script, _router_deps(planner_handback="عام جداً"))
    assert "ask_attachment_agent" not in script.tools()


def test_tool_schema_has_files_and_no_question_argument():
    script = _Script(_chat())
    _run(script, _router_deps())
    [tool] = [t for t in script.infos[0].function_tools if t.name == "ask_attachment_agent"]
    assert set(tool.parameters_json_schema["properties"]) == {"files"}
    assert "word for word" in tool.description


def test_router_deps_defaults_keep_todays_router():
    deps = rr.RouterDeps(
        supabase=None, user_id=USER, conversation_id=CONVO, case_id=None,
        case_memory_md=None, case_metadata=None, user_preferences=None,
    )
    assert deps.attachment_agent_on is False
    assert deps.message_id is None
    assert deps.attachment_agent_called is False


# --------------------------------------------------------------------------- #
# The tool, end to end through the router
# --------------------------------------------------------------------------- #


def test_router_call_passes_the_message_verbatim_and_renders_the_result(agent_calls):
    script = _Script(_call("ask_attachment_agent", files=["WI-1"]), _chat())
    _run(script, _router_deps())

    [call] = agent_calls
    assert call.user_message == USER_MESSAGE
    assert call.item_ids == [FILE_A]
    assert (call.user_id, call.conversation_id, call.message_id) == (USER, CONVO, MSG)
    assert script.tool_returns() == [render_for_router(FOUND)]


def test_a_second_call_in_the_same_run_runs_nothing(agent_calls):
    script = _Script(
        ModelResponse(parts=[
            ToolCallPart(tool_name="ask_attachment_agent", args={"files": ["WI-1"]}),
            ToolCallPart(tool_name="ask_attachment_agent", args={"files": ["WI-3"]}),
        ]),
        _chat(),
    )
    _run(script, _router_deps())
    assert len(agent_calls) == 1
    assert aat.ALREADY_CALLED_AR in script.tool_returns()


# --------------------------------------------------------------------------- #
# The tool body
# --------------------------------------------------------------------------- #


def _ask(deps: Any, files: list[str] | None) -> str:
    return asyncio.run(aat.ask_attachment_agent_for_deps(deps, files))


def test_aliases_resolve_to_the_users_files_only(agent_calls):
    out = _ask(_router_deps(), ["WI-3", "WI-2", "WI-9", "wi-1", "WI-3", STRAY])
    [call] = agent_calls
    assert call.item_ids == [FILE_B, FILE_A]       # report, unknown, stray dropped; deduped
    assert out == render_for_router(FOUND)


def test_only_non_files_given_returns_a_system_line(agent_calls):
    out = _ask(_router_deps(), ["WI-2", "WI-9"])
    assert agent_calls == []
    assert out.startswith("〔[نظام]") and "WI-2" in out and "WI-9" in out
    assert "ليست من ملفات المستخدم المرفوعة" in out


@pytest.mark.parametrize("files", [[], None, ["", "  "]])
def test_no_files_means_every_file(agent_calls, files):
    _ask(_router_deps(), files)
    [call] = agent_calls
    assert call.item_ids == []
    assert call.user_message == USER_MESSAGE


def test_a_conversation_without_uploads_runs_nothing(agent_calls):
    deps = _router_deps(workspace_item_summaries=[dict(CARDS[1])], wi_alias_map={2: REPORT})
    assert _ask(deps, []) == aat.NO_USER_FILES_AR
    assert agent_calls == []


def test_an_exception_becomes_a_system_line(monkeypatch):
    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("model down")

    monkeypatch.setattr(aa_runner, "run_attachment_agent", _boom)
    assert _ask(_router_deps(), ["WI-1"]) == aat.FAILED_AR


def test_the_error_outcome_is_rendered_for_the_router(monkeypatch):
    result = AttachmentAgentResult(outcome="error", note="انتهت المهلة.", error="timeout")

    async def _err(*_a: Any, **_k: Any) -> AttachmentAgentResult:
        return result

    monkeypatch.setattr(aa_runner, "run_attachment_agent", _err)
    out = _ask(_router_deps(), ["WI-1"])
    assert out == render_for_router(result)
    assert "timeout" not in out                     # internal error never shown


# --------------------------------------------------------------------------- #
# unfold_workspace_item
# --------------------------------------------------------------------------- #


class _Query:
    def __init__(self, rows: list[dict], log: list[str]) -> None:
        self._rows = list(rows)
        self._filters: list[Any] = []
        self._single = False
        self._log = log

    def select(self, cols: str) -> "_Query":
        self._log.append(cols)
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col: str, vals: list[Any]) -> "_Query":
        wanted = set(vals)
        self._filters.append(lambda r: r.get(col) in wanted)
        return self

    def is_(self, col: str, _val: str) -> "_Query":
        self._filters.append(lambda r: r.get(col) is None)
        return self

    def order(self, *_a: Any, **_k: Any) -> "_Query":
        return self

    def maybe_single(self) -> "_Query":
        self._single = True
        return self

    def execute(self) -> SimpleNamespace:
        rows = [r for r in self._rows if all(f(r) for f in self._filters)]
        if self._single:
            return SimpleNamespace(data=rows[0] if rows else None)
        return SimpleNamespace(data=rows)


FILE_TEXT = "نص تقرير الخبير: المدة المتبقية ١٤٢ يوماً."
REPORT_TEXT = "## النتيجة\n\nخلص البحث إلى أن المدة كافية."


def _db() -> tuple[Any, list[str]]:
    log: list[str] = []
    rows = [
        {"item_id": FILE_A, "user_id": USER, "deleted_at": None, "kind": "attachment",
         "title": "تقرير الخبير", "content_md": FILE_TEXT, "wi_seq": 1},
        {"item_id": REPORT, "user_id": USER, "deleted_at": None, "kind": "agent_search",
         "title": "بحث", "content_md": REPORT_TEXT, "wi_seq": 2},
        {"item_id": STRAY, "user_id": USER, "deleted_at": None, "kind": "attachment",
         "title": "ملف", "content_md": "نص ملف آخر", "wi_seq": None},
    ]
    tables = {"workspace_items": rows}
    return SimpleNamespace(table=lambda name: _Query(tables.get(name, []), log)), log


def _unfold(deps: Any, wi: str) -> str:
    return asyncio.run(uwi.unfold_for_deps(deps, wi))


def test_router_with_the_flag_does_not_unfold_an_attachment():
    db, log = _db()
    out = _unfold(_router_deps(supabase=db), "WI-1")
    assert out == uwi.ATTACHMENT_AGENT_UNFOLD_LINE
    assert out == "〔[نظام] ملفات المستخدم المرفوعة تُقرأ عبر ask_attachment_agent لا عبر هذه الأداة.〕"
    assert log == []                                # the kind came from the cards: no query


def test_router_with_the_flag_still_unfolds_our_own_output():
    db, _ = _db()
    out = _unfold(_router_deps(supabase=db), "WI-2")
    assert out == REPORT_TEXT


def test_a_raw_uuid_off_the_cards_is_checked_with_one_kind_read():
    db, log = _db()
    out = _unfold(_router_deps(supabase=db), STRAY)
    assert out == uwi.ATTACHMENT_AGENT_UNFOLD_LINE
    assert log == ["kind"]


def test_router_with_the_flag_off_unfolds_the_attachment_as_today():
    db, _ = _db()
    assert _unfold(_router_deps(supabase=db, attachment_agent_on=False), "WI-1") == FILE_TEXT


def test_planner_style_deps_unfold_the_attachment_in_full():
    db, _ = _db()
    planner = SimpleNamespace(supabase=db, user_id=USER, wi_alias_map=dict(ALIASES))
    assert _unfold(planner, "WI-1") == FILE_TEXT


def test_only_router_deps_declare_the_flag():
    import dataclasses

    from agents.deep_search_v4.planner.deps import PlannerDeps
    from agents.writer_planner.deps import WriterPlannerDeps

    def names(cls: Any) -> set[str]:
        if dataclasses.is_dataclass(cls):
            return {f.name for f in dataclasses.fields(cls)}
        return set(getattr(cls, "model_fields", {}) or vars(cls))

    assert "attachment_agent_on" in names(rr.RouterDeps)
    assert "attachment_agent_on" not in names(PlannerDeps)
    assert "attachment_agent_on" not in names(WriterPlannerDeps)


# --------------------------------------------------------------------------- #
# The «own documents» section
# --------------------------------------------------------------------------- #

# Today's section as it stood in SYSTEM_PROMPT at bdb812f — any change to its
# words changes the flag-off prompt and must be deliberate.
_OLD_SECTION_SHA256 = "a956f9c4829ed77ab1a6119189645af04a9ffce0b8cebaa5763b009a1853f745"
_OLD_HEADING = "## The conversation's own documents — find the item, open it, answer"
_NEW_HEADING = "## The conversation's own documents — find the file, ask the attachment agent, answer"


def _instructions(deps: rr.RouterDeps) -> str:
    script = _Script(_chat())
    _run(script, deps)
    return script.infos[0].instructions or ""


_BEFORE = "- Ambiguous messages — ask the user before routing\n\n"
_AFTER = "\n## Before searching again — CheckPriorReports\n"


def test_old_section_is_pinned_word_for_word():
    section = rr.OWN_DOCUMENTS_SECTION
    assert hashlib.sha256(section.encode("utf-8")).hexdigest() == _OLD_SECTION_SHA256
    assert section.startswith(_OLD_HEADING + "\n")
    assert section.endswith("a document from the legal library: route it.**\n")
    # Today's prompt holds it once, at its place; the agent prompt not at all.
    assert rr.SYSTEM_PROMPT.count(_OLD_HEADING) == 1
    assert _BEFORE + section + _AFTER in rr.SYSTEM_PROMPT
    assert _OLD_HEADING not in rr.SYSTEM_PROMPT_ATTACHMENT_AGENT


def test_the_two_prompts_differ_only_in_the_section():
    assert rr.SYSTEM_PROMPT_ATTACHMENT_AGENT == rr.SYSTEM_PROMPT.replace(
        rr.OWN_DOCUMENTS_SECTION, rr.OWN_DOCUMENTS_SECTION_AGENT
    )
    assert _BEFORE + rr.OWN_DOCUMENTS_SECTION_AGENT + _AFTER in rr.SYSTEM_PROMPT_ATTACHMENT_AGENT
    assert "ask_attachment_agent" not in rr.SYSTEM_PROMPT


def test_flag_off_gets_todays_prompt_then_the_date_line():
    text = _instructions(_router_deps(attachment_agent_on=False))
    assert text.startswith(f"{rr.SYSTEM_PROMPT.strip()}\n\n{rr.inject_today()}")
    assert _NEW_HEADING not in text and "ask_attachment_agent" not in text


def test_flag_on_gets_the_agent_prompt_then_the_date_line():
    text = _instructions(_router_deps())
    assert text.startswith(f"{rr.SYSTEM_PROMPT_ATTACHMENT_AGENT.strip()}\n\n{rr.inject_today()}")
    assert _OLD_HEADING not in text


def test_agent_section_says_what_the_owner_asked():
    s = rr.OWN_DOCUMENTS_SECTION_AGENT
    # "open it yourself" elsewhere in the prompt is redefined, not contradicted.
    assert '"Open it yourself" in this prompt' in s
    assert "the user's uploaded files (`kind=attachment`) → call `ask_attachment_agent`" in s
    # The vague-pointer rule stays: ask, call nothing.
    assert "«وين مكتوب هذا الكلام؟»" in s and "without calling or opening anything" in s
    # One call; answer only from it; never a location it did not return.
    assert "One call per user message" in s
    assert "Never give a location or a quote it did not return." in s
    assert "It asks a question → ask the user that question" in s
    assert "tell the user plainly what changed" in s
    # Changes and re-analysis go to the agent; a search still routes; library → simple_search.
    assert "A change to a file goes to the attachment agent too." in s
    assert "A search about what a file says still routes as before." in s
    assert "A **library** source stays with simple_search" in s


# --------------------------------------------------------------------------- #
# run_router / _route plumbing
# --------------------------------------------------------------------------- #


class _Span:
    def __enter__(self) -> "_Span":
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False

    def record_run(self, *_a: Any, **_k: Any) -> None:
        pass

    def set(self, **_k: Any) -> None:
        pass

    def set_outcome(self, *_a: Any) -> None:
        pass


def test_run_router_puts_the_flag_and_message_id_on_router_deps(monkeypatch):
    seen: list[rr.RouterDeps] = []

    async def _fake_run(_q: str, *, deps: rr.RouterDeps, **_k: Any) -> Any:
        seen.append(deps)
        return SimpleNamespace(
            output=ChatResponse(message="رد قصير كافٍ للاختبار هنا."),
            usage=lambda: SimpleNamespace(requests=1, output_tokens=1),
        )

    monkeypatch.setattr(rr.router_agent, "run", _fake_run)
    monkeypatch.setattr(rr, "track_stage", lambda *_a, **_k: _Span())
    common = dict(
        question="س", supabase=None, user_id=USER, conversation_id=CONVO, case_id=None,
        case_memory_md=None, case_metadata=None, user_preferences=None, message_history=[],
    )
    asyncio.run(rr.run_router(**common, attachment_agent_on=True, message_id=MSG))
    asyncio.run(rr.run_router(**common))
    assert (seen[0].attachment_agent_on, seen[0].message_id) == (True, MSG)
    assert (seen[1].attachment_agent_on, seen[1].message_id) == (False, None)


@pytest.mark.parametrize(
    ("enabled", "users", "expected"),
    [(False, None, False), (True, None, True), (True, USER, True), (True, "someone-else", False)],
)
def test_route_resolves_the_flag_per_user(monkeypatch, enabled, users, expected):
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", enabled)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", users)

    seen: dict[str, Any] = {}

    async def _spy(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return rr.RouterRunResult(output=ChatResponse(message="رد"))

    async def _no_lock(*_a: Any, **_k: Any) -> None:
        return None

    ctx = SimpleNamespace(
        case_memory_md=None, case_metadata=None, user_preferences=None, user_call_name=None,
        message_history=[], workspace_item_summaries=[], compaction_summary_md=None,
        conversation_memory=[],
    )
    monkeypatch.setattr("agents.router.router.run_router", _spy)
    monkeypatch.setattr("agents.router.context.load_router_context", lambda *_a, **_k: ctx)
    monkeypatch.setattr("shared.workspace.preview_lock.resolve_lock_policy", _no_lock)
    monkeypatch.setattr("agents.utils.welcome.resolve_welcome", lambda *_a, **_k: None)
    monkeypatch.setattr("agents.utils.welcome.mark_welcomed", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "backend.app.services.masking_service.persist_new_mappings", lambda *_a, **_k: None
    )
    monkeypatch.setattr(orch, "_router_extended_view", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "_record_follow_up_status", lambda *_a, **_k: None)

    async def _collect() -> list[dict]:
        return [ev async for ev in orch._route(
            question=USER_MESSAGE, supabase=object(), user_id=USER, conversation_id=CONVO,
            case_id=None, user_message_id=MSG,
        )]

    events = asyncio.run(_collect())
    assert events[-1]["type"] == "done"
    assert seen["attachment_agent_on"] is expected
    assert seen["message_id"] == MSG
    assert seen["question"] == USER_MESSAGE


def test_the_hand_back_run_keeps_todays_router_even_with_the_flag_on(monkeypatch):
    """The talk-only hand-back run hides ``ask_attachment_agent``; with the agent's
    section and the unfold redirect it could not read any uploaded file, so it runs
    with ``attachment_agent_on=False`` (today's section, today's unfold)."""
    from shared.config import get_settings

    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_ENABLED", True)
    monkeypatch.setattr(get_settings(), "ATTACHMENT_AGENT_USERS", None)
    seen: dict[str, Any] = {}

    async def _spy(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return rr.RouterRunResult(output=ChatResponse(message="ما الذي تريد البحث عنه تحديداً؟"))

    async def _no_lock(*_a: Any, **_k: Any) -> None:
        return None

    ctx = SimpleNamespace(
        case_memory_md=None, case_metadata=None, user_preferences=None, user_call_name=None,
        message_history=[], workspace_item_summaries=[], compaction_summary_md=None,
        conversation_memory=[],
    )
    monkeypatch.setattr("agents.router.router.run_router", _spy)
    monkeypatch.setattr("shared.workspace.preview_lock.resolve_lock_policy", _no_lock)
    monkeypatch.setattr("agents.utils.welcome.mark_welcomed", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "backend.app.services.masking_service.persist_new_mappings", lambda *_a, **_k: None
    )

    async def _collect() -> list[dict]:
        out: list[dict] = []
        try:
            async for ev in orch._route_after_handback(
                question=USER_MESSAGE, handback_note="عام جداً", supabase=object(), user_id=USER,
                conversation_id=CONVO, case_id=None, router_context=ctx,
            ):
                out.append(ev)
        except Exception:  # noqa: BLE001 — only the router's arguments are pinned here
            pass
        return out

    asyncio.run(_collect())
    assert seen["planner_handback"] == "عام جداً"
    assert seen["attachment_agent_on"] is False


# --------------------------------------------------------------------------- #
# list_workspace_items kind_hint (independent of the flag)
# --------------------------------------------------------------------------- #


def test_list_workspace_items_gives_each_kind_its_own_hint():
    rows = [
        {"item_id": i, "wi_seq": n, "title": k, "kind": k, "metadata": {}, "created_at": "",
         "conversation_id": CONVO, "user_id": USER, "deleted_at": None}
        for n, (i, k) in enumerate(
            [("1", "attachment"), ("2", "references"), ("3", "agent_search"),
             ("4", "agent_writing"), ("5", "note"), ("6", "pinned_texts")], start=1,
        )
    ]

    class _Q(_Query):
        def limit(self, _n: int) -> "_Q":
            return self

    db = SimpleNamespace(table=lambda _name: _Q(rows, []))
    ctx = SimpleNamespace(deps=_router_deps(supabase=db))
    items = asyncio.run(rr.list_workspace_items(ctx))
    hints = {it["title"]: it["kind_hint"] for it in items}
    assert hints == {
        "attachment": "attachment",
        "references": "references",
        "agent_search": "agent_search",
        "agent_writing": "agent_writing",
        "note": "agent_writing",
        "pinned_texts": "pinned_texts",
    }
