"""``complete_search`` / ``keep_results`` as a planner calls them (plan §4.4).

A scripted "planner" calls the tools; the helper behind them runs on the in-memory
library, and on a scripted model when the request needs one. Synthetic text only.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import agents.completion_search.runner as helper_runner
import agents.tool_repository.completion_search_tool as tool
from agents.completion_search.agent import create_completion_agent
from agents.completion_search.tests.fakes import CIVIL, PROC, FakeLibrary, answer, scripted_model
from shared.workspace.tests.test_pinned_texts import CardDb

USER, CONV = "user-1", "conv-1"


@dataclass
class Deps:
    supabase: Any
    user_id: str = USER
    conversation_id: str = CONV
    _events: list = field(default_factory=list)
    _turn_pins: dict = field(default_factory=dict)
    _pending_pins: dict = field(default_factory=dict)


def planner(*calls: tuple[str, dict], library: FakeLibrary | None = None, caller: str = "deep_planner"):
    """A planner that makes ``calls`` one per step, then answers. Returns (deps, tool returns, messages)."""
    library = library or FakeLibrary()
    step = {"n": 0}

    async def fn(messages: list, info: AgentInfo) -> ModelResponse:
        i = step["n"]
        step["n"] += 1
        if i < len(calls):
            return ModelResponse(parts=[ToolCallPart(calls[i][0], calls[i][1])])
        return ModelResponse(parts=[TextPart("تم")])

    agent: Agent[Deps, str] = Agent(FunctionModel(fn), deps_type=Deps, output_type=str)
    tool.register_completion_search(agent, caller, library_factory=lambda deps: library)
    deps = Deps(supabase=CardDb())
    # The user's message carries what the planner asks for: a name counts as written
    # by the user only when it stands in what the planner was given.
    said = " — ".join(str(args.get("request", "")) for _name, args in calls if args.get("request")) or "طلب"
    result = asyncio.run(agent.run(said, deps=deps))
    returns = [
        str(part.content) for m in result.all_messages() for part in getattr(m, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]
    return deps, returns, result.all_messages(), library


@pytest.fixture
def helper_model(monkeypatch):
    """Give the helper behind the tool a scripted model for the requests code cannot answer."""
    def use(*steps):
        agent = create_completion_agent(model=scripted_model(*steps))
        monkeypatch.setattr(helper_runner, "get_completion_agent", lambda: agent)
    return use


@pytest.fixture(autouse=True)
def _no_masking(monkeypatch):
    monkeypatch.setattr(tool, "_masking", lambda: None)


PLAIN = "نظام المعاملات المدنية المادة 56 والمادة 234"


# --------------------------------------------------------------------------- #
# High confidence: pinned at once
# --------------------------------------------------------------------------- #


def test_a_plain_request_is_pinned_on_the_card_and_kept_for_the_turn():
    deps, returns, _, library = planner(("complete_search", {"request": PLAIN}))
    text = returns[0]
    assert text.count("[P-") == 2 and "[K-" not in text
    assert f"## المادة 56 من {CIVIL} [P-" in text and "الحالة: وُجد — ثقة عالية" in text
    assert library.laws[CIVIL]["articles"]["56"] in text

    assert [rec["title"] for rec in deps._turn_pins.values()] == [f"المادة 56 من {CIVIL}", f"المادة 234 من {CIVIL}"]
    card = deps.supabase.card
    assert [e["title"] for e in card["metadata"]["entries"]] == [f"المادة 56 من {CIVIL}", f"المادة 234 من {CIVIL}"]
    assert card["metadata"]["entries"][0]["cited_in"][0]["by"] == "deep_planner"
    assert [e["type"] for e in deps._events] == ["workspace_item_created"]
    assert deps._events[0]["kind"] == "pinned_texts"


def test_the_planner_reads_a_shortened_text_but_the_pin_is_whole():
    laws = {CIVIL: {"id": "reg-civil", "status": "in_force", "articles": {"56": "سطر من المادة.\n" * 600}}}
    deps, returns, _, _ = planner(("complete_search", {"request": "المادة 56 من نظام المعاملات المدنية"}),
                                  library=FakeLibrary(laws))
    full = laws[CIVIL]["articles"]["56"].strip()
    assert len(returns[0]) < len(full) and len(returns[0]) < 4_600
    assert next(iter(deps._turn_pins.values()))["text"].strip() == full
    assert full in deps.supabase.card["content_md"]


def test_a_second_request_updates_the_same_card():
    deps, _, _, _ = planner(
        ("complete_search", {"request": "المادة 56 من نظام المعاملات المدنية"}),
        ("complete_search", {"request": "المادة 234 من نظام المعاملات المدنية"}),
        ("complete_search", {"request": "المادة 56 من نظام المعاملات المدنية"}),
    )
    assert [e["type"] for e in deps._events] == [
        "workspace_item_created", "workspace_item_updated", "referenced_existing_item",
    ]
    assert len(deps.supabase.rows) == 1 and len(deps._turn_pins) == 2


def test_the_writer_planner_is_named_on_the_card():
    deps, _, _, _ = planner(("complete_search", {"request": PLAIN}), caller="writer_planner")
    cited = deps.supabase.card["metadata"]["entries"][0]["cited_in"][0]
    assert cited["by"] == "writer_planner" and "الصياغة" in cited["label"]


# --------------------------------------------------------------------------- #
# Medium confidence: the planner decides
# --------------------------------------------------------------------------- #

MEDIUM_STEPS = (
    lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام المرافعات", "article_number": "76"})],
    lambda _: [("get_article", {"search_id": "S1", "regulation": PROC, "article_number": "76"})],
    lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}),
)


def test_a_medium_result_is_offered_not_pinned(helper_model):
    helper_model(*MEDIUM_STEPS)
    deps, returns, _, _ = planner(("complete_search", {"request": "المادة 76 من نظام المرافعات"}))
    assert "[K-" in returns[0] and "[P-" not in returns[0]
    assert "ثقة متوسطة" in returns[0] and "keep_results" in returns[0]
    assert deps._turn_pins == {} and deps.supabase.rows == [] and deps._events == []
    assert len(deps._pending_pins) == 1


def test_keep_results_pins_what_the_planner_accepts(helper_model):
    helper_model(*MEDIUM_STEPS)
    probe, first, _, _ = planner(("complete_search", {"request": "المادة 76 من نظام المرافعات"}))
    tag = "K-" + next(iter(probe._pending_pins))

    helper_model(*MEDIUM_STEPS)
    deps, returns, _, _ = planner(
        ("complete_search", {"request": "المادة 76 من نظام المرافعات"}),
        ("keep_results", {"result_ids": [f"[{tag}]"]}),
    )
    assert returns[1] == f"ثُبّت: المادة 76 من {PROC} [P-{tag[2:]}]"
    assert deps._pending_pins == {} and len(deps._turn_pins) == 1
    entry = deps.supabase.card["metadata"]["entries"][0]
    assert (entry["title"], entry["confidence"]) == (f"المادة 76 من {PROC}", "medium")
    assert [e["type"] for e in deps._events] == ["workspace_item_created"]


def test_keep_results_with_an_unknown_tag_pins_nothing():
    deps, returns, _, _ = planner(("keep_results", {"result_ids": ["K-deadbeef", "whatever"]}))
    assert returns[0].count("غير معروف") == 2
    assert deps._turn_pins == {} and deps.supabase.rows == []
    assert planner(("keep_results", {"result_ids": []}))[1][0] == tool.NOTHING_KEPT_AR


def test_keeping_a_text_twice_is_harmless(helper_model):
    helper_model(*MEDIUM_STEPS)
    probe, _, _, _ = planner(("complete_search", {"request": "المادة 76 من نظام المرافعات"}))
    tag = "K-" + next(iter(probe._pending_pins))
    helper_model(*MEDIUM_STEPS)
    deps, returns, _, _ = planner(
        ("complete_search", {"request": "المادة 76 من نظام المرافعات"}),
        ("keep_results", {"result_ids": [tag]}),
        ("keep_results", {"result_ids": [tag]}),
    )
    assert returns[1] == returns[2]
    assert len(deps.supabase.card["metadata"]["entries"]) == 1


# --------------------------------------------------------------------------- #
# Failures never break the planner
# --------------------------------------------------------------------------- #


def test_a_broken_library_returns_one_arabic_line(helper_model):
    class Broken(FakeLibrary):
        async def title_index(self):
            raise RuntimeError("down")

        async def find_law(self, name):
            raise RuntimeError("down")

    helper_model(lambda _: [("get_article", {"search_id": "S1", "regulation": "نظام العمل", "article_number": "77"})],
                 lambda _: answer())
    deps, returns, _, _ = planner(("complete_search", {"request": "المادة 77 من نظام العمل"}), library=Broken())
    assert returns[0] == tool.FAILED_AR or "لم يتبيّن" in returns[0] or "الحالة" in returns[0]
    assert deps.supabase.rows == []


def test_a_card_that_cannot_be_written_still_returns_the_text():
    class NoTable:
        def table(self, _name):
            raise RuntimeError("database down")

    library = FakeLibrary()
    step = {"n": 0}

    async def fn(messages: list, info: AgentInfo) -> ModelResponse:
        step["n"] += 1
        if step["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("complete_search", {"request": PLAIN})])
        return ModelResponse(parts=[TextPart("تم")])

    agent: Agent[Deps, str] = Agent(FunctionModel(fn), deps_type=Deps, output_type=str)
    tool.register_completion_search(agent, "deep_planner", library_factory=lambda deps: library)
    deps = Deps(supabase=NoTable())
    result = asyncio.run(agent.run(PLAIN, deps=deps))
    returned = [str(p.content) for m in result.all_messages() for p in getattr(m, "parts", [])
                if isinstance(p, ToolReturnPart)][0]
    assert returned.count("[P-") == 2
    assert len(deps._turn_pins) == 2 and deps._events == []      # the agents after the planner still get the text


def test_a_helper_that_times_out_returns_the_failure_line(monkeypatch):
    async def slow(*_a, **_k):
        await asyncio.sleep(5)

    monkeypatch.setattr(tool, "TOOL_TIMEOUT_S", 0.05)
    monkeypatch.setattr("agents.completion_search.run_completion_search", slow)
    _, returns, _, _ = planner(("complete_search", {"request": PLAIN}))
    assert returns[0] == tool.FAILED_AR


# --------------------------------------------------------------------------- #
# وضع السرية — the real case number exists only inside the lookup
# --------------------------------------------------------------------------- #

REAL, FAKE = "4455", "9911"            # stand-ins: real numbers are 8+ digits, the rule is the same


def test_a_masked_case_number_is_decoded_for_the_lookup_only(monkeypatch, helper_model):
    monkeypatch.setattr(tool, "_masking", lambda: (lambda t: t.replace(REAL, FAKE), lambda t: t.replace(FAKE, REAL)))
    seen_by_model: list[str] = []

    def first(_returns):
        return [("find_case", {"search_id": "S1", "case_number": FAKE})]

    def last(returns):
        seen_by_model.extend(returns)
        return answer({"search_id": "S1", "selected": ["T1"], "status": "found"})

    helper_model(first, last)
    deps, returns, _, library = planner(("complete_search", {"request": f"الحكم في القضية رقم {FAKE}"}))

    assert ("cases", REAL, "", "") in library.calls                # the database was asked for the real number
    assert REAL not in " ".join(seen_by_model) and FAKE in seen_by_model[0]   # the helper's model saw the fake
    assert REAL not in returns[0] and FAKE in returns[0]            # … and so did the planner
    record = next(iter(deps._turn_pins.values()))
    assert FAKE in record["title"] and REAL not in record["title"]  # the agents after it read encoded text
    entry = deps.supabase.card["metadata"]["entries"][0]
    assert REAL in entry["title"] and FAKE not in entry["title"]    # the card stores the real text


def test_masked_library_leaves_laws_alone():
    library = FakeLibrary()
    masked = tool.MaskedLibrary(library, encode=lambda t: f"<{t}>", decode=lambda t: t.strip("<>"))
    lookup = asyncio.run(masked.find_law("نظام العمل"))
    assert lookup.law.title == "نظام العمل"
    assert asyncio.run(masked.open_text("circular", "circ-1", "عنوان")) == "نص التعميم الأول."
    assert asyncio.run(masked.open_text("case", "case-1", "<عنوان>")) == "<نص الحكم الأول كاملاً.>"


# --------------------------------------------------------------------------- #
# After a pause: the turn's pins are rebuilt from the history and the card
# --------------------------------------------------------------------------- #


def history_from(messages: list) -> list:
    """Only what a resumed run has: the earlier messages — on FRESH deps."""
    return [m for m in messages if isinstance(m, (ModelRequest, ModelResponse))]


def test_pins_made_before_a_pause_are_rebuilt_on_fresh_deps():
    first, _, messages, library = planner(("complete_search", {"request": PLAIN}))
    fresh = Deps(supabase=first.supabase)                 # new deps, same conversation
    assert fresh._turn_pins == {}
    pins = asyncio.run(tool.collect_turn_pins(fresh, history_from(messages), library=library))
    assert [p["title"] for p in pins] == [f"المادة 56 من {CIVIL}", f"المادة 234 من {CIVIL}"]
    assert pins[0]["text"] == library.laws[CIVIL]["articles"]["56"]
    assert len(fresh._turn_pins) == 2


def test_a_ruling_pinned_before_a_pause_is_opened_again_in_full(helper_model):
    helper_model(lambda _: [("find_case", {"search_id": "S1", "case_number": "4455"})],
                 lambda _: answer({"search_id": "S1", "selected": ["T1"], "status": "found"}))
    first, _, messages, library = planner(("complete_search", {"request": "الحكم في القضية رقم 4455"}))
    fresh = Deps(supabase=first.supabase)
    pins = asyncio.run(tool.collect_turn_pins(fresh, history_from(messages), library=library))
    assert [p["source_type"] for p in pins] == ["case"]
    assert pins[0]["text"] == "نص الحكم الأول كاملاً."


def test_pins_of_earlier_turns_are_not_pulled_in():
    first, _, _, library = planner(("complete_search", {"request": PLAIN}))
    fresh = Deps(supabase=first.supabase)
    assert asyncio.run(tool.collect_turn_pins(fresh, [], library=library)) == []     # no tag in this run's history


def test_pins_made_on_these_deps_need_no_history():
    deps, _, _, _ = planner(("complete_search", {"request": PLAIN}))
    assert [p["title"] for p in asyncio.run(tool.collect_turn_pins(deps, []))] == [
        f"المادة 56 من {CIVIL}", f"المادة 234 من {CIVIL}",
    ]


def test_a_lost_card_does_not_break_the_turn():
    _, _, messages, library = planner(("complete_search", {"request": PLAIN}))

    class NoTable:
        def table(self, _name):
            raise RuntimeError("down")

    assert asyncio.run(tool.collect_turn_pins(Deps(supabase=NoTable()), history_from(messages), library=library)) == []


# --------------------------------------------------------------------------- #
# The block for the agents after the planner, and the standing rules
# --------------------------------------------------------------------------- #


def test_the_block_holds_every_pinned_text_in_full():
    deps, _, _, library = planner(("complete_search", {"request": PLAIN}))
    block = tool.render_pins_block(deps._turn_pins.values())
    assert block.startswith(tool.BLOCK_MARKER_AR)
    # an article keeps the heading the search agents' prompts name: «## نص المادة …»
    assert f"## نص المادة 56 من {CIVIL}\n\n{library.laws[CIVIL]['articles']['56']}" in block
    assert "[P-" not in block and tool.render_pins_block([]) == ""


def test_card_events_filters_the_card_events():
    deps, _, _, _ = planner(("complete_search", {"request": PLAIN}))
    deps._events.insert(0, {"event": "planner_decided"})
    assert [e["type"] for e in tool.card_events(deps)] == ["workspace_item_created"]


def test_no_fee_and_no_shared_usage():
    source = inspect.getsource(tool)
    assert "record_search_fee" not in source
    assert "usage=" not in source and "ctx.usage" not in source      # the helper bills its own run


def test_short_ids_are_stable():
    assert tool.short_id("article:a77") == tool.short_id("article:a77")
    assert tool.short_id("article:a77") != tool.short_id("article:a78")
    assert tool.PIN_TAG_RE.findall(f"## عنوان [P-{tool.short_id('x')}] ثم [K-{tool.short_id('y')}]") == [tool.short_id("x")]


# --------------------------------------------------------------------------- #
# A name the planner completed on its own is a guess (found in the live check)
# --------------------------------------------------------------------------- #


def planner_with_context(user_message: str, *calls, library: FakeLibrary | None = None):
    """Like ``planner`` but the deps carry what the user actually wrote."""
    library = library or FakeLibrary()
    step = {"n": 0}

    async def fn(messages: list, info: AgentInfo) -> ModelResponse:
        i = step["n"]
        step["n"] += 1
        if i < len(calls):
            return ModelResponse(parts=[ToolCallPart(calls[i][0], calls[i][1])])
        return ModelResponse(parts=[TextPart("تم")])

    agent: Agent[Deps, str] = Agent(FunctionModel(fn), deps_type=Deps, output_type=str)
    tool.register_completion_search(agent, "deep_planner", library_factory=lambda deps: library)
    deps = Deps(supabase=CardDb())
    deps.recent_messages = [SimpleNamespace(role="user", content=user_message)]
    result = asyncio.run(agent.run(user_message, deps=deps))
    returns = [str(p.content) for m in result.all_messages() for p in getattr(m, "parts", [])
               if isinstance(p, ToolReturnPart)]
    return deps, returns


def test_a_law_name_the_user_wrote_is_pinned():
    deps, returns = planner_with_context(
        "ما حكم المادة 76 من نظام المرافعات الشرعية؟",
        ("complete_search", {"request": "نظام المرافعات الشرعية المادة 76"}),
    )
    assert "[P-" in returns[0] and len(deps._turn_pins) == 1


def test_a_law_name_the_planner_completed_is_only_offered():
    # The user wrote «نظام المرافعات»; the planner sent the full name of ONE of the two laws.
    deps, returns = planner_with_context(
        "هل يحق لي الاعتراض على حكم استند إلى المادة 76 من نظام المرافعات؟",
        ("complete_search", {"request": "نظام المرافعات الشرعية المادة 76"}),
    )
    assert "[K-" in returns[0] and "[P-" not in returns[0] and "ثقة متوسطة" in returns[0]
    assert deps._turn_pins == {} and deps.supabase.rows == []
    assert len(deps._pending_pins) == 1


def test_the_planner_can_still_keep_its_guess_on_purpose():
    probe, _ = planner_with_context(
        "المادة 76 من نظام المرافعات",
        ("complete_search", {"request": "نظام المرافعات الشرعية المادة 76"}),
    )
    tag = "K-" + next(iter(probe._pending_pins))
    deps, returns = planner_with_context(
        "المادة 76 من نظام المرافعات",
        ("complete_search", {"request": "نظام المرافعات الشرعية المادة 76"}),
        ("keep_results", {"result_ids": [tag]}),
    )
    assert returns[1].startswith("ثُبّت:") and len(deps._turn_pins) == 1
    assert deps.supabase.card["metadata"]["entries"][0]["confidence"] == "medium"


def test_a_name_found_in_an_attachment_counts_as_written():
    library = FakeLibrary()
    step = {"n": 0}

    async def fn(messages: list, info: AgentInfo) -> ModelResponse:
        step["n"] += 1
        if step["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("complete_search", {"request": "نظام العمل المادة 77"})])
        return ModelResponse(parts=[TextPart("تم")])

    agent: Agent[Deps, str] = Agent(FunctionModel(fn), deps_type=Deps, output_type=str)
    tool.register_completion_search(agent, "deep_planner", library_factory=lambda deps: library)
    deps = Deps(supabase=CardDb())
    deps.recent_messages = [SimpleNamespace(role="user", content="هل تقدر تعترض على الحكم")]
    deps.attached_items = [SimpleNamespace(title="حكم", summary="", content_md="استند الحكم إلى المادة 77 من نِظام العمل.")]
    asyncio.run(agent.run("هل تقدر تعترض على الحكم", deps=deps))
    assert len(deps._turn_pins) == 1


def test_source_text_of_reads_what_the_planner_was_given():
    deps = SimpleNamespace(
        recent_messages=[SimpleNamespace(content="رسالة المستخدم")],
        attached_items=[SimpleNamespace(title="عنوان", summary="ملخص", content_md="نص المرفق")],
        prior_searches=[SimpleNamespace(title="بحث", describe_query="وصف", summary="خلاصة")],
        intent="نية الكاتب", case_brief="ملف القضية",
    )
    text = tool.source_text_of(deps, "السؤال الحالي")
    for piece in ("السؤال الحالي", "رسالة المستخدم", "نص المرفق", "وصف", "نية الكاتب", "ملف القضية"):
        assert piece in text
    assert tool.source_text_of(SimpleNamespace()) is None            # no context: the request is the reference
