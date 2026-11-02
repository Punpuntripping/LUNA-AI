"""Today's date: what the line says, and that it reaches every agent that needs it.

A model with no date guesses one, and nothing at runtime notices. In a real
conversation the router said it was October in August, and wrong deadline advice
followed. Spec: ``.claude/plans/conversation_memory_agent.md`` §3.

Offline: the date is patched, models are ``FunctionModel``, and each agent is
built by its real factory so the assertions read the prompt the model was given.
Sync on purpose: CI has no ``pytest-asyncio``.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from hijridate import Gregorian, Hijri
from pydantic_ai import DeferredToolResults
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.artifact_editor.agent import SYSTEM_PROMPT as EDITOR_PROMPT
from agents.artifact_editor.agent import EditorDeps, editor_agent
from agents.deep_search_v4.aggregator import agent as aggregator_agent
from agents.deep_search_v4.aggregator.deps import AggregatorDeps
from agents.deep_search_v4.aggregator.models import AggregatorInput
from agents.deep_search_v4.aggregator.prompts import (
    AGGREGATOR_PROMPTS,
    build_aggregator_user_message,
    get_aggregator_prompt,
)
from agents.deep_search_v4.aggregator.preprocessor import preprocess_references
from agents.deep_search_v4.aggregator.runner import handle_aggregator_turn
from agents.deep_search_v4.planner.agent import (
    create_planner_decider,
    create_planner_responder,
)
from agents.deep_search_v4.planner.deps import PlannerDeps
from agents.deep_search_v4.planner.prompts import (
    PLANNER_DECIDER_SYSTEM_PROMPT,
    PLANNER_RESPONDER_SYSTEM_PROMPT,
)
from agents.deep_search_v4.ura.schema import RegURAResult, UnifiedRetrievalArtifact
from agents.models import ChatMessageSnapshot
from agents.responder.agent import ResponderDeps, create_responder
from agents.responder.prompts import SYSTEM_PROMPTS
from agents.router import router as rr
from agents.simple_search import runner as simple_runner
from agents.simple_search.compliance import (
    ComplianceOutput,
    ServiceCandidate,
    run_compliance,
)
from agents.simple_search.models import ResolvedObject, UnfoldResult
from agents.simple_search.prompts import (
    SEARCHER_SYSTEM_PROMPT,
    get_synthesizer_prompt,
)
from agents.simple_search.publisher import SimpleSearchPublishResult
from agents.simple_search.responder import CardVerdict, ResponderOutput
from agents.simple_search.searcher import (
    SearcherDecision,
    SearcherDeps,
    create_searcher_agent,
)
from agents.simple_search.synthesizer import SynthesizerOutput
from agents.utils import today
from agents.writer.agent import create_writer_agent
from agents.writer.deps import build_writer_deps
from agents.writer.prompts import get_writer_prompt
from agents.writer_planner.agent import create_writer_planner_decider
from agents.writer_planner.deps import build_writer_planner_deps
from agents.writer_planner.prompts import WRITER_PLANNER_SYSTEM_PROMPT
from shared.library import supersession

REPO_ROOT = Path(__file__).resolve().parents[2]

TAIL = (
    "(Saudi time). Use this for any deadline, elapsed time or reference to today. "
    "Never guess the date. Do not add it to a document unless the document calls "
    "for a date."
)
#: Month 4 on purpose: the one month whose name is not the library's.
DAY = date(2026, 10, 1)
LINE = f"Today is 1 أكتوبر 2026م الموافق 20 ربيع الآخر 1448هـ {TAIL}"
GREGORIAN_ONLY = f"Today is 1 أكتوبر 2026م {TAIL}"
NEXT_DAY_LINE = f"Today is 2 أكتوبر 2026م الموافق 21 ربيع الآخر 1448هـ {TAIL}"

RECENT = [ChatMessageSnapshot(role="user", content="سؤال سابق", created_at="2026-11-01T00:00:00Z")]


@pytest.fixture
def dated(monkeypatch) -> None:
    monkeypatch.setattr(today, "riyadh_today", lambda: DAY)


# --------------------------------------------------------------------------- #
# The line
# --------------------------------------------------------------------------- #


def test_the_line_is_the_specs_wording(dated) -> None:
    assert today.today_line() == LINE


def test_it_is_one_line_in_latin_digits(dated) -> None:
    line = today.today_line()
    assert "\n" not in line
    assert not re.search(r"[٠-٩۰-۹]", line)


@pytest.mark.parametrize(
    ("day", "hijri"),
    [
        (date(2024, 3, 11), "1 رمضان 1445هـ"),
        (date(2024, 6, 16), "10 ذو الحجة 1445هـ"),
        (date(2025, 3, 30), "1 شوال 1446هـ"),
        (date(2026, 2, 18), "1 رمضان 1447هـ"),
        (date(2026, 6, 16), "1 محرم 1448هـ"),
        (date(2026, 11, 2), "22 جمادى الأولى 1448هـ"),
    ],
)
def test_known_umm_al_qura_dates(monkeypatch, day: date, hijri: str) -> None:
    monkeypatch.setattr(today, "riyadh_today", lambda: day)
    assert f"الموافق {hijri} (Saudi time)" in today.today_line()


def test_gregorian_month_names_match_the_library() -> None:
    assert today._GREGORIAN_MONTHS == tuple(
        Gregorian(2026, month, 1).month_name("ar") for month in range(1, 13)
    )


def test_hijri_month_names_match_the_library_except_rabi_al_akhir() -> None:
    """hijridate prints month 4 as «ربيع الثاني». The corpus and the courts do not."""
    for month in range(1, 13):
        name = Hijri(1448, month, 1).month_name("ar")
        assert today._HIJRI_MONTHS[month - 1] == ("ربيع الآخر" if month == 4 else name)


def _freeze_utc(monkeypatch, moment: datetime) -> None:
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz)

    monkeypatch.setattr(supersession, "datetime", Frozen)


NOV_1 = "1 نوفمبر 2026م الموافق 21 جمادى الأولى 1448هـ"
NOV_2 = "2 نوفمبر 2026م الموافق 22 جمادى الأولى 1448هـ"
NOV_3 = "3 نوفمبر 2026م الموافق 23 جمادى الأولى 1448هـ"


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        pytest.param((2026, 11, 1, 20, 59, 59), NOV_1, id="last-second-of-the-saudi-day"),
        pytest.param((2026, 11, 1, 21, 0, 0), NOV_2, id="saudi-midnight-three-hours-before-utc"),
        pytest.param((2026, 11, 1, 23, 59, 59), NOV_2, id="just-before-utc-midnight"),
        pytest.param((2026, 11, 2, 0, 0, 1), NOV_2, id="just-after-utc-midnight"),
        pytest.param((2026, 11, 2, 21, 0, 0), NOV_3, id="the-next-saudi-midnight"),
    ],
)
def test_the_date_is_saudi_not_utc(monkeypatch, utc: tuple, expected: str) -> None:
    _freeze_utc(monkeypatch, datetime(*utc, tzinfo=timezone.utc))
    assert today.today_line() == f"Today is {expected} {TAIL}"


def test_a_failed_hijri_conversion_still_gives_a_line(dated, monkeypatch) -> None:
    class Broken:
        @staticmethod
        def fromdate(_day: date) -> None:
            raise ValueError("conversion failed")

    monkeypatch.setattr("hijridate.Gregorian", Broken)
    assert today.today_line() == GREGORIAN_ONLY


def test_a_missing_package_still_gives_a_line(dated, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "hijridate", None)
    assert today.today_line() == GREGORIAN_ONLY


def test_a_date_outside_the_hijri_table_still_gives_a_line(monkeypatch) -> None:
    monkeypatch.setattr(today, "riyadh_today", lambda: date(2078, 1, 1))
    assert today.today_line() == f"Today is 1 يناير 2078م {TAIL}"


# --------------------------------------------------------------------------- #
# Agents that read it in their instructions
# --------------------------------------------------------------------------- #


class _Stop(Exception):
    """Ends the run once the prompt has reached the model."""


class _Capture:
    """A model that records the system prompt, then stops the run."""

    def __init__(self) -> None:
        self.instructions: list[str] = []

        def stop(_messages: list, info: AgentInfo) -> ModelResponse:
            self.instructions.append(info.instructions or "")
            raise _Stop

        self.model = FunctionModel(stop)

    def run(self, agent: Any, deps: Any) -> str:
        with agent.override(model=self.model), pytest.raises(_Stop):
            asyncio.run(agent.run("سؤال", deps=deps))
        return self.instructions[-1]


@pytest.fixture
def capture(dated, monkeypatch) -> _Capture:
    """No factory reaches a real provider, so CI's dummy keys are enough."""
    fake = _Capture()
    for module in (
        "agents.deep_search_v4.planner.agent",
        "agents.responder.agent",
        "agents.simple_search.searcher",
        "agents.writer.agent",
        "agents.writer_planner.agent",
    ):
        monkeypatch.setattr(f"{module}.get_agent_model", lambda *_a, **_k: fake.model)
    return fake


