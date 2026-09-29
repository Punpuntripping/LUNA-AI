"""The editorial query cap on the reg_compliance expander — prompt + clamp.

The reg expander's user message is ``build_expander_user_message(...)`` plus,
when non-empty, ``build_expander_dynamic_instructions(...)``. The editorial
query cap rides on the second one, LAST.

What is pinned here:

- **``cap=None`` is byte-identical to before the field existed** (D8). Every
  in-app run goes through this path; if the block leaked in unconditionally it
  would change the prompt of every expander call in the product.
- The block is appended **after** the weak-axes guidance. It overrides the
  prompt's own "Number of queries" rule for one run, and an instruction that
  overrides another has to be read after it.
- The cap block never touches the **system** prompt — that string is the
  DashScope prefix-cache key.
- **Ceiling only** (D3). Coming in under the cap is a legitimate answer and
  nothing pads it back up.
"""
from __future__ import annotations

import pytest

from agents.deep_search_v4.reg_compliance_search.models import WeakAxis
from agents.deep_search_v4.reg_compliance_search.prompts import (
    EXPANDER_PROMPTS,
    build_expander_dynamic_instructions,
    build_expander_user_message,
    get_expander_prompt,
)


def _axes() -> list[WeakAxis]:
    return [WeakAxis(reason="لا توجد نتائج عن المدة", suggested_query="مدة فترة التجربة")]


# ===========================================================================
# None ⇒ byte-identical (D8)
# ===========================================================================


def test_no_cap_round_one_is_still_the_empty_string():
    """Round 1 with no weak axes produced "" before the cap — it still does."""
    assert build_expander_dynamic_instructions([], 1) == ""
    assert build_expander_dynamic_instructions([], 1, cap=None) == ""


def test_no_cap_is_byte_identical_to_the_two_argument_call():
    """The new keyword defaulting to None must change nothing, in any round."""
    for axes in ([], _axes()):
        for rnd in (1, 2, 3):
            assert build_expander_dynamic_instructions(
                axes, rnd, cap=None
            ) == build_expander_dynamic_instructions(axes, rnd)


def test_the_whole_user_message_is_byte_identical_when_unpinned():
    """The full assembly the node performs, both halves, unpinned.

    ⚠ LOAD-BEARING — this is the proof that the in-app path is untouched by
    the editorial cap. ``PinnedPlan`` is minted in exactly one place (the
    headless Blog-Post endpoint), so no in-app turn can set a cap; this test is
    what says that a ``None`` cap costs the product nothing.
    """
    base = build_expander_user_message("سؤال", "سياق")
    dyn = build_expander_dynamic_instructions(_axes(), 2, cap=None)
    assembled = f"{base}\n\n{dyn}" if dyn else base

    expected_base = build_expander_user_message("سؤال", "سياق")
    expected_dyn = build_expander_dynamic_instructions(_axes(), 2)
    expected = f"{expected_base}\n\n{expected_dyn}" if expected_dyn else expected_base
    assert assembled == expected
    assert "capped by the editor" not in assembled


# ===========================================================================
# A pinned cap renders the block, last
# ===========================================================================


@pytest.mark.parametrize("cap", [2, 4, 9, 50])
def test_a_pinned_cap_renders_its_block(cap):
    """Any integer renders — there is no level table and no upper bound (D1/D6)."""
    out = build_expander_dynamic_instructions([], 1, cap=cap)
    assert "## Query count for this run — capped by the editor" in out
    assert f"**at most {cap}**" in out
    assert f"only the first {cap} are used" in out


def test_the_block_is_last_after_the_weak_axes_guidance():
    out = build_expander_dynamic_instructions(_axes(), 2, cap=4)
    assert "Re-search instructions (round 2)" in out
    assert out.index("Re-search instructions") < out.index("Query count for this run")
    assert out.rstrip().endswith("only the first 4 are used.")


def test_the_block_matches_the_shared_renderer_exactly():
    """One implementation for both executors — no local copy of the wording."""
    from agents.deep_search_v4.shared.query_cap import render_cap_block

    out = build_expander_dynamic_instructions([], 1, cap=3)
    assert out == render_cap_block(3)


def test_the_cap_never_touches_the_system_prompt():
    """⚠ The system prompt is the prefix-cache key. A per-run block there would
    miss the cache on every expander call in the product, in-app included."""
    for key in EXPANDER_PROMPTS:
        system = get_expander_prompt(key)
        assert "capped by the editor" not in system
        assert "Query count for this run" not in system


def test_a_pinned_cap_does_not_disturb_the_base_user_message():
    """The reg executor carries the block on the dynamic half only — the
    ``build_expander_user_message`` output is untouched by the cap."""
    from agents.deep_search_v4.shared.context import ContextBlock

    blocks = [ContextBlock(label="planner_brief", body="موجز", persistence="turn")]
    with_blocks = build_expander_user_message("سؤال", "سياق", context_blocks=blocks)
    assert "capped by the editor" not in with_blocks
    assert "<context_blocks>" in with_blocks


# ===========================================================================
# The clamp inside ExpanderNode — the prompt asks, the code enforces
# ===========================================================================


class _Ctx:
    """Stand-in for ``GraphRunContext`` — the node reads ``.state`` / ``.deps``."""

    def __init__(self, state, deps) -> None:
        self.state = state
        self.deps = deps


