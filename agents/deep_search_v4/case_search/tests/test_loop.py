"""Tests for the case_search graph loop (3-node: Expander → Search → Reranker)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.deep_search_v4.case_search.models import (
    CaseSearchResult,
    ExpanderOutput,
    RerankerQueryResult,
)

_LOOP = "agents.deep_search_v4.case_search.loop"
_RERANKER = "agents.deep_search_v4.case_search.reranker"


# ---------------------------------------------------------------------------
# FunctionModel helpers
# ---------------------------------------------------------------------------


def _make_expander_fn(queries: list[str]):
    """Build a FunctionModel function returning a deterministic ExpanderOutput."""
    output = ExpanderOutput(
        queries=queries,
        rationales=[f"rationale {i+1}" for i in range(len(queries))],
    )

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0] if info.output_tools else None
        if tool:
            return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=output.model_dump())])
        return ModelResponse(parts=[])

    return fn


def _make_reranker_result(
    query: str = "استعلام",
    rationale: str = "",
    sufficient: bool = True,
    kept: int = 2,
    dropped: int = 1,
) -> RerankerQueryResult:
    """Build a RerankerQueryResult for testing."""
    from agents.deep_search_v4.case_search.models import RerankedCaseResult
    results = [
        RerankedCaseResult(
            title=f"حكم {i+1}",
            court=f"محكمة {i+1}",
            case_number=f"1445/3/{i+1}",
            content="محتوى الحكم",
            relevance="high",
            score=0.01,
        )
        for i in range(kept)
    ]
    return RerankerQueryResult(
        query=query,
        rationale=rationale,
        sufficient=sufficient,
        results=results,
        dropped_count=dropped,
        summary_note="ملاحظة",
    )


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------


def _patch_expander(expander_fn):
    """Patch create_expander_agent to use a FunctionModel."""
    return patch(
        f"{_LOOP}.create_expander_agent",
        return_value=Agent(FunctionModel(expander_fn), output_type=ExpanderOutput, retries=0),
    )


def _patch_search(md: str = "# نتائج\n### [1] حكم: محكمة — مدينة (ابتدائي)\nRRF: 0.01\n", count: int = 3):
    """Patch search_cases_pipeline to return mock results."""
    return patch(
        f"{_LOOP}.search_cases_pipeline",
        new=AsyncMock(return_value=(md, count)),
    )


def _patch_embeddings():
    """Patch embed_regulation_queries_alibaba to return zero vectors."""
    async def _embed(queries):
        return [[0.0] * 4096 for _ in queries]
    return patch("agents.utils.embeddings.embed_regulation_queries_alibaba", side_effect=_embed)


def _patch_reranker(reranker_results: list[RerankerQueryResult]):
    """Patch run_reranker_for_query to return predefined results, cycling through list."""
    call_count = [0]

    async def _run_reranker(query, rationale, raw_markdown, **kwargs):
        idx = min(call_count[0], len(reranker_results) - 1)
        call_count[0] += 1
        return reranker_results[idx], [], []

    return patch(f"{_LOOP}.run_reranker_for_query", side_effect=_run_reranker)


def _patch_logger():
    """Return a single context manager that patches all logger save functions.

    Built via ExitStack so callers can list it as ONE item in a parenthesized
    ``with`` (star-unpacking a list of CMs into ``with (...)`` is a syntax trap —
    Python parses it as a single tuple, which is not a context manager).
    """
    from contextlib import ExitStack
    from unittest.mock import patch as mock_patch

    _LOGGER = "agents.deep_search_v4.case_search.logger"
    targets = [
        mock_patch(f"{_LOOP}.save_expander_md"),
        mock_patch(f"{_LOOP}.save_search_query_md"),
        mock_patch(f"{_LOOP}.save_reranker_query_md"),
        mock_patch(f"{_LOOP}.save_run_overview_md"),
        mock_patch(f"{_LOOP}.save_run_json"),
        # create_run_dir / make_log_id are imported INSIDE run_case_search
        # (`from .logger import ...`), so patch them at the source module.
        mock_patch(f"{_LOGGER}.create_run_dir"),
        mock_patch(f"{_LOGGER}.make_log_id", return_value="test_log"),
    ]
    stack = ExitStack()
    for t in targets:
        stack.enter_context(t)
    return stack


# ---------------------------------------------------------------------------
# Test: Single round success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_round_success(case_search_deps):
    """Expander → Search → Reranker → End. Verify CaseSearchResult has
    reranker_results, queries_used, rounds_used=1, domain='cases'."""
    queries = ["استعلام 1", "استعلام 2"]
    reranker_results = [
        _make_reranker_result(query="استعلام 1", sufficient=True, kept=2, dropped=1),
        _make_reranker_result(query="استعلام 2", sufficient=True, kept=1, dropped=2),
    ]

    from agents.deep_search_v4.case_search.loop import run_case_search

    logger_patches = _patch_logger()

    with (
        _patch_expander(_make_expander_fn(queries)),
        _patch_search(),
        _patch_reranker(reranker_results),
        logger_patches,
        patch("agents.utils.embeddings.embed_regulation_queries_alibaba", new=AsyncMock(return_value=[[0.0]*4096, [0.0]*4096])),
    ):
        result = await run_case_search(
            focus_instruction="ابحث عن سوابق الفصل التعسفي",
            user_context="تم فصلي من العمل",
            deps=case_search_deps,
            expander_prompt_key="prompt_1",  # legacy 3-node path these mocks target
        )

    assert isinstance(result, CaseSearchResult)
    assert result.domain == "cases"
    assert result.rounds_used == 1
    assert len(result.queries_used) == 2
    assert len(result.reranker_results) == 2
    assert result.expander_prompt_key == "prompt_1"

    # Verify reranker_results contain the expected data
    assert result.reranker_results[0].query == "استعلام 1"
    assert result.reranker_results[0].sufficient is True
    assert len(result.reranker_results[0].results) == 2


# ---------------------------------------------------------------------------
# Test: Expander fallback on error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expander_error_fallback(case_search_deps):
    """When expander raises, fallback to single query from focus_instruction."""
    from agents.deep_search_v4.case_search.loop import run_case_search

    reranker_result = _make_reranker_result(query="ابحث عن الفصل التعسفي", sufficient=False, kept=0, dropped=0)
    logger_patches = _patch_logger()

    # ExpanderNode catches errors raised by expander.run() (not by
    # create_expander_agent itself) → return an agent whose run() raises so the
    # node's fallback branch (single query from focus_instruction) is exercised.
    failing_agent = MagicMock()
    failing_agent.run = AsyncMock(side_effect=Exception("API error"))

    with (
        patch(f"{_LOOP}.create_expander_agent", return_value=failing_agent),
        _patch_search("", 0),
        _patch_reranker([reranker_result]),
        logger_patches,
        patch("agents.utils.embeddings.embed_regulation_queries_alibaba", new=AsyncMock(return_value=[[0.0]*4096])),
    ):
        result = await run_case_search(
            focus_instruction="ابحث عن الفصل التعسفي",
            user_context="",
            deps=case_search_deps,
            expander_prompt_key="prompt_1",  # legacy 3-node path these mocks target
        )

    assert isinstance(result, CaseSearchResult)
    assert result.rounds_used == 1
    # Fallback: focus_instruction used as query
    assert len(result.queries_used) >= 1


# ---------------------------------------------------------------------------
# Test: CaseSearchResult structure
# ---------------------------------------------------------------------------


def test_case_search_result_no_quality_field():
    """CaseSearchResult has no quality, summary_md, citations fields."""
    result = CaseSearchResult(
        reranker_results=[],
        queries_used=["استعلام"],
        rounds_used=1,
        domain="cases",
        expander_prompt_key="prompt_1",
    )
    assert result.domain == "cases"
    assert result.rounds_used == 1
    assert not hasattr(result, "quality")
    assert not hasattr(result, "summary_md")
    assert not hasattr(result, "citations")


# ---------------------------------------------------------------------------
# Test: Multiple queries concurrent reranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_reranking_multiple_queries(case_search_deps):
    """Verify all sub-queries get a RerankerQueryResult when multiple queries used."""
    queries = ["استعلام 1", "استعلام 2", "استعلام 3"]
    reranker_results = [
        _make_reranker_result(query=q, sufficient=i < 2, kept=i+1, dropped=0)
        for i, q in enumerate(queries)
    ]

    from agents.deep_search_v4.case_search.loop import run_case_search

    logger_patches = _patch_logger()

    with (
        _patch_expander(_make_expander_fn(queries)),
        _patch_search(),
        _patch_reranker(reranker_results),
        logger_patches,
        patch("agents.utils.embeddings.embed_regulation_queries_alibaba", new=AsyncMock(return_value=[[0.0]*4096]*3)),
    ):
        result = await run_case_search(
            focus_instruction="بحث معقد بعدة محاور",
            user_context="",
            deps=case_search_deps,
            expander_prompt_key="prompt_1",  # legacy 3-node path these mocks target
        )

    assert len(result.reranker_results) == 3
    assert len(result.queries_used) == 3


# ---------------------------------------------------------------------------
# Editorial query cap — the clamp through the node
# (.claude/plans/expander_query_cap.md §7.5)
# ---------------------------------------------------------------------------


class _Ctx:
    """Stand-in for ``GraphRunContext`` — the node reads ``.state`` / ``.deps``."""

    def __init__(self, state, deps) -> None:
        self.state = state
        self.deps = deps


def _fake_agent_returning(output):
    """An expander agent whose run() yields exactly this output object."""
    result = MagicMock()
    result.output = output
    result.usage = MagicMock(
        return_value=MagicMock(
            requests=1, input_tokens=1, output_tokens=1, total_tokens=2, details=None,
        )
    )
    result.all_messages_json = MagicMock(return_value=b"[]")
    agent = MagicMock()
    agent.run = AsyncMock(return_value=result)
    return agent


@pytest.mark.asyncio
async def test_twelve_queries_under_a_cap_of_four_keep_four(case_search_deps):
    """§7.5 — 12 emitted, cap 4, 4 kept, and only 4 searches are issued.

    Driven through the whole legacy loop rather than the node alone, because
    the thing that matters downstream is that ``queries_used`` and the search
    fan-out both see the clamped list — a clamp the SearchNode ignored would
    look identical at the node and cost eight real searches.
    """
    from agents.deep_search_v4.case_search.loop import run_case_search

    queries = [f"استعلام {i}" for i in range(12)]
    logger_patches = _patch_logger()

    with (
        _patch_expander(_make_expander_fn(queries)),
        _patch_search(),
        _patch_reranker([_make_reranker_result()]),
        logger_patches,
        patch(
            "agents.utils.embeddings.embed_regulation_queries_alibaba",
            new=AsyncMock(side_effect=lambda qs: [[0.0] * 4096 for _ in qs]),
        ),
    ):
        result = await run_case_search(
            focus_instruction="ابحث عن سوابق الفصل التعسفي",
            user_context="",
            deps=case_search_deps,
            expander_prompt_key="prompt_1",
            expander_query_cap=4,
        )

    assert len(result.queries_used) == 4
    assert result.queries_used == [f"استعلام {i}" for i in range(4)]
    # One reranker result per query actually searched — the dropped eight
    # never reached the pipeline.
    assert len(result.reranker_results) == 4


@pytest.mark.asyncio
async def test_an_unpinned_case_run_keeps_all_twelve(case_search_deps):
    """D8 — without a cap the expander stays uncapped, exactly as in-app."""
    from agents.deep_search_v4.case_search.loop import run_case_search

    queries = [f"استعلام {i}" for i in range(12)]
    logger_patches = _patch_logger()

    with (
        _patch_expander(_make_expander_fn(queries)),
        _patch_search(),
        _patch_reranker([_make_reranker_result()]),
        logger_patches,
        patch(
            "agents.utils.embeddings.embed_regulation_queries_alibaba",
            new=AsyncMock(side_effect=lambda qs: [[0.0] * 4096 for _ in qs]),
        ),
    ):
        result = await run_case_search(
            focus_instruction="ابحث عن سوابق الفصل التعسفي",
            user_context="",
            deps=case_search_deps,
            expander_prompt_key="prompt_1",
        )

    assert len(result.queries_used) == 12


@pytest.mark.asyncio
async def test_the_flat_case_expander_trims_rationales_in_step(case_search_deps):
    """Positional lists — a stale rationale mislabels the surviving query."""
    from agents.deep_search_v4.case_search.loop import ExpanderNode
    from agents.deep_search_v4.case_search.models import LoopState

    out = ExpanderOutput(
        queries=[f"استعلام {i}" for i in range(9)],
        rationales=[f"سبب {i}" for i in range(9)],
    )
    state = LoopState(
        focus_instruction="سؤال", user_context="", expander_query_cap=4
    )
    case_search_deps._log_id = ""
    with patch(f"{_LOOP}.create_expander_agent", return_value=_fake_agent_returning(out)):
        await ExpanderNode().run(_Ctx(state, case_search_deps))

    assert state.expander_output.queries == [f"استعلام {i}" for i in range(4)]
    assert state.expander_output.rationales == [f"سبب {i}" for i in range(4)]
    assert state.all_queries_used == state.expander_output.queries


# -- Sectioned path: the >=2-channel invariant survives the clamp --------------


async def _run_sectioned_expander(typed_queries, cap, deps):
    from agents.deep_search_v4.case_search.loop import SectionedExpanderNode
    from agents.deep_search_v4.case_search.models import ExpanderOutputV2, LoopState

    state = LoopState(
        focus_instruction="سؤال",
        user_context="",
        expander_prompt_key="prompt_3",
        expander_query_cap=cap,
    )
    deps._log_id = ""
    out = ExpanderOutputV2(queries=typed_queries)
    with patch(f"{_LOOP}.create_expander_agent", return_value=_fake_agent_returning(out)):
        await SectionedExpanderNode().run(_Ctx(state, deps))
    return state


def _tq(text: str, channel: str):
    from agents.deep_search_v4.case_search.models import TypedQuery

    return TypedQuery(text=text, channel=channel, rationale="سبب")


@pytest.mark.asyncio
async def test_sectioned_clamp_keeps_at_least_two_channels(case_search_deps):
    """§7.5 — a head-slice that lands in one channel would break the prompt's
    «at least two channels» rule; the clamp swaps the cheapest kept query for
    the first dropped one carrying a new channel.

    ⚠ The rescue needs ≥2 kept slots to mean anything, which is exactly why the
    wire floors ``cap`` at 2 (D5). At a cap of 1 the rule is unreachable and the
    swap would only throw away the model's top-ranked query.
    """
    typed = [
        _tq("p1", "principle"),
        _tq("p2", "principle"),
        _tq("p3", "principle"),
        _tq("p4", "principle"),
        _tq("f1", "facts"),
        _tq("b1", "basis"),
    ]
    state = await _run_sectioned_expander(typed, 4, case_search_deps)

    kept = state.expander_output_v2.queries
    assert len(kept) == 4
    assert len({q.channel for q in kept}) >= 2
    assert [q.text for q in kept[:3]] == ["p1", "p2", "p3"]
    assert kept[-1].text == "f1"
    # all_queries_used mirrors what actually survived.
    assert state.all_queries_used == ["p1", "p2", "p3", "f1"]


@pytest.mark.asyncio
async def test_sectioned_clamp_leaves_a_mixed_head_in_the_models_order(case_search_deps):
    typed = [
        _tq("p1", "principle"),
        _tq("f1", "facts"),
        _tq("b1", "basis"),
        _tq("p2", "principle"),
        _tq("f2", "facts"),
    ]
    state = await _run_sectioned_expander(typed, 4, case_search_deps)
    assert [q.text for q in state.expander_output_v2.queries] == ["p1", "f1", "b1", "p2"]


@pytest.mark.asyncio
async def test_the_wire_minimum_cap_of_two_still_reaches_two_channels(case_search_deps):
    """D5 at the boundary — ``cap=2`` is the smallest the API accepts, and it is
    the smallest at which the ≥2-channel rescue can fire at all."""
    typed = [
        _tq("p1", "principle"),
        _tq("p2", "principle"),
        _tq("f1", "facts"),
    ]
    state = await _run_sectioned_expander(typed, 2, case_search_deps)
    kept = state.expander_output_v2.queries
    assert [q.text for q in kept] == ["p1", "f1"]
    assert len({q.channel for q in kept}) == 2


@pytest.mark.asyncio
async def test_sectioned_unpinned_run_is_untouched(case_search_deps):
    typed = [_tq(f"p{i}", "principle") for i in range(6)]
    state = await _run_sectioned_expander(typed, None, case_search_deps)
    assert len(state.expander_output_v2.queries) == 6
    assert len(state.all_queries_used) == 6


@pytest.mark.asyncio
async def test_sectioned_expander_sees_the_block_in_its_user_message(case_search_deps):
    from agents.deep_search_v4.case_search.loop import SectionedExpanderNode
    from agents.deep_search_v4.case_search.models import ExpanderOutputV2, LoopState

    agent = _fake_agent_returning(ExpanderOutputV2(queries=[_tq("p1", "principle")]))
    state = LoopState(
        focus_instruction="سؤال",
        user_context="",
        expander_prompt_key="prompt_3",
        expander_query_cap=6,
    )
    case_search_deps._log_id = ""
    with patch(f"{_LOOP}.create_expander_agent", return_value=agent):
        await SectionedExpanderNode().run(_Ctx(state, case_search_deps))

    sent = agent.run.call_args.args[0]
    assert "## Query count for this run — capped by the editor" in sent
    assert "**at most 6**" in sent
    # ⚠ LAST in the message — it overrides the prompt's own "Number of queries"
    # guidance, so the model has to read it after that guidance.
    assert sent.rstrip().endswith("only the first 6 are used.")