def _assert_opens_the_dynamic_part(instructions: str, static: str, line: str = LINE) -> None:
    """The static prompt comes first with its bytes intact, so its cache holds."""
    assert instructions.startswith(f"{static.strip()}\n\n{line}")
    assert instructions.count(line) == 1


def _planner_deps(**over: Any) -> PlannerDeps:
    deps = PlannerDeps(supabase=None, embedding_fn=None)
    for name, value in over.items():
        setattr(deps, name, value)
    return deps


def test_router(capture: _Capture) -> None:
    deps = rr.RouterDeps(
        supabase=None, user_id="u1", conversation_id="c1", case_id=None,
        case_memory_md="ملف القضية", case_metadata=None, user_preferences=None,
    )
    instructions = capture.run(rr.router_agent, deps)
    _assert_opens_the_dynamic_part(instructions, rr.SYSTEM_PROMPT)
    assert "ملف القضية" in instructions


@pytest.mark.parametrize(
    "context",
    [{}, {"case_brief": "ملخص", "recent_messages": RECENT}],
    ids=["no-context-stub", "with-context"],
)
def test_deep_search_decider(capture: _Capture, context: dict) -> None:
    instructions = capture.run(create_planner_decider(), _planner_deps(**context))
    _assert_opens_the_dynamic_part(instructions, PLANNER_DECIDER_SYSTEM_PROMPT)


