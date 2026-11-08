"""artifact_analyzer: ``read_full_text`` (plan artifact_analyzer_scope_full_text.md §4).

A ruling, or a service with «الدليل الشامل», read in full beside what the search
read. The rules code enforces: which sources qualify, three per turn, quotes only
from the full half, reading errors proven word for word on both sides, and a
cited ruling read before the report's sentence about it is relied on.
"""
from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from agents.memory.artifact_analyzer import agent as agent_mod, analyze, runner as runner_mod
from agents.memory.artifact_analyzer.context import load_reports, load_sources, render_turn_message
from agents.memory.artifact_analyzer.deps import AnalyzerDeps
from agents.memory.artifact_analyzer.full_text import (
    FULL_TEXT_CHARS,
    MAX_FULL_READS,
    _RULING_GAP,
    cap,
    read_full,
    render_full,
    take,
)
from agents.memory.artifact_analyzer.models import (
    AnalyzerOutputFullLLM,
    AnalyzerOutputLLM,
    QuoteLLM,
    ReadingErrorLLM,
)
from agents.memory.artifact_analyzer.prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_FULL_TEXT
from agents.memory.artifact_analyzer.verify import finalize, unread_citations
from agents.simple_search.unfold import GUIDE_SLICE_GAP_AR
from agents.tests.test_artifact_analyzer import CONVO, USER, FakeDB, _call, _report_row

REPORT_SAYS = "حكمت المحكمة بإلزام المدعى عليه بدفع مئة ألف ريال"
RULING_SAYS = "حكمت الدائرة بإلزام المدعى عليه بدفع خمسين ألف ريال"
BODY = f"{REPORT_SAYS} [7]. والخدمة تُقدَّم إلكترونيًا [8]."
SEARCH_VIEW = "ملخص الحكم: ألزمت المحكمة المدعى عليه بالدفع."
FULL_RULING = f"الوقائع: تتلخص في مطالبة مالية.\nالأسباب: ثبت الدين.\n{RULING_SAYS}."
GUIDE_MD = "# دليل الخدمة\nالخطوة الأولى: الدخول إلى المنصة.\n1_1\nالخطوة الثانية: رفع الطلب."
IMAGE = "صورة تبين زر تقديم الطلب في أعلى الصفحة"


def _case(ref: str, **over: Any) -> dict:
    return {
        "ref_id": f"case:{ref}", "domain": "cases", "title": f"حكم {ref}",
        "court": "المحكمة العامة", "case_content": SEARCH_VIEW, "short_summary": "مختصر",
        **over,
    }


def _service(ref: str) -> dict:
    return {
        "ref_id": f"svc:{ref}", "domain": "compliance", "service_ref": ref,
        "service_name": f"خدمة {ref}", "provider_name": "وزارة العدل",
        "service_context": "سياق مختصر", "intro_description": "وصف الخدمة",
        "steps": ["الدخول", "رفع الطلب"], "requirements": [], "required_documents": [],
    }


def _db() -> FakeDB:
    high = [
        _case("CR1"), _service("SR1"), _service("SR2"),
        {"ref_id": "reg:c1", "domain": "regulations", "reg_title": "نظام", "chunk_content": "م"},
        _case("CR2"), _case("CR3"), _case("CR4"),
    ]
    return FakeDB(
        workspace_items=[_report_row("wi-1", "2026-11-02T10:00:00", content_md=BODY)],
        retrieval_artifacts=[{
            "artifact_id": "wi-1", "user_id": USER, "deleted_at": None,
            "ura_json": {"high_results": high, "medium_results": []},
        }],
        workspace_item_references=[
            {"wi_id": "wi-1", "n": 7, "ref_id": "case:CR1", "domain": "cases",
             "item_id": "CR1", "used": True},
            {"wi_id": "wi-1", "n": 8, "ref_id": "svc:SR1", "domain": "compliance",
             "item_id": "SR1", "used": True},
        ],
        cases=[
            {"case_ref": ref, "content": FULL_RULING if ref == "CR1" else f"نص {ref} الكامل"}
            for ref in ("CR1", "CR2", "CR3", "CR4")
        ],
        services=[{"id": "svc-1", "service_ref": "SR1"}, {"id": "svc-2", "service_ref": "SR2"}],
        service_guides=[{
            "id": "g-1", "service_id": "svc-1", "is_canonical": True,
            "title": "دليل الخدمة", "guide_md": GUIDE_MD,
        }],
        service_guide_images=[{"guide_id": "g-1", "image_ref": "1_1", "description": IMAGE}],
    )


