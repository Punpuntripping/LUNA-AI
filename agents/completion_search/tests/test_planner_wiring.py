"""The search planner with Completion Search on and off (plan §4.4, §4.10).

Off must be today's planner, byte for byte. On swaps the tool AND the prompt section
together, feeds the ``statute_articles`` block from the turn's pins, and carries
those pins across an ``ask_user`` pause.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agents.deep_search_v4.planner.agent as planner_agent
import agents.deep_search_v4.planner.prompts as prompts
import agents.orchestrator as orchestrator
import agents.tool_repository.completion_search_tool as tool
from agents.completion_search.tests.fakes import CIVIL
from agents.completion_search.tests.test_planner_tool import PLAIN, Deps, history_from, planner
from agents.deep_search_v4.orchestrator import _build_candidate_context_blocks
from agents.deep_search_v4.planner.deps import build_planner_deps


def tools_of(agent) -> list[str]:
    return sorted(agent._function_toolset.tools)


# --------------------------------------------------------------------------- #
# Off = today
# --------------------------------------------------------------------------- #


def test_off_is_the_same_prompt_object_and_the_same_tools():
    assert prompts.decider_system_prompt(False) is prompts.PLANNER_DECIDER_SYSTEM_PROMPT
    assert prompts.planner_brief_detail_rules(False) is prompts._PLANNER_BRIEF_DETAIL_RULES
    assert tools_of(planner_agent.create_planner_decider()) == ["ask_user", "fetch_article", "unfold_workspace_item"]


def test_the_flag_reaches_the_deps(monkeypatch):
    async def embed(_q):
        return []

    for flag in (False, True):
        monkeypatch.setattr("agents.utils.citation_pin_flags.completion_search_enabled", lambda uid, f=flag: f)
        deps = build_planner_deps(supabase=object(), embedding_fn=embed, user_id="u1")
        assert deps.completion_search is flag
        assert deps._turn_pins == {} and deps._pending_pins == {}


# --------------------------------------------------------------------------- #
# On: the tool and the prompt change together
# --------------------------------------------------------------------------- #


def test_on_swaps_the_tool():
    agent = planner_agent.create_planner_decider(completion_search=True)
    assert tools_of(agent) == ["ask_user", "complete_search", "keep_results", "unfold_workspace_item"]


def test_on_the_prompt_never_names_the_old_tool():
    on = prompts.decider_system_prompt(True)
    assert "fetch_article" not in on
    assert "## The `complete_search` tool — pull the official wording of a named text" in on
    assert "keep_results" in on and "[K-…]" in on and "[P-…]" in on
    assert "`statute_articles`, the official texts you pinned with `complete_search`" in on
    assert "the texts you pinned with `complete_search` (forwarded verbatim)" in on
    assert "fetch_article" not in prompts.planner_brief_detail_rules(True)
    assert "fetch it with `complete_search`" in prompts.planner_brief_detail_rules(True)


def test_on_keeps_every_other_line_of_the_prompt():
    off, on = prompts.decider_system_prompt(False), prompts.decider_system_prompt(True)
    start = off.index(prompts._FETCH_ARTICLE_HEADING)
    end = off.index(prompts._PLANNER_BRIEF_HEADING)
    assert on.startswith(off[:off.index("Four kinds of block")])          # everything before the first swap
    assert on.endswith(off[off.index("so never retell them here") + 400:])  # and the long tail after the last
    for kept in ("رقم مادة بلا نظام؟ اسأل، لا تخمّن ولا تبحث", "**Still run the normal search.**",
                 "**TEXT ONLY**", "**The citation does not have to be the user's.**",
                 "An attached item cites it", "Check the title in the returned header"):
        assert kept in off[start:end] and kept in on, kept


def test_the_rules_the_old_section_carried_are_all_still_there():
    on = prompts.decider_system_prompt(True)
    section = on[on.index("## The `complete_search` tool"):on.index(prompts._PLANNER_BRIEF_HEADING)]
    assert "one request may ask for several things" in section.lower()
    assert "by its case number only" in section
    assert "reaches the executors and the aggregator on its own" in section
    assert "a wrong article reads exactly like a right one" in section


def test_a_prompt_that_cannot_be_built_keeps_the_old_tool(monkeypatch):
    def broken(_on):
        raise ValueError("section moved")

    monkeypatch.setattr(planner_agent, "decider_system_prompt", broken)
    agent = planner_agent.create_planner_decider(completion_search=True)
    assert "fetch_article" in tools_of(agent) and "complete_search" not in tools_of(agent)


def test_the_swap_fails_loudly_when_the_base_prompt_changes(monkeypatch):
    monkeypatch.setattr(prompts, "PLANNER_DECIDER_SYSTEM_PROMPT", "a prompt with no such section")
    with pytest.raises(ValueError):
        prompts.decider_system_prompt(True)


def test_the_output_schema_names_no_tool_that_may_be_absent():
    from agents.deep_search_v4.planner.models import PlannerDecision

    description = PlannerDecision.model_fields["planner_brief"].description
    assert "fetch_article" not in description and "lookup tool" in description


# --------------------------------------------------------------------------- #
# The block the search agents read
# --------------------------------------------------------------------------- #


def block_of(deps):
    decision = SimpleNamespace(planner_brief="", context_labels=[])
    return _build_candidate_context_blocks(decision, deps).get("statute_articles")


def planner_like_deps(**extra):
    base = dict(case_brief=None, prior_searches=[], conversation_memory_md=None, _flushed_articles=[],
                _turn_pins={}, analyzer_handoff=None, analyzer_checked=False, prior_coverage=None)
    return SimpleNamespace(**{**base, **extra})


def test_the_block_is_built_from_the_turns_pins():
    deps, _, _, library = planner(("complete_search", {"request": PLAIN}))
    block = block_of(planner_like_deps(_turn_pins=deps._turn_pins))
    assert block is not None and block.label == "statute_articles" and block.persistence == "turn"
    # the heading the search agents' prompts name for an article
    assert f"## نص المادة 56 من {CIVIL}\n\n{library.laws[CIVIL]['articles']['56']}" in block.body
    assert f"## نص المادة 234 من {CIVIL}" in block.body
    assert "[P-" not in block.body


def test_without_pins_the_old_path_is_untouched():
    assert block_of(planner_like_deps()) is None
    old = planner_like_deps(_flushed_articles=[
        {"regulation": "نظام العمل", "article_number": "77", "content": "نص المادة.", "confidence": "high"},
    ])
    assert block_of(old).body == "> 📌 نصوص المواد المثبّتة من البحث\n\n## نص المادة 77 من نظام العمل\n\nنص المادة."


def test_a_ruling_in_the_block_comes_under_its_own_title():
    body = tool.render_pins_block([
        {"source_type": "case", "title": "الحكم في القضية رقم 4455 — التجارية", "text": "نص الحكم."},
        {"source_type": "article", "title": "المادة 77 من نظام العمل", "text": "نص المادة."},
    ])
    assert "## الحكم في القضية رقم 4455 — التجارية\n\nنص الحكم." in body
    assert "## نص المادة 77 من نظام العمل\n\nنص المادة." in body


# --------------------------------------------------------------------------- #
# Across an ask_user pause
# --------------------------------------------------------------------------- #


def test_off_nothing_is_carried():
    resume_deps = SimpleNamespace(completion_search=False)
    assert asyncio.run(orchestrator._completion_pins_after_resume(resume_deps, object())) == ([], [])


def test_pins_from_before_and_after_the_pause_reach_the_search(monkeypatch):
    # Leg 1 (before the pause) pinned two articles on deps that no longer exist.
    before, _, messages, library = planner(("complete_search", {"request": PLAIN}))
    monkeypatch.setattr(tool, "_reopen", lambda *a, **k: "")

    # Leg 2 (the resumed decider) runs on fresh deps and pins one more.
    resume = Deps(supabase=before.supabase)
    resume.completion_search = True
    resume._turn_pins["article:late"] = {
        "key": "article:late", "short_id": tool.short_id("article:late"), "source_type": "article",
        "title": "المادة 80 من نظام العمل", "text": "نص المادة 80.", "confidence": "high", "note": "",
    }
    resume._events.append({"type": "workspace_item_updated", "item_id": "item-1"})
    resume._events.append({"event": "planner_decided"})
    result = SimpleNamespace(all_messages=lambda: history_from(messages))

    pins, events = asyncio.run(orchestrator._completion_pins_after_resume(resume, result))
    assert [p["title"] for p in pins] == [
        f"المادة 56 من {CIVIL}", f"المادة 234 من {CIVIL}", "المادة 80 من نظام العمل",
    ]
    assert pins[0]["text"] == library.laws[CIVIL]["articles"]["56"]
    assert events == [{"type": "workspace_item_updated", "item_id": "item-1"}]


def test_a_failure_while_carrying_never_stops_the_search():
    class Broken:
        completion_search = True

        def __getattr__(self, name):
            raise RuntimeError("deps gone")

    result = SimpleNamespace(all_messages=lambda: [])
    assert asyncio.run(orchestrator._completion_pins_after_resume(Broken(), result)) == ([], [])