@pytest.mark.parametrize(
    "agg_output",
    [
        None,
        SimpleNamespace(references=[], gaps=[], synthesis_md="جواب", confidence="medium"),
    ],
    ids=["degraded", "normal"],
)
def test_deep_search_responder(capture: _Capture, agg_output: Any) -> None:
    deps = _planner_deps(
        _agg_output=agg_output,
        _decision=SimpleNamespace(mode="full", planner_brief=""),
        welcome_instruction="WELCOME-BLOCK",
    )
    instructions = capture.run(create_planner_responder(), deps)
    _assert_opens_the_dynamic_part(instructions, PLANNER_RESPONDER_SYSTEM_PROMPT)
    assert instructions.index(LINE) < instructions.index("WELCOME-BLOCK")


def test_writer_planner(capture: _Capture) -> None:
    deps = build_writer_planner_deps(supabase=None, intent="اكتب مذكرة")
    instructions = capture.run(create_writer_planner_decider(), deps)
    _assert_opens_the_dynamic_part(instructions, WRITER_PLANNER_SYSTEM_PROMPT)


def test_writer(capture: _Capture) -> None:
    deps = build_writer_deps(
        conversation_id="c1", describe_query="الطلب", welcome_instruction="WELCOME-BLOCK",
    )
    instructions = capture.run(create_writer_agent(subtype="memo"), deps)
    _assert_opens_the_dynamic_part(instructions, get_writer_prompt("memo"))
    assert instructions.index(LINE) < instructions.index("WELCOME-BLOCK")