def _deps(db: FakeDB | None = None, full_text: bool = True) -> AnalyzerDeps:
    db = db or _db()
    reports = load_reports(db, _call())
    sources = load_sources(db, reports, full_text=full_text)
    return AnalyzerDeps(
        supabase=db, user_id=USER, conversation_id=CONVO, full_text=full_text,
        reports={r.label: r for r in reports},
        sources={s.label: s for s in sources},
    )


def _labels(deps: AnalyzerDeps) -> dict[str, str]:
    """open_key → label."""
    return {s.open_key: s.label for s in deps.sources.values()}


def _read(deps: AnalyzerDeps, *keys: str) -> None:
    """What the tool does, without the model."""
    labels = [_labels(deps)[k] for k in keys]
    for source in take(deps, labels)[0]:
        deps.full_texts[source.label] = read_full(deps.supabase, source)


def _out(verdict: str = "sufficient", quotes: list | None = None, errors: list | None = None):
    return AnalyzerOutputFullLLM(
        restatement="س", verdict=verdict, quotes=quotes or [], reading_errors=errors or [],
    )


# ── What the model is shown ───────────────────────────────────────────────────


def test_a_service_with_a_guide_is_marked_only_when_the_tool_is_on() -> None:
    on = _deps()
    by_key = {s.open_key: s for s in on.sources.values()}
    assert by_key["SR1"].has_guide and "**له دليل شامل**" in by_key["SR1"].block
    assert not by_key["SR2"].has_guide and "له دليل شامل" not in by_key["SR2"].block

    off = _deps(full_text=False)
    assert not any(s.has_guide or "له دليل شامل" in s.block for s in off.sources.values())


def test_the_search_view_is_what_the_aggregator_read() -> None:
    by_key = {s.open_key: s for s in _deps().sources.values()}
    assert by_key["CR1"].search_view == SEARCH_VIEW
    service_view = by_key["SR1"].search_view
    assert "وصف الخدمة" in service_view and "رفع الطلب" in service_view
    assert by_key["c1"].search_view == ""


def test_a_failed_guide_lookup_leaves_services_as_they_were() -> None:
    db = _db()
    db.broken.add("service_guides")
    assert not any(s.has_guide for s in _deps(db).sources.values())


def test_the_prompt_section_exists_only_in_the_full_text_variant() -> None:
    assert "read_full_text" not in SYSTEM_PROMPT and "reading_errors" not in SYSTEM_PROMPT
    for phrase in ("read_full_text", "reading_errors", "ما قرأه البحث", "له دليل شامل"):
        assert phrase in SYSTEM_PROMPT_FULL_TEXT
    # The variant only adds: every line of the base prompt is still there.
    assert all(line in SYSTEM_PROMPT_FULL_TEXT for line in SYSTEM_PROMPT.splitlines())


# ── The tool ─────────────────────────────────────────────────────────────────


def test_rulings_and_guided_services_qualify_nothing_else() -> None:
    deps = _deps()
    labels = _labels(deps)
    to_read, refused = take(deps, [labels["CR1"], labels["SR1"], labels["SR2"], labels["c1"], "S99"])
    assert [s.open_key for s in to_read] == ["CR1", "SR1"]
    assert "open_sources" in refused[labels["SR2"]] and "open_sources" in refused[labels["c1"]]
    assert refused["S99"].startswith("Unknown label")


def test_three_sources_per_turn_across_calls_and_a_reread_is_free() -> None:
    deps = _deps()
    labels = _labels(deps)
    take(deps, [labels["CR1"], labels["CR2"]])
    to_read, refused = take(deps, [labels["CR3"], labels["CR4"], labels["CR1"]])
    assert [s.open_key for s in to_read] == ["CR3", "CR1"]
    assert "تجاوزت الحد" in refused[labels["CR4"]]
    assert len(deps.full_read) == MAX_FULL_READS


def test_the_ruling_and_the_guide_are_read_in_full() -> None:
    deps = _deps()
    by_key = {s.open_key: s for s in deps.sources.values()}
    assert read_full(deps.supabase, by_key["CR1"]) == FULL_RULING
    guide = read_full(deps.supabase, by_key["SR1"])
    assert "رفع الطلب" in guide and IMAGE in guide and "1_1" not in guide


