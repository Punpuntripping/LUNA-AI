"""The writer planner with Completion Search on and off (plan §4.4).

Off must be today's planner, byte for byte. On adds the two tools together with their
prompt passage, and the texts the planner pins reach the writer as ONE extra source —
inside ``<sources>``, where the writer's own rules already let it cite.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agents.writer_planner.agent as wp_agent
import agents.writer_planner.prompts as wp_prompts
import agents.writer_planner.runner as wp_runner
from agents.completion_search.tests.fakes import CIVIL
from agents.completion_search.tests.test_planner_tool import PLAIN, Deps, history_from, planner
from agents.writer.models import WriterPackage
from agents.writer.prompts import render_package_for_system_prompt
from agents.writer_planner.deps import build_writer_planner_deps


def tools_of(agent) -> list[str]:
    return sorted(agent._function_toolset.tools)


def package() -> WriterPackage:
    return WriterPackage(intent_ar="مذكرة جوابية", subtype="memo", edit_mode="fresh")


def writer_deps(source: Deps, *, on: bool = True) -> Deps:
    """Fresh deps on the same conversation — what a resumed leg has."""
    deps = Deps(supabase=source.supabase)
    deps.completion_search = on
    return deps


# --------------------------------------------------------------------------- #
# Off = today
# --------------------------------------------------------------------------- #


def test_off_is_the_same_prompt_object_and_the_same_tools():
    assert wp_prompts.writer_planner_system_prompt(False) is wp_prompts.WRITER_PLANNER_SYSTEM_PROMPT
    assert tools_of(wp_agent.create_writer_planner_decider()) == [
        "add_user_template", "ask_user", "present_plan_for_approval", "unfold_workspace_item",
    ]


def test_the_flag_reaches_the_deps(monkeypatch):
    for flag in (False, True):
        monkeypatch.setattr("agents.utils.citation_pin_flags.completion_search_enabled", lambda uid, f=flag: f)
        deps = build_writer_planner_deps(supabase=object(), user_id="u1", conversation_id="c1")
        assert deps.completion_search is flag and deps._turn_pins == {}


def test_off_adds_nothing_to_the_package():
    source, _, messages, _ = planner(("complete_search", {"request": PLAIN}), caller="writer_planner")
    pkg = package()
    result = SimpleNamespace(all_messages=lambda: history_from(messages))
    asyncio.run(wp_runner._add_pinned_texts(pkg, writer_deps(source, on=False), result))
    assert pkg.analyzed_items == []
    assert wp_runner._pinned_card_events(writer_deps(source, on=False)) == []


# --------------------------------------------------------------------------- #
# On: the tools and the passage come together
# --------------------------------------------------------------------------- #


def test_on_adds_the_two_tools():
    assert tools_of(wp_agent.create_writer_planner_decider(completion_search=True)) == [
        "add_user_template", "ask_user", "complete_search", "keep_results",
        "present_plan_for_approval", "unfold_workspace_item",
    ]


def test_on_the_passage_sits_right_after_the_tools_table_and_nothing_else_changes():
    off, on = wp_prompts.writer_planner_system_prompt(False), wp_prompts.writer_planner_system_prompt(True)
    assert on.replace(f"{wp_prompts.COMPLETE_SEARCH_PASSAGE}\n", "") == off
    assert on.index("`present_plan_for_approval(plan_md)` |") < on.index("## `complete_search` and `keep_results`")
    assert on.index("## `complete_search` and `keep_results`") < on.index(wp_prompts._AFTER_TOOLS_HEADING)


def test_the_passage_agrees_with_the_rest_of_the_prompt():
    passage = wp_prompts.COMPLETE_SEARCH_PASSAGE
    # the planner's own rules: placeholders, not guesses; no raw text carried in its fields
    assert "placeholder in the plan" in passage and "not a guess" in passage
    assert "never copy it into `intent_ar` or `plan_md`" in passage
    assert "does not change the core invariant" in passage
    assert "You do not list it in `selected_wis`" in passage
    assert "[P-…]" in passage and "[K-…]" in passage


def test_a_passage_that_cannot_be_placed_leaves_the_tools_out(monkeypatch):
    def broken(_on):
        raise ValueError("heading moved")

    monkeypatch.setattr(wp_agent, "writer_planner_system_prompt", broken)
    assert "complete_search" not in tools_of(wp_agent.create_writer_planner_decider(completion_search=True))


def test_placing_the_passage_fails_loudly_when_the_base_prompt_changes(monkeypatch):
    monkeypatch.setattr(wp_prompts, "WRITER_PLANNER_SYSTEM_PROMPT", "a prompt with no such heading")
    with pytest.raises(ValueError):
        wp_prompts.writer_planner_system_prompt(True)


# --------------------------------------------------------------------------- #
# The pinned texts reach the writer as one source
# --------------------------------------------------------------------------- #


def test_the_runs_pins_become_one_source_in_the_package():
    source, _, messages, library = planner(("complete_search", {"request": PLAIN}), caller="writer_planner")
    source.supabase.card["wi_seq"] = 7
    pkg = package()
    result = SimpleNamespace(all_messages=lambda: history_from(messages))
    # fresh deps: the pins were made before the plan-approval pause
    asyncio.run(wp_runner._add_pinned_texts(pkg, writer_deps(source), result))

    (item,) = pkg.analyzed_items
    assert (item.kind, item.role, item.need, item.wi_seq, item.title) == (
        "pinned_texts", "source", "full", 7, "النصوص المثبّتة",
    )
    assert item.item_id == source.supabase.card["item_id"]
    assert wp_runner.PINNED_SOURCE_NOTE_AR in item.body_md
    assert f"## نص المادة 56 من {CIVIL}\n\n{library.laws[CIVIL]['articles']['56']}" in item.body_md
    assert "[P-" not in item.body_md and item.word_count_after == item.word_count_before > 0


def test_the_writer_reads_it_inside_sources_and_never_as_an_exhibit():
    source, _, messages, _ = planner(("complete_search", {"request": PLAIN}), caller="writer_planner")
    source.supabase.card["wi_seq"] = 7
    pkg = package()
    asyncio.run(wp_runner._add_pinned_texts(
        pkg, writer_deps(source), SimpleNamespace(all_messages=lambda: history_from(messages))))
    rendered = render_package_for_system_prompt(pkg)
    sources = rendered[rendered.index("<sources>"):rendered.index("</sources>")]
    assert '<source kind="pinned_texts" wi="WI-7" need="full">' in sources
    assert "نص المادة 56" in sources
    assert 'kind="attachment"' not in rendered           # only an attachment is an exhibit


def test_nothing_pinned_adds_nothing():
    source, _, _, _ = planner(("keep_results", {"result_ids": []}), caller="writer_planner")
    pkg = package()
    asyncio.run(wp_runner._add_pinned_texts(
        pkg, writer_deps(source), SimpleNamespace(all_messages=lambda: [])))
    assert pkg.analyzed_items == []


def test_a_failure_never_stops_the_draft():
    class Broken:
        completion_search = True

        def __getattr__(self, name):
            raise RuntimeError("deps gone")

    pkg = package()
    asyncio.run(wp_runner._add_pinned_texts(pkg, Broken(), SimpleNamespace(all_messages=lambda: [])))
    assert pkg.analyzed_items == []


def test_the_card_events_of_this_leg_are_relayed():
    source, _, _, _ = planner(("complete_search", {"request": PLAIN}), caller="writer_planner")
    source.completion_search = True
    source._events.append({"event": "something_else"})
    assert [e["type"] for e in wp_runner._pinned_card_events(source)] == ["workspace_item_created"]