def test_artifact_editor(capture: _Capture) -> None:
    deps = EditorDeps(
        supabase=None, item_id="i1", artifact_title="مذكرة", artifact_content_md="نص المذكرة",
        user_message="عدّل التاريخ", task="عدّل التاريخ",
    )
    instructions = capture.run(editor_agent, deps)
    _assert_opens_the_dynamic_part(instructions, EDITOR_PROMPT)
    assert instructions.index(LINE) < instructions.index("نص المذكرة")


@pytest.mark.parametrize("mode", ["deep_search", "simple_search", "writing"])
def test_unified_responder(capture: _Capture, mode: str) -> None:
    deps = ResponderDeps(mode=mode, context_block="CONTEXT-BLOCK")
    instructions = capture.run(create_responder(mode), deps)
    _assert_opens_the_dynamic_part(instructions, SYSTEM_PROMPTS[mode])
    assert instructions.index(LINE) < instructions.index("CONTEXT-BLOCK")


def test_simple_search_searcher_with_nothing_else_to_add(capture: _Capture) -> None:
    instructions = capture.run(create_searcher_agent(), SearcherDeps(supabase=None))
    assert instructions == f"{SEARCHER_SYSTEM_PROMPT.strip()}\n\n{LINE}"


def test_a_run_resumed_the_next_day_reads_the_new_date(dated, monkeypatch) -> None:
    """A paused turn can wait overnight. Its history holds yesterday's render."""
    seen: list[str] = []

    def respond(_messages: list, info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions or "")
        if len(seen) > 1:
            raise _Stop
        return ModelResponse(
            parts=[ToolCallPart(tool_name="ask_user", args={"question": "أي نظام؟", "data_type": "regs"})]
        )

    model = FunctionModel(respond)
    monkeypatch.setattr("agents.simple_search.searcher.get_agent_model", lambda *_a, **_k: model)
    agent = create_searcher_agent()
    deps = SearcherDeps(supabase=None)
    paused = asyncio.run(agent.run("سؤال", deps=deps))

    monkeypatch.setattr(today, "riyadh_today", lambda: date(2026, 10, 2))
    results = DeferredToolResults(calls={paused.output.calls[0].tool_call_id: "نظام العمل"})
    with pytest.raises(_Stop):
        asyncio.run(
            agent.run(message_history=paused.all_messages(), deferred_tool_results=results, deps=deps)
        )

    _assert_opens_the_dynamic_part(seen[0], SEARCHER_SYSTEM_PROMPT)
    _assert_opens_the_dynamic_part(seen[1], SEARCHER_SYSTEM_PROMPT, NEXT_DAY_LINE)
    assert LINE not in seen[1]


# --------------------------------------------------------------------------- #
# Simple search agents that read it in their user message
# --------------------------------------------------------------------------- #


class _Scripted:
    """A model that emits one output and records what each call was sent."""

    def __init__(self, output: Any) -> None:
        self.instructions: list[str] = []
        self.messages: list[str] = []

        def fn(messages: list, info: AgentInfo) -> ModelResponse:
            self.instructions.append(info.instructions or "")
            self.messages.append(_user_prompts(messages)[-1])
            return ModelResponse(
                parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=output.model_dump())]
            )

        self.model = FunctionModel(fn)

    def install(self, monkeypatch: pytest.MonkeyPatch, target: str) -> "_Scripted":
        monkeypatch.setattr(target, lambda *_a, **_k: self.model)
        return self


def _user_prompts(messages: list) -> list[str]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    ]