def test_the_tool_shows_what_the_search_read_beside_the_full_text() -> None:
    deps = _deps()
    source = {s.open_key: s for s in deps.sources.values()}["CR1"]
    shown = render_full(source, FULL_RULING)
    assert shown.index("ما قرأه البحث") < shown.index(SEARCH_VIEW) < shown.index("النص الكامل")
    assert shown.endswith(FULL_RULING)


def test_the_cap_keeps_a_rulings_ending_even_on_one_long_line() -> None:
    ending = "المنطوق: رفض الدعوى"
    text = "بداية الحكم " + "كلمة " * 9000 + ending  # one 45k-character line
    capped = cap(text, "cases")
    assert len(capped) <= FULL_TEXT_CHARS
    assert capped.startswith("بداية الحكم") and capped.endswith(ending)
    assert _RULING_GAP in capped and GUIDE_SLICE_GAP_AR not in capped
    assert cap("قصير", "cases") == "قصير"


# ── Quotes and reading errors ─────────────────────────────────────────────────


def test_the_full_text_is_quotable_and_the_search_view_is_not() -> None:
    deps = _deps()
    _read(deps, "CR1")
    label = _labels(deps)["CR1"]
    result = finalize(deps, _out(quotes=[
        QuoteLLM(source=label, text=RULING_SAYS),
        QuoteLLM(source=label, text=SEARCH_VIEW),
    ]))
    assert [q.text for q in result.quotes] == [RULING_SAYS]
    assert (result.quotes[0].domain, result.quotes[0].open_key) == ("cases", "CR1")
    assert result.quotes_dropped == 1


def _error(deps: AnalyzerDeps, report_text: str = REPORT_SAYS, full: str = RULING_SAYS):
    return ReadingErrorLLM(
        report="R1", report_text=report_text, source=_labels(deps)["CR1"], full_text=full,
    )


def test_a_reading_error_replaces_the_wrong_sentence_and_keeps_sufficient() -> None:
    deps = _deps()
    _read(deps, "CR1")
    result = finalize(deps, _out(
        quotes=[QuoteLLM(source="R1", text=REPORT_SAYS)], errors=[_error(deps)],
    ))
    assert result.verdict == "sufficient"
    (correction,) = result.reading_errors
    assert (correction.report_text, correction.corrected_text) == (REPORT_SAYS, RULING_SAYS)
    assert (correction.report_id, correction.open_key, correction.domain) == ("wi-1", "CR1", "cases")
    # The report's wrong words are never printed; the ruling's are.
    assert [q.text for q in result.quotes] == [RULING_SAYS]


def test_a_correction_alone_is_enough_support() -> None:
    deps = _deps()
    _read(deps, "CR1")
    assert finalize(deps, _out(errors=[_error(deps)])).verdict == "sufficient"


def test_a_reading_error_must_be_word_for_word_on_both_sides() -> None:
    deps = _deps()
    _read(deps, "CR1")
    bad = [
        _error(deps, report_text="جملة لم ترد في التقرير"),
        _error(deps, full="جملة لم ترد في الحكم"),
        _error(deps, full=REPORT_SAYS),
    ]
    result = finalize(deps, _out(quotes=[QuoteLLM(source="R1", text=REPORT_SAYS)], errors=bad))
    assert result.reading_errors == [] and result.reading_errors_dropped == 3
    assert [q.text for q in result.quotes] == [REPORT_SAYS]


def test_a_reading_error_needs_the_source_read_in_full() -> None:
    deps = _deps()
    assert finalize(deps, _out(errors=[_error(deps)])).reading_errors == []


def test_the_same_correction_twice_is_kept_once() -> None:
    deps = _deps()
    _read(deps, "CR1")
    assert len(finalize(deps, _out(errors=[_error(deps), _error(deps)])).reading_errors) == 1


# ── D10: read before relying ─────────────────────────────────────────────────


def test_a_report_sentence_on_an_unread_ruling_must_be_checked_first() -> None:
    deps = _deps()
    out = _out(quotes=[QuoteLLM(source="R1", text=REPORT_SAYS)])
    assert unread_citations(deps, out) == [(REPORT_SAYS, _labels(deps)["CR1"])]
    _read(deps, "CR1")
    assert unread_citations(deps, out) == []


def test_no_check_when_the_tool_is_off_or_the_reads_are_used_up() -> None:
    out = _out(quotes=[QuoteLLM(source="R1", text=REPORT_SAYS)])
    assert unread_citations(_deps(full_text=False), out) == []
    deps = _deps()
    _read(deps, "CR2", "CR3", "CR4")
    assert unread_citations(deps, out) == []