def _fake_expander_agent(queries: list[str]):
    """An expander agent whose run() returns exactly these queries."""
    from unittest.mock import AsyncMock, MagicMock

    from agents.deep_search_v4.reg_compliance_search.models import ExpanderOutput

    result = MagicMock()
    result.output = ExpanderOutput(
        queries=list(queries),
        rationales=[f"سبب {i + 1}" for i in range(len(queries))],
    )
    result.usage = MagicMock(
        return_value=MagicMock(
            requests=1, input_tokens=1, output_tokens=1, total_tokens=2, details=None,
        )
    )
    result.all_messages_json = MagicMock(return_value=b"[]")
    agent = MagicMock()
    agent.run = AsyncMock(return_value=result)
    return agent


async def _run_expander_node(queries: list[str], cap):
    """Drive ``ExpanderNode`` once and hand back its LoopState."""
    from unittest.mock import MagicMock, patch

    from agents.deep_search_v4.reg_compliance_search.loop import ExpanderNode
    from agents.deep_search_v4.reg_compliance_search.models import (
        LoopState,
        RegComplianceSearchDeps,
    )

    state = LoopState(
        focus_instruction="ما مدة فترة التجربة؟",
        user_context="",
        expander_query_cap=cap,
    )
    deps = RegComplianceSearchDeps(supabase=MagicMock(), embedding_fn=MagicMock())
    deps._log_id = ""          # skip the markdown log write
    with patch(
        "agents.deep_search_v4.reg_compliance_search.loop.create_expander_agent",
        return_value=_fake_expander_agent(queries),
    ):
        await ExpanderNode().run(_Ctx(state, deps))
    return state


@pytest.mark.asyncio
async def test_twelve_queries_under_a_cap_of_four_keep_four():
    """The headline invariant: 12 emitted, cap 4, the first 4 kept.

    ``all_queries_used`` must carry the KEPT four: it is what the run reports
    as the queries it ran, and a search was only ever issued for those.
    """
    state = await _run_expander_node([f"استعلام {i}" for i in range(12)], 4)
    assert state.expander_output is not None
    assert len(state.expander_output.queries) == 4
    assert state.expander_output.queries == [f"استعلام {i}" for i in range(4)]
    assert len(state.all_queries_used) == 4
    assert state.all_queries_used == state.expander_output.queries


@pytest.mark.asyncio
async def test_the_rationales_are_trimmed_in_step():
    """Positional lists — a stale rationale would label the wrong query."""
    state = await _run_expander_node([f"استعلام {i}" for i in range(12)], 4)
    out = state.expander_output
    assert len(out.rationales) == len(out.queries) == 4
    assert out.rationales == ["سبب 1", "سبب 2", "سبب 3", "سبب 4"]


@pytest.mark.asyncio
async def test_under_the_ceiling_nothing_is_touched():
    state = await _run_expander_node(["أ", "ب", "ج"], 4)
    assert state.expander_output.queries == ["أ", "ب", "ج"]
    assert len(state.expander_output.rationales) == 3
    assert state.all_queries_used == ["أ", "ب", "ج"]


@pytest.mark.asyncio
async def test_one_query_under_a_cap_of_four_is_left_alone(caplog):
    """D3 — a ceiling, not a target. Nothing pads and nothing warns.

    The expander judging the question settled in a single query is a valid
    answer to "at most 4". There is no floor to fall below, so the clamp has
    nothing to say about it.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="agents.deep_search_v4.shared.query_cap"):
        state = await _run_expander_node(["أ"], 4)
    assert state.expander_output.queries == ["أ"]      # not padded
    assert state.expander_output.rationales == ["سبب 1"]
    assert caplog.records == []


@pytest.mark.asyncio
async def test_an_unpinned_run_keeps_everything_the_expander_emitted():
    """D8 — no cap, no clamp. The expander stays uncapped, as it is in-app."""
    state = await _run_expander_node([f"استعلام {i}" for i in range(12)], None)
    assert len(state.expander_output.queries) == 12
    assert len(state.all_queries_used) == 12


@pytest.mark.asyncio
async def test_the_pinned_cap_reaches_the_user_message_the_agent_saw():
    """The node must pass the cap to the prompt builder, not just clamp."""
    from unittest.mock import MagicMock, patch

    from agents.deep_search_v4.reg_compliance_search.loop import ExpanderNode
    from agents.deep_search_v4.reg_compliance_search.models import (
        LoopState,
        RegComplianceSearchDeps,
    )

    agent = _fake_expander_agent(["أ", "ب"])
    state = LoopState(
        focus_instruction="سؤال", user_context="", expander_query_cap=9
    )
    deps = RegComplianceSearchDeps(supabase=MagicMock(), embedding_fn=MagicMock())
    deps._log_id = ""
    with patch(
        "agents.deep_search_v4.reg_compliance_search.loop.create_expander_agent",
        return_value=agent,
    ):
        await ExpanderNode().run(_Ctx(state, deps))

    sent = agent.run.call_args.args[0]
    assert "## Query count for this run — capped by the editor" in sent
    assert "**at most 9**" in sent


@pytest.mark.asyncio
async def test_an_unpinned_run_sends_a_message_with_no_block():
    from unittest.mock import MagicMock, patch

    from agents.deep_search_v4.reg_compliance_search.loop import ExpanderNode
    from agents.deep_search_v4.reg_compliance_search.models import (
        LoopState,
        RegComplianceSearchDeps,
    )

    agent = _fake_expander_agent(["أ", "ب"])
    state = LoopState(focus_instruction="سؤال", user_context="")
    deps = RegComplianceSearchDeps(supabase=MagicMock(), embedding_fn=MagicMock())
    deps._log_id = ""
    with patch(
        "agents.deep_search_v4.reg_compliance_search.loop.create_expander_agent",
        return_value=agent,
    ):
        await ExpanderNode().run(_Ctx(state, deps))

    sent = agent.run.call_args.args[0]
    assert "capped by the editor" not in sent
    assert sent == build_expander_user_message("سؤال", "")
