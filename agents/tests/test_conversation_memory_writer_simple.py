"""Conversation memory in the writer and simple_search families.

Plan ``.claude/plans/conversation_memory.md`` §4.3, §5, §8. The block is rendered,
encoded and escaped once, upstream, so what these tests pin is the transport: it
reaches each agent exactly once, unchanged and whole, and a conversation with no
memory renders exactly what it did before the feature.

No LLM, no network. Agents run on ``FunctionModel``s patched in at their
``get_agent_model``. Sync on purpose: CI has no ``pytest-asyncio``. Self-contained
on purpose too: ``agents/simple_search/tests/`` is not tracked, so its harness is
not there in a clean clone.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.models import ChatMessageSnapshot, MajorAgentInput
from agents.simple_search import prompts as P
from agents.simple_search import runner as R
from agents.simple_search.compliance import (
    ComplianceOutput,
    ServiceCandidate,
    run_compliance,
)
from agents.simple_search.models import ResolvedObject, ResponderDocDigest, UnfoldResult
from agents.simple_search.publisher import SimpleSearchPublishResult
from agents.simple_search.responder import CardVerdict, ResponderOutput
from agents.simple_search.searcher import (
    SearcherDecision,
    SearcherDeps,
    create_searcher_agent,
)
from agents.simple_search.synthesizer import SynthesizerOutput
from agents.utils.conversation_memory import MemoryEntry, render_conversation_memory
from agents.utils.tracking import _bounded_snapshot, _full_snapshot
from agents.writer import agent as writer_agent
from agents.writer.deps import build_writer_deps
from agents.writer.models import (
    AnalyzedItem,
    WriterLLMOutput,
    WriterPackage,
    WriterSection,
    WriterStyle,
)
from agents.writer.prompts import get_writer_prompt
from agents.writer.runner import _handle_package_turn
from agents.writer_planner import prompts as WPP
from agents.writer_planner import runner as WR
from agents.writer_planner.deps import build_writer_planner_deps
from agents.writer_planner.models import PlannerDecision

REPO_ROOT = Path(__file__).resolve().parents[2]
RECENT = [ChatMessageSnapshot(role="user", content="سؤال سابق", created_at="2026-11-01T00:00:00Z")]
WELCOME = "WELCOME-BLOCK"


@pytest.fixture
def block() -> str:
    """A real block: markup in the content to catch a second escape, and long
    enough that any of the prompt clips (500, 2000 chars) would cut it."""
    rendered = render_conversation_memory([
        MemoryEntry(seq=1, kind="fact", content="المبلغ 210,000 <b>نهائي</b> & صحيح"),
        MemoryEntry(seq=2, kind="request", content="طلب " * 700),
        MemoryEntry(seq=3, kind="legal_position", content="رأي قانوني", insisted=True),
    ])
    assert rendered is not None and len(rendered) > 3000
    return rendered


def _assert_once(text: str, block: str) -> None:
    """Present exactly once, whole, and not escaped a second time."""
    assert text.count(block) == 1
    assert text.count("<conversation_memory>") == 1
    assert text.count("</conversation_memory>") == 1
    assert "&lt;b&gt;" in text
    assert "&amp;lt;" not in text


class _Scripted:
    """A ``FunctionModel`` that records what each call was given."""

    def __init__(self, step: Callable[[AgentInfo, int], ToolCallPart]) -> None:
        #: Per call: the system prompt.
        self.instructions: list[str] = []
        #: Per call: the system prompt plus the user prompts of the last request.
        self.seen: list[str] = []

        def fn(messages: list, info: AgentInfo) -> ModelResponse:
            prompts = [
                part.content
                for part in getattr(messages[-1], "parts", [])
                if isinstance(part, UserPromptPart) and isinstance(part.content, str)
            ]
            self.instructions.append(info.instructions or "")
            self.seen.append("\n".join([info.instructions or "", *prompts]))
            return ModelResponse(parts=[step(info, len(self.seen))])

        self.model = FunctionModel(fn)

    def install(self, monkeypatch: pytest.MonkeyPatch, target: str) -> "_Scripted":
        monkeypatch.setattr(target, lambda *_a, **_k: self.model)
        return self


def _emits(output: Any) -> Callable[[AgentInfo, int], ToolCallPart]:
    def step(info: AgentInfo, _n: int) -> ToolCallPart:
        return ToolCallPart(tool_name=info.output_tools[0].name, args=output.model_dump())

    return step


# =========================================================================== #
# writer_planner
# =========================================================================== #


def _planner_deps(**over: Any) -> Any:
    base: dict[str, Any] = dict(
        supabase=None, intent="اكتب مذكرة", recent_messages=RECENT,
        case_brief="موجز القضية", compaction_summary_md="ملخص سابق",
    )
    return build_writer_planner_deps(**{**base, **over})


def test_planner_gets_the_block_before_case_brief(block: str) -> None:
    text = WPP.build_writer_planner_instructions(_planner_deps(conversation_memory_md=block))
    _assert_once(text, block)
    assert text.index("</intent>") < text.index(block) < text.index("<case_brief>")


def test_planner_without_memory_adds_nothing(block: str) -> None:
    plain = WPP.build_writer_planner_instructions(_planner_deps())
    with_memory = WPP.build_writer_planner_instructions(_planner_deps(conversation_memory_md=block))
    assert with_memory.replace(f"{block}\n{WPP._MEMORY_PLAN_NOTE}\n\n", "", 1) == plain
    assert WPP.build_writer_planner_instructions(_planner_deps(conversation_memory_md="")) == plain
    assert WPP._MEMORY_PLAN_NOTE not in plain


def test_the_plan_note_stays_out_of_the_cached_static_prompt() -> None:
    assert WPP._MEMORY_PLAN_NOTE not in WPP.WRITER_PLANNER_SYSTEM_PROMPT


def test_planner_deps_are_filled_from_the_major_input(monkeypatch, block: str) -> None:
    async def _empty(**_k: Any) -> list:
        return []

    monkeypatch.setattr(WR, "get_detail_level", lambda *_a: "medium")
    monkeypatch.setattr(WR, "load_writer_planner_context", _empty)
    monkeypatch.setattr(WR, "load_template_catalog", _empty)
    major = MajorAgentInput(
        describe_query="اكتب", task_label="مذكرة", user_id="u1",
        conversation_id="c1", conversation_memory_md=block,
    )
    deps = asyncio.run(WR._build_writer_planner_deps_from_input(major, object(), None))
    assert deps.conversation_memory_md == block


def test_a_planner_turn_threads_the_block_to_executor_and_responder(
    monkeypatch, block: str
) -> None:
    """Both handoffs of one completed turn, the voice on the unified responder."""
    seen: dict[str, Any] = {}

    async def _deps(*_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(
            present_count=0,
            style=SimpleNamespace(detail_level="medium", tone="formal"),
            resolve_tpl_alias=lambda _alias: None,
        )

    decision = PlannerDecision.model_construct(
        parties=[], intent_ar="مذكرة", subtype="memo", edit_mode="fresh", plan_md="خطة",
        selected_wis=[], role_assignments={}, chosen_template=None, aborted=False,
        rationale="",
    )

    class _Decider:
        async def run(self, *_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(output=decision)

    @contextlib.contextmanager
    def _track(*_a: Any, **_k: Any):
        yield SimpleNamespace(
            set=lambda **_kw: None, set_outcome=lambda *_a: None,
            record_run=lambda *_a, **_k: None,
        )

    async def _package(*_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(prior_draft=lambda: None, analyzed_items=[])

    def _build_writer_deps(**kwargs: Any) -> Any:
        seen["exec_memory"] = kwargs.get("conversation_memory_md")
        return SimpleNamespace(revising_item_id=None)

    llm_output = WriterLLMOutput(
        title_ar="مذكرة", confidence="medium", chat_summary="ملخص", key_findings=["نقطة"],
        sections=[WriterSection(heading_ar="الوقائع", body_md="نص")],
    )

    async def _execute(*_a: Any) -> WriterLLMOutput:
        return llm_output

    async def _publish(*_a: Any) -> Any:
        return SimpleNamespace(
            item_id="wi-1", chat_summary="ملخص", key_findings=["نقطة"], sse_events=[],
            metadata={"exhibits": []},
        )

    async def _run_responder(inp: Any) -> None:
        seen["responder_input"] = inp

    monkeypatch.setattr(WR, "_build_writer_planner_deps_from_input", _deps)
    monkeypatch.setattr(WR, "create_writer_planner_decider", lambda: _Decider())
    monkeypatch.setattr(WR, "track_stage", _track)
    monkeypatch.setattr(WR, "_resolve_decision_aliases", lambda *_a: ([], {}))
    monkeypatch.setattr(WR, "_sole_system_template_for", lambda *_a: None)
    monkeypatch.setattr(WR, "_build_package_from_decision", _package)
    monkeypatch.setattr(WR, "build_writer_deps", _build_writer_deps)
    monkeypatch.setattr(WR, "handle_writer_turn", _execute)
    monkeypatch.setattr(WR, "WriterInput", SimpleNamespace(from_package=lambda *_a, **_k: object()))
    monkeypatch.setattr(WR, "publish_writer_result", _publish)
    monkeypatch.setattr("agents.responder.wiring.responder_unified", lambda _family: True)
    monkeypatch.setattr("agents.responder.wiring.load_profession_group", lambda *_a: None)
    monkeypatch.setattr("agents.responder.wiring.load_style_profile", lambda *_a: None)
    monkeypatch.setattr("agents.responder.run_responder", _run_responder)

    major = MajorAgentInput(
        describe_query="اكتب", task_label="مذكرة", user_id="u1",
        conversation_id="c1", conversation_memory_md=block,
    )
    asyncio.run(WR.handle_writer_planner_turn(major, object()))
    assert seen["exec_memory"] == block
    assert seen["responder_input"].conversation_memory_md == block


# =========================================================================== #
# agent_writer
# =========================================================================== #


def _writer_instructions(monkeypatch, memory: str | None, *, welcome: str | None = WELCOME) -> str:
    """The whole system prompt of one executor run."""
    output = WriterLLMOutput(
        title_ar="عنوان", confidence="high", chat_summary="ملخص", key_findings=["نقطة"],
        sections=[WriterSection(heading_ar="## أ", body_md="ب")],
    )
    scripted = _Scripted(_emits(output))
    monkeypatch.setattr(writer_agent, "get_agent_model", lambda *_a, **_k: scripted.model)
    package = WriterPackage(
        intent_ar="مذكرة", subtype="memo", edit_mode="fresh", plan_md="الخطة",
        analyzed_items=[
            AnalyzedItem(
                item_id="i1", wi_seq=1, kind="attachment", role="source", need="full",
                title="قرار", body_md="نص", word_count_before=1, word_count_after=1,
            )
        ],
        style=WriterStyle(detail_level="medium", tone="formal"),
    )
    deps = build_writer_deps(
        conversation_id="c1", describe_query="الطلب", detail_level="medium", tone="formal",
        welcome_instruction=welcome, conversation_memory_md=memory,
    )
    asyncio.run(_handle_package_turn(package, deps))
    return scripted.instructions[0]


def test_writer_block_comes_after_package_and_envelope_before_welcome(
    monkeypatch, block: str
) -> None:
    text = _writer_instructions(monkeypatch, block)
    _assert_once(text, block)
    package = text.index("The following is the writing package prepared by the planner:")
    envelope = text.index("## Current task context")
    memory = text.index(block)
    welcome = text.index(WELCOME)
    assert package < envelope < memory < welcome
    # The welcome stays last: nothing may follow it in the system prompt.
    assert text.endswith(WELCOME)
    # The cached prefix is the static prompt, exactly.
    assert text.startswith(get_writer_prompt("memo"))


def test_writer_without_memory_adds_nothing(monkeypatch, block: str) -> None:
    plain = _writer_instructions(monkeypatch, None)
    with_memory = _writer_instructions(monkeypatch, block)
    added = f"{block}\n\n{writer_agent._MEMORY_DRAFTING_NOTE}\n\n"
    assert with_memory.replace(added, "", 1) == plain
    assert _writer_instructions(monkeypatch, "") == plain


def test_writer_note_is_never_in_the_static_prompt() -> None:
    for subtype in ("memo", "contract", "statement_of_claim"):
        assert writer_agent._MEMORY_DRAFTING_NOTE not in get_writer_prompt(subtype)


def test_writer_note_settles_the_rules_the_block_leaves_open() -> None:
    note = writer_agent._MEMORY_DRAFTING_NOTE
    assert "`[…]` placeholders" in note
    assert "`label_ar`" in note
    assert "`<parties>`" in note and "`<plan>`" in note
    # Not `chat_summary` or `notes_ar`: see the comment on the note.
    assert "FIRST item of `key_findings`" in note


# =========================================================================== #
# simple_search: the four builders
# =========================================================================== #


def _reference() -> Any:
    return SimpleNamespace(n=1, source_type="regulation", title="نظام", regulation_title="نظام")


def _digest() -> ResponderDocDigest:
    return ResponderDocDigest(
        label="D1", level="article", object_title="المادة 77", excerpt="نص", body_chars=2,
    )


def _synthesizer_message(**kw: Any) -> str:
    unfold = UnfoldResult(level="article", text="نص المادة")
    return P.build_synthesizer_user_message(
        "ما المادة 77؟", [unfold], [_reference()], recent_messages=RECENT, **kw
    )


def _compliance_message(**kw: Any) -> str:
    return P.build_compliance_user_message(
        "كيف أطبع الوثيقة؟", ["[1] خدمة"], recent_messages=RECENT, **kw
    )


def _responder_message(**kw: Any) -> str:
    return P.build_responder_user_message(
        "ما المادة 77؟", [_digest()], recent_messages=RECENT, **kw
    )


@pytest.mark.parametrize(
    ("build", "note"),
    [
        (_synthesizer_message, P._ANSWER_MEMORY_NOTE),
        (_compliance_message, P._ANSWER_MEMORY_NOTE),
        (_responder_message, P._RESPONDER_MEMORY_NOTE),
    ],
    ids=["synthesizer", "compliance", "responder"],
)
def test_message_builders_print_the_block_before_the_window(
    build: Callable[..., str], note: str, block: str
) -> None:
    text = build(conversation_memory_md=block)
    _assert_once(text, block)
    assert text.index(block) < text.index("<recent_messages>") < text.index("<user_message>")
    assert text.count(note) == 1
    # Nothing else changed.
    assert text.replace(f"{block}\n{note}\n\n", "", 1) == build()
    assert build(conversation_memory_md=None) == build()
    assert build(conversation_memory_md="") == build()


def test_searcher_instructions_print_the_block_before_the_window(block: str) -> None:
    deps = SearcherDeps(supabase=None, recent_messages=RECENT, conversation_memory_md=block)
    text = P.build_searcher_instructions(deps)
    _assert_once(text, block)
    assert text.index(block) < text.index("<recent_messages>")
    plain = P.build_searcher_instructions(SearcherDeps(supabase=None, recent_messages=RECENT))
    assert text.replace(f"{block}\n", "", 1) == plain


def test_searcher_instructions_stay_empty_with_nothing_to_add() -> None:
    assert P.build_searcher_instructions(SearcherDeps(supabase=None)) == ""


def test_the_notes_keep_the_warning_where_the_digest_can_see_it() -> None:
    """The responder writes the chat line from a clipped excerpt of the body, so
    the warning only reaches it from the very start of the answer."""
    assert "very start of your answer" in P._ANSWER_MEMORY_NOTE
    assert "`chat_summary_md`" in P._RESPONDER_MEMORY_NOTE


# =========================================================================== #
# simple_search: the whole thread
# =========================================================================== #


REGULATION_ID = "0cf48da9-3752-4eed-bf00-c22230823745"


def _article() -> ResolvedObject:
    return ResolvedObject(
        level="article", regulation_id=REGULATION_ID, article_number="77", title="المادة 77",
    )


def _lookup_agents(monkeypatch) -> tuple[_Scripted, _Scripted, _Scripted]:
    """Searcher, synthesizer and responder scripted for one clean lookup."""
    searcher = _Scripted(_emits(SearcherDecision(objects=[_article()]))).install(
        monkeypatch, "agents.simple_search.searcher.get_agent_model"
    )
    synthesizer = _Scripted(
        _emits(SynthesizerOutput(synthesis_md="نص الإجابة [1]", used_refs=[1]))
    ).install(monkeypatch, "agents.simple_search.synthesizer.get_agent_model")
    responder = _Scripted(
        _emits(ResponderOutput(
            chat_summary_md="فتحت لك ما طلبت.",
            next_steps=[],
            cards=[CardVerdict(doc="D1", card=True, title="المادة 77")],
        ))
    ).install(monkeypatch, "agents.simple_search.responder.get_agent_model")

    async def _unfold(_supabase: Any, obj: Any, *, judgment_access: Any = None) -> UnfoldResult:
        return UnfoldResult(level=obj.level, text="نص المادة")

    async def _publish(_supabase: Any, **_k: Any) -> SimpleSearchPublishResult:
        return SimpleSearchPublishResult(item_id="wi-1", sse_events=[])

    monkeypatch.setattr(R, "unfold", _unfold)
    monkeypatch.setattr(R, "publish_simple_search_result", _publish)
    monkeypatch.setattr("agents.responder.wiring.responder_unified", lambda _family: False)
    return searcher, synthesizer, responder


def test_run_simple_search_reaches_every_agent_of_the_turn(monkeypatch, block: str) -> None:
    agents = _lookup_agents(monkeypatch)
    result = asyncio.run(
        R.run_simple_search(
            "ما المادة 77؟", object(), "u1", "c1", None,
            recent_messages=RECENT, conversation_memory_md=block,
        )
    )
    assert result.chat_messages == ["فتحت لك ما طلبت."]
    for scripted in agents:
        assert len(scripted.seen) == 1
        _assert_once(scripted.seen[0], block)


def test_run_simple_search_without_memory_sends_no_block(monkeypatch) -> None:
    agents = _lookup_agents(monkeypatch)
    asyncio.run(R.run_simple_search("ما المادة 77؟", object(), "u1", "c1", None))
    for scripted in agents:
        assert "<conversation_memory>" not in scripted.seen[0]


def test_run_simple_search_hands_the_unified_responder_the_field(monkeypatch, block: str) -> None:
    _lookup_agents(monkeypatch)
    captured: list[Any] = []

    async def _run_responder(inp: Any) -> None:
        captured.append(inp)

    monkeypatch.setattr("agents.responder.wiring.responder_unified", lambda _family: True)
    monkeypatch.setattr("agents.responder.wiring.load_profession_group", lambda *_a: None)
    monkeypatch.setattr("agents.responder.wiring.load_style_profile", lambda *_a: None)
    monkeypatch.setattr("agents.responder.run_responder", _run_responder)

    asyncio.run(
        R.run_simple_search(
            "ما المادة 77؟", object(), "u1", "c1", None, conversation_memory_md=block,
        )
    )
    assert [inp.conversation_memory_md for inp in captured] == [block]
    # Once: the fallback window is a separate field and must not carry it too.
    assert block not in (captured[0].fallback_context or "")


def test_compliance_agent_receives_the_block(monkeypatch, block: str) -> None:
    scripted = _Scripted(
        _emits(ComplianceOutput(sufficient=False, insufficient_reason="لا مرشح مناسب"))
    ).install(monkeypatch, "agents.simple_search.compliance.get_agent_model")
    asyncio.run(
        run_compliance(
            "كيف أطبع الوثيقة؟",
            [ServiceCandidate(n=1, service_id="s1", title="خدمة")],
            supabase=object(), conversation_id="c1", case_id=None,
            recent_messages=RECENT, conversation_memory_md=block,
        )
    )
    _assert_once(scripted.seen[0], block)


def test_resume_shows_the_current_memory_not_the_one_saved_with_the_pause(
    monkeypatch, block: str
) -> None:
    """Instructions are re-rendered on resume. The serialized history still holds
    the old render, and the resumed run must not be reading that one."""
    question = "أي نظام تقصد؟"

    def _ask(_info: AgentInfo, _n: int) -> ToolCallPart:
        return ToolCallPart(tool_name="ask_user", args={"question": question, "data_type": "regs"})

    _Scripted(_ask).install(monkeypatch, "agents.simple_search.searcher.get_agent_model")
    stale = render_conversation_memory([MemoryEntry(seq=1, kind="fact", content="قيمة قديمة")])
    deps = SearcherDeps(supabase=None, conversation_memory_md=stale)
    paused = asyncio.run(
        create_searcher_agent().run(P.build_searcher_user_message("ما المادة؟"), deps=deps)
    )
    row = {
        "run_id": "r1",
        "message_history": paused.all_messages_json(),
        "deferred_payload": {
            "tool_call_id": paused.output.calls[0].tool_call_id,
            "question": "ما المادة؟",
        },
    }

    searcher, synthesizer, responder = _lookup_agents(monkeypatch)
    result = asyncio.run(
        R.resume_simple_search(
            "نظام العمل", row, object(), "u1", "c1", None,
            recent_messages=RECENT, conversation_memory_md=block,
        )
    )
    assert result.chat_messages == ["فتحت لك ما طلبت."]
    for scripted in (searcher, synthesizer, responder):
        _assert_once(scripted.seen[0], block)
        assert "قيمة قديمة" not in scripted.seen[0]


#: Every call that must carry the kwarg. Structural, because a pause leg or a
#: services cycle that drops it fails silently: the turn still answers, without
#: the memory.
_THREADED_CALLS = {
    "agents/simple_search/runner.py": {
        "SearcherDeps", "_answer_loop", "_finalise", "_run_round", "_synthesize_group",
        "_run_unified_responder", "run_compliance", "build_synthesizer_user_message",
        "build_responder_user_message", "ResponderInput",
    },
    "agents/simple_search/compliance.py": {"build_compliance_user_message"},
    "agents/writer_planner/runner.py": {
        "build_writer_planner_deps", "build_writer_deps", "ResponderInput",
    },
}


@pytest.mark.parametrize("path", sorted(_THREADED_CALLS))
def test_every_call_site_passes_the_kwarg(path: str) -> None:
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in _THREADED_CALLS[path]:
            continue
        found[name] = found.get(name, 0) + 1
        keywords = {kw.arg for kw in node.keywords}
        assert "conversation_memory_md" in keywords, f"{name} at line {node.lineno}"
    assert set(found) == _THREADED_CALLS[path]


# =========================================================================== #
# Telemetry: counts and flags, never the text.
# =========================================================================== #


@pytest.mark.parametrize(
    "make",
    [
        lambda memory: _planner_deps(conversation_memory_md=memory),
        lambda memory: build_writer_deps(conversation_memory_md=memory),
        lambda memory: SearcherDeps(
            supabase=None, recent_messages=RECENT, conversation_memory_md=memory
        ),
    ],
    ids=["writer_planner", "writer", "searcher"],
)
def test_telemetry_never_carries_the_text(make: Callable[[str], Any], block: str) -> None:
    deps = make(block)
    bounded, full = _bounded_snapshot(deps), _full_snapshot(deps)
    assert "<conversation_memory>" not in repr(bounded)
    assert "<conversation_memory>" not in repr(full)
    # The verbose view itself still works.
    assert full


def test_tracking_reports_presence_only(block: str) -> None:
    for deps in (
        _planner_deps(conversation_memory_md=block),
        build_writer_deps(conversation_memory_md=block),
    ):
        assert deps.tracking_input()["conversation_memory_present"] is True
    assert _planner_deps().tracking_input()["conversation_memory_present"] is False
    assert build_writer_deps().tracking_input()["conversation_memory_present"] is False


def test_the_searcher_verbose_view_only_loses_the_memory(block: str) -> None:
    deps = SearcherDeps(
        supabase=None, recent_messages=RECENT, rejection_notes=["سبب"],
        conversation_memory_md=block,
    )
    full = deps.tracking_input_full()
    assert set(full) >= {"recent_messages", "rejection_notes", "candidates", "user_id"}
    assert not {"supabase", "emit_sse", "conversation_memory_md"} & set(full)
