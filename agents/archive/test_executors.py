"""Tests for deep_search_v3 executor factory and search tools.

Validates:
- create_executor() for each domain produces a configured Agent
- create_executor() with invalid domain raises ValueError
- Each search tool (regulations, cases, compliance) works with mock_results
- SSE events are appended to deps._events during search
- run_executor() returns ExecutorResult
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.deep_search_v3.executors.base import (
    DOMAIN_MODEL_SLOTS,
    DOMAIN_PROMPTS,
    create_executor,
    run_executor,
)
from agents.deep_search_v3.executors.search_pipeline import (
    search_cases_pipeline,
    search_compliance_pipeline,
    search_regulations_pipeline,
)
from agents.deep_search_v3.models import (
    ExecutorDeps,
    ExecutorResult,
)


def _get_full_instructions(agent) -> str:
    """Helper: join agent._instructions list into a single string for assertions."""
    return "\n".join(agent._instructions)


# ---------------------------------------------------------------------------
# create_executor factory
# ---------------------------------------------------------------------------


class TestCreateExecutor:
    @patch("agents.deep_search_v3.executors.base.get_agent_model")
    def test_create_regulations_executor(self, mock_get_model):
        """Regulations executor is created with correct prompt and tool."""
        mock_get_model.return_value = "test"
        agent = create_executor(
            domain="regulations",
            focus_instruction="أحكام الفصل التعسفي",
            user_context="عامل مفصول",
        )
        assert agent is not None
        assert agent.output_type is ExecutorResult
        # Check that the model slot was resolved
        mock_get_model.assert_called_with("deep_search_v3_regulations_executor")

    @patch("agents.deep_search_v3.executors.base.get_agent_model")
    def test_create_cases_executor(self, mock_get_model):
        """Cases executor is created with correct prompt."""
        mock_get_model.return_value = "test"
        agent = create_executor(
            domain="cases",
            focus_instruction="سوابق قضائية الفصل التعسفي",
            user_context="عامل يبحث عن أحكام",
        )
        assert agent is not None
        mock_get_model.assert_called_with("deep_search_v3_cases_executor")

    @patch("agents.deep_search_v3.executors.base.get_agent_model")
    def test_create_compliance_executor(self, mock_get_model):
        """Compliance executor is created with correct prompt."""
        mock_get_model.return_value = "test"
        agent = create_executor(
            domain="compliance",
            focus_instruction="خدمة التسوية الودية",
            user_context="عامل يريد تقديم شكوى",
        )
        assert agent is not None
        mock_get_model.assert_called_with("deep_search_v3_compliance_executor")

    def test_invalid_domain_raises(self):
        """Unknown domain should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown executor domain"):
            create_executor(
                domain="invalid_domain",
                focus_instruction="test",
                user_context="test",
            )

    @patch("agents.deep_search_v3.executors.base.get_agent_model")
    def test_focus_instruction_in_prompt(self, mock_get_model):
        """Focus instruction should be included in the agent's instructions."""
        mock_get_model.return_value = "test"
        agent = create_executor(
            domain="regulations",
            focus_instruction="البحث عن أحكام نظام المرور",
            user_context="سائق ارتكب مخالفة",
        )
        full = _get_full_instructions(agent)
        assert "البحث عن أحكام نظام المرور" in full

    @patch("agents.deep_search_v3.executors.base.get_agent_model")
    def test_user_context_in_prompt(self, mock_get_model):
        """User context should be included in the agent's instructions."""
        mock_get_model.return_value = "test"
        agent = create_executor(
            domain="cases",
            focus_instruction="focus",
            user_context="محامي يبحث عن سوابق لقضية تجارية",
        )
        full = _get_full_instructions(agent)
        assert "محامي يبحث عن سوابق" in full

    @patch("agents.deep_search_v3.executors.base.get_agent_model")
    def test_static_prompt_included(self, mock_get_model):
        """The domain-specific static system prompt should be included."""
        mock_get_model.return_value = "test"
        agent = create_executor(
            domain="regulations",
            focus_instruction="focus",
            user_context="ctx",
        )
        full = _get_full_instructions(agent)
        assert "search_regulations" in full

    def test_domain_model_slots_complete(self):
        """All three domains have model slot entries."""
        assert "regulations" in DOMAIN_MODEL_SLOTS
        assert "cases" in DOMAIN_MODEL_SLOTS
        assert "compliance" in DOMAIN_MODEL_SLOTS

    def test_domain_prompts_complete(self):
        """All three domains have static prompt entries."""
        assert "regulations" in DOMAIN_PROMPTS
        assert "cases" in DOMAIN_PROMPTS
        assert "compliance" in DOMAIN_PROMPTS

    @patch("agents.deep_search_v3.executors.base.get_agent_model")
    def test_regulations_has_unfolding_in_instructions(self, mock_get_model):
        """Regulations executor includes unfolding guidance in instructions."""
        mock_get_model.return_value = "test"
        agent = create_executor(
            domain="regulations",
            focus_instruction="test",
            user_context="ctx",
        )
        full = _get_full_instructions(agent)
        # Unfolding guidance mentions the auto-expansion behavior
        assert "تلقائياً" in full

    @patch("agents.deep_search_v3.executors.base.get_agent_model")
    def test_cases_no_unfolding_in_instructions(self, mock_get_model):
        """Cases executor does NOT include unfolding guidance."""
        mock_get_model.return_value = "test"
        agent = create_executor(
            domain="cases",
            focus_instruction="test",
            user_context="ctx",
        )
        full = _get_full_instructions(agent)
        assert "تلقائياً" not in full