# ── End to end, with a scripted model ─────────────────────────────────────────


def _final(quotes: list[dict], errors: list[dict] | None = None) -> dict:
    out = {"restatement": "س", "verdict": "sufficient", "quotes": quotes, "missing": []}
    if errors is not None:
        out["reading_errors"] = errors
    return out


def _script(steps: list, seen: dict) -> FunctionModel:
    it = iter(steps)

    def respond(messages: list, info: Any) -> ModelResponse:
        seen["tools"] = {t.name for t in info.function_tools}
        seen.setdefault("returns", []).extend(
            p.content for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
        )
        seen.setdefault("retries", []).extend(
            str(p.content) for m in messages for p in m.parts if isinstance(p, RetryPromptPart)
        )
        step = next(it)
        if isinstance(step, dict):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, step)])
        return ModelResponse(parts=[step])

    return FunctionModel(respond)


def _run(monkeypatch, steps: list, *, on: bool = True) -> tuple[Any, dict]:
    seen: dict = {}
    monkeypatch.setattr(runner_mod, "full_text_enabled", lambda _user: on)
    monkeypatch.setattr(agent_mod, "get_agent_model", lambda _slot: _script(steps, seen))
    return asyncio.run(analyze(_call(), _db())), seen


def test_off_the_model_sees_neither_the_tool_nor_the_field(monkeypatch) -> None:
    result, seen = _run(monkeypatch, [_final([{"source": "R1", "text": REPORT_SAYS}])], on=False)
    assert "read_full_text" not in seen["tools"] and "open_sources" in seen["tools"]
    assert result.verdict == "sufficient" and result.full_read_n == 0


def test_the_model_reads_in_full_and_records_the_correction(monkeypatch) -> None:
    result, seen = _run(monkeypatch, [
        ToolCallPart("read_full_text", {"labels": ["S1"]}),
        _final([], [{"report": "R1", "report_text": REPORT_SAYS, "source": "S1",
                     "full_text": RULING_SAYS}]),
    ])
    assert "read_full_text" in seen["tools"]
    assert SEARCH_VIEW in seen["returns"][0] and FULL_RULING in seen["returns"][0]
    assert result.verdict == "sufficient" and result.full_read_n == 1
    assert [c.corrected_text for c in result.reading_errors] == [RULING_SAYS]


def test_relying_on_an_unread_ruling_sends_the_model_back_to_read(monkeypatch) -> None:
    quoted = [{"source": "R1", "text": REPORT_SAYS}]
    result, seen = _run(monkeypatch, [
        _final(quoted),
        ToolCallPart("read_full_text", {"labels": ["S1"]}),
        _final(quoted, []),
    ])
    assert any("read_full_text" in r for r in seen["retries"])
    assert result.full_read_n == 1 and result.verdict == "sufficient"


def test_a_fixed_answer_that_adds_an_unread_ruling_is_checked_again(monkeypatch) -> None:
    # Seen in the replay: the one retry went to a bad quote, and the fixed answer
    # then leaned on a ruling nobody read.
    result, seen = _run(monkeypatch, [
        _final([{"source": "R1", "text": "جملة مختلقة"}]),
        _final([{"source": "R1", "text": REPORT_SAYS}]),
        ToolCallPart("read_full_text", {"labels": ["S1"]}),
        _final([{"source": "R1", "text": REPORT_SAYS}], []),
    ])
    assert any("not word for word" in r for r in seen["retries"])
    assert any("read_full_text" in r for r in seen["retries"])
    assert result.full_read_n == 1 and not result.degraded


def test_off_there_is_still_only_one_retry(monkeypatch) -> None:
    result, _ = _run(monkeypatch, [
        _final([{"source": "R1", "text": "جملة مختلقة"}]),
        _final([{"source": "R1", "text": REPORT_SAYS}]),
    ], on=False)
    assert not result.degraded and result.verdict == "sufficient"


def test_the_full_text_schema_is_still_an_analyzer_output() -> None:
    assert issubclass(AnalyzerOutputFullLLM, AnalyzerOutputLLM)
    assert "reading_errors" not in AnalyzerOutputLLM.model_fields


def test_the_turn_message_marks_guided_services(monkeypatch) -> None:
    db = _db()
    reports = load_reports(db, _call())
    message = render_turn_message(_call(), reports, load_sources(db, reports, full_text=True))
    assert message.count("**له دليل شامل**") == 1