def test_a_lookup_turn_reaches_the_searcher_the_synthesizer_and_the_responder(
    dated, monkeypatch
) -> None:
    article = ResolvedObject(
        level="article",
        regulation_id="0cf48da9-3752-4eed-bf00-c22230823745",
        article_number="77",
        title="المادة 77",
    )
    searcher = _Scripted(SearcherDecision(objects=[article])).install(
        monkeypatch, "agents.simple_search.searcher.get_agent_model"
    )
    synthesizer = _Scripted(SynthesizerOutput(synthesis_md="نص الإجابة [1]", used_refs=[1])).install(
        monkeypatch, "agents.simple_search.synthesizer.get_agent_model"
    )
    responder = _Scripted(
        ResponderOutput(
            chat_summary_md="فتحت لك ما طلبت.",
            next_steps=[],
            cards=[CardVerdict(doc="D1", card=True, title="المادة 77")],
        )
    ).install(monkeypatch, "agents.simple_search.responder.get_agent_model")

    async def _unfold(_supabase: Any, obj: Any, *, judgment_access: Any = None) -> UnfoldResult:
        return UnfoldResult(level=obj.level, text="نص المادة")

    async def _publish(_supabase: Any, **_k: Any) -> SimpleSearchPublishResult:
        return SimpleSearchPublishResult(item_id="wi-1", sse_events=[])

    monkeypatch.setattr(simple_runner, "unfold", _unfold)
    monkeypatch.setattr(simple_runner, "publish_simple_search_result", _publish)
    monkeypatch.setattr("agents.responder.wiring.responder_unified", lambda _family: False)

    asyncio.run(
        simple_runner.run_simple_search(
            "ما المادة 77؟", object(), "u1", "c1", None, recent_messages=RECENT,
        )
    )

    _assert_opens_the_dynamic_part(searcher.instructions[0], SEARCHER_SYSTEM_PROMPT)
    assert synthesizer.messages[0].startswith(f"{LINE}\n\n")
    assert responder.messages[0].startswith(f"{LINE}\n\n")
    # The system prompts themselves stay out of it.
    assert synthesizer.instructions[0] == get_synthesizer_prompt("article").strip()
    assert LINE not in responder.instructions[0]


def test_the_compliance_agent(dated, monkeypatch) -> None:
    scripted = _Scripted(ComplianceOutput(sufficient=False, insufficient_reason="لا مرشح")).install(
        monkeypatch, "agents.simple_search.compliance.get_agent_model"
    )
    asyncio.run(
        run_compliance(
            "كيف أطبع الوثيقة؟",
            [ServiceCandidate(n=1, service_id="s1", title="خدمة")],
            supabase=object(), conversation_id="c1", case_id=None,
        )
    )
    assert scripted.messages[0].startswith(f"{LINE}\n\n")
    assert LINE not in scripted.instructions[0]


# --------------------------------------------------------------------------- #
# The aggregator: every path to the model
# --------------------------------------------------------------------------- #


def _llm_json(synthesis: str, used: list[int]) -> str:
    return json.dumps(
        {"synthesis_md": synthesis, "used_refs": used, "gaps": [], "confidence": "high"},
        ensure_ascii=False,
    )


CLEAN = _llm_json("## الخلاصة\n\nالجواب [1]", [1])
DANGLING_CITATION = _llm_json("## الخلاصة\n\nالجواب [9]", [9])
#: Everything on the v1 path that is not the system prompt, in the first request.
OPENING = f"{LINE}\n\n<original_query>"
SINGLE_SHOT_KEYS = sorted(key for key in AGGREGATOR_PROMPTS if not key.startswith("prompt_3"))


def _agg_input(prompt_key: str) -> AggregatorInput:
    return AggregatorInput(
        original_query="ما الحكم؟",
        sub_queries=[],
        prompt_key=prompt_key,
        ura=UnifiedRetrievalArtifact(
            high_results=[
                RegURAResult(
                    ref_id="reg:one", source_type="reg_chunk", relevance="high",
                    reg_title="نظام الموانئ الجافة", chunk_content="نص المادة",
                ),
            ],
        ),
    )