# ---------------------------------------------------------------------------
# Search pipelines with mock_results
# ---------------------------------------------------------------------------


class TestSearchRegulationsPipeline:
    @pytest.mark.asyncio
    async def test_mock_results_returned(self, executor_deps):
        """With mock_results set, pipeline returns mock content directly."""
        result_md, count = await search_regulations_pipeline(
            query="حقوق العامل عند الفصل التعسفي",
            deps=executor_deps,
        )
        assert count == 2  # Mock returns 2
        assert "نتائج" in result_md or "نظام العمل" in result_md

    @pytest.mark.asyncio
    async def test_sse_events_appended(self, executor_deps):
        """SSE events should be appended to deps._events during search."""
        await search_regulations_pipeline(
            query="test",
            deps=executor_deps,
        )
        # At least one status event should be emitted
        assert len(executor_deps._events) >= 1
        assert any(
            e.get("type") == "status" for e in executor_deps._events
        )


class TestSearchCasesPipeline:
    @pytest.mark.asyncio
    async def test_mock_results_returned(self, executor_deps):
        """With mock_results set, pipeline returns mock content directly."""
        result_md, count = await search_cases_pipeline(
            query="سوابق قضائية الفصل التعسفي",
            deps=executor_deps,
        )
        assert count == 2
        assert "المحكمة" in result_md or "حكم" in result_md

    @pytest.mark.asyncio
    async def test_sse_events_appended(self, executor_deps):
        await search_cases_pipeline(
            query="test",
            deps=executor_deps,
        )
        assert len(executor_deps._events) >= 1
        assert any(
            e.get("type") == "status" for e in executor_deps._events
        )


class TestSearchCompliancePipeline:
    @pytest.mark.asyncio
    async def test_mock_results_returned(self, executor_deps):
        """With mock_results set, pipeline returns mock content directly."""
        result_md, count = await search_compliance_pipeline(
            query="خدمة التسوية الودية",
            deps=executor_deps,
        )
        assert count == 2
        assert "خدم" in result_md or "ودّي" in result_md

    @pytest.mark.asyncio
    async def test_sse_events_appended(self, executor_deps):
        await search_compliance_pipeline(
            query="test",
            deps=executor_deps,
        )
        assert len(executor_deps._events) >= 1
        assert any(
            e.get("type") == "status" for e in executor_deps._events
        )


# ---------------------------------------------------------------------------
# Search pipelines without mock_results (mock infrastructure)
# ---------------------------------------------------------------------------


class TestSearchPipelinesRealPath:
    """Test the non-mock code path by removing mock_results and mocking infra."""

    @pytest.mark.asyncio
    async def test_regulations_no_candidates(self, executor_deps):
        """When no mock_results and RPCs return empty, return 'no results' message."""
        executor_deps.mock_results = None

        # Mock the RPC to return empty
        def _rpc_empty(rpc_name, params):
            result = MagicMock()
            result.data = []
            return MagicMock(execute=MagicMock(return_value=result))

        executor_deps.supabase.rpc = MagicMock(side_effect=_rpc_empty)

        result_md, count = await search_regulations_pipeline(
            query="test",
            deps=executor_deps,
        )
        assert count == 0
        assert "لم يتم العثور" in result_md

    @pytest.mark.asyncio
    async def test_cases_no_candidates(self, executor_deps):
        """When RPC returns empty, return 'no results' message for cases."""
        executor_deps.mock_results = None

        def _rpc_empty(rpc_name, params):
            result = MagicMock()
            result.data = []
            return MagicMock(execute=MagicMock(return_value=result))

        executor_deps.supabase.rpc = MagicMock(side_effect=_rpc_empty)

        result_md, count = await search_cases_pipeline(
            query="test",
            deps=executor_deps,
        )
        assert count == 0
        assert "لم يتم العثور" in result_md

    @pytest.mark.asyncio
    async def test_compliance_no_candidates(self, executor_deps):
        """When RPC returns empty, return 'no results' message for compliance."""
        executor_deps.mock_results = None

        def _rpc_empty(rpc_name, params):
            result = MagicMock()
            result.data = []
            return MagicMock(execute=MagicMock(return_value=result))

        executor_deps.supabase.rpc = MagicMock(side_effect=_rpc_empty)

        result_md, count = await search_compliance_pipeline(
            query="test",
            deps=executor_deps,
        )
        assert count == 0
        assert "لم يتم العثور" in result_md