class _AggregatorScript:
    """Plays one step per LLM call: an output text, or an exception to raise."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *steps: Any) -> None:
        #: Per call: the system prompt and the user prompts of the whole request.
        self.calls: list[tuple[str, list[str]]] = []

        def fn(messages: list, info: AgentInfo) -> ModelResponse:
            self.calls.append((info.instructions or "", _user_prompts(messages)))
            step = steps[len(self.calls) - 1]
            if isinstance(step, Exception):
                raise step
            return ModelResponse(parts=[TextPart(step)])

        model = FunctionModel(fn)
        monkeypatch.setattr(aggregator_agent, "get_agent_model", lambda *_a, **_k: model)

    def run(self, prompt_key: str) -> None:
        asyncio.run(handle_aggregator_turn(_agg_input(prompt_key), AggregatorDeps()))


def _system(key: str) -> str:
    return get_aggregator_prompt(key).strip()


def test_the_builder_opens_with_the_line_ahead_of_the_query(dated) -> None:
    agg_input = _agg_input("prompt_1")
    references, _ = preprocess_references(agg_input)
    assert build_aggregator_user_message(agg_input, references).startswith(OPENING)


@pytest.mark.parametrize("key", SINGLE_SHOT_KEYS)
def test_every_single_shot_prompt_gets_it(dated, monkeypatch, key: str) -> None:
    script = _AggregatorScript(monkeypatch, CLEAN)
    script.run(key)

    assert len(script.calls) == 1
    system, prompts = script.calls[0]
    assert system == _system(key)  # Not in the system prompt: v1 keeps its bytes.
    assert len(prompts) == 1
    assert prompts[0].startswith(OPENING)


def test_the_prompt_1_retry_after_a_raise_gets_it(dated, monkeypatch) -> None:
    script = _AggregatorScript(monkeypatch, RuntimeError("provider down"), CLEAN)
    script.run("prompt_mode_full")

    assert [call[0] for call in script.calls] == [_system("prompt_mode_full"), _system("prompt_1")]
    assert all(prompts[0].startswith(OPENING) for _, prompts in script.calls)


def test_draft_critique_and_rewrite_all_get_it(dated, monkeypatch) -> None:
    script = _AggregatorScript(monkeypatch, CLEAN, CLEAN, CLEAN)
    script.run("prompt_3_draft")

    assert [call[0] for call in script.calls] == [
        _system("prompt_3_draft"), _system("prompt_3_critique"), _system("prompt_3_rewrite"),
    ]
    assert all(prompts[0].startswith(OPENING) for _, prompts in script.calls)
    assert "<draft>" in script.calls[1][1][0]
    assert "<critique>" in script.calls[2][1][0]


def test_the_single_shot_fallback_of_a_failed_chain_gets_it(dated, monkeypatch) -> None:
    script = _AggregatorScript(monkeypatch, RuntimeError("draft failed"), CLEAN)
    script.run("prompt_3_draft")

    assert script.calls[-1][0] == _system("prompt_1")
    assert script.calls[-1][1][0].startswith(OPENING)


def test_the_correction_turn_gets_it_through_the_replayed_history(dated, monkeypatch) -> None:
    """Its own message is only the fix. The date reaches the model in the first
    request, which pydantic-ai resends ahead of it."""
    script = _AggregatorScript(monkeypatch, DANGLING_CITATION, CLEAN)
    script.run("prompt_1")

    assert len(script.calls) == 2
    system, prompts = script.calls[1]
    assert system == _system("prompt_1")
    assert len(prompts) == 2
    first, correction = prompts
    assert first.startswith(OPENING)
    assert first == script.calls[0][1][0]
    assert "citation_ok" in correction
    assert LINE not in correction


# --------------------------------------------------------------------------- #
# Who does not get it
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("package", ["reg_compliance_search", "case_search"])
def test_rerankers_and_expanders_do_not_get_it(package: str) -> None:
    folder = REPO_ROOT / "agents" / "deep_search_v4" / package
    users = [
        path.name
        for path in folder.rglob("*.py")
        if "tests" not in path.parts and "today_line" in path.read_text(encoding="utf-8")
    ]
    assert users == []
