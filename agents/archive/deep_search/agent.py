"""Agent assembly for deep_search planner.

Defines the planner_agent instance, Citation model, system prompt, and
dynamic instructions. Tools are registered separately in tools.py and
triggered via __init__.py import.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from agents.models import PlannerResult
from agents.utils.agent_models import get_agent_model

from .deps import SearchDeps
from .prompts import get_prompt, PROMPTS  # noqa: F401 — PROMPTS re-exported for tools.py

logger = logging.getLogger(__name__)


# -- Citation model (used by create_report tool for structured tracking) ------


class Citation(BaseModel):
    """Structured citation for a legal source referenced in a research report."""

    source_type: str = Field(
        description='Type of source: "regulation", "article", "section", "case", or "service"',
    )
    ref: str = Field(
        description="Unique identifier -- chunk_ref or case_ref",
    )
    title: str = Field(
        description="Arabic title of the source",
    )
    content_snippet: str = Field(
        default="",
        description="Relevant excerpt from the source",
    )
    regulation_title: Optional[str] = Field(
        default=None,
        description="Parent regulation name (if source is an article or section)",
    )
    article_num: Optional[str] = Field(
        default=None,
        description="Article number (if applicable)",
    )
    court: Optional[str] = Field(
        default=None,
        description="Court name (if source is a court case)",
    )
    relevance: str = Field(
        default="",
        description="Why this source supports the answer",
    )


# -- Usage limits -------------------------------------------------------------


PLANNER_LIMITS = UsageLimits(
    response_tokens_limit=10_000,
    request_limit=20,
    tool_calls_limit=25,
)


# -- System prompt (loaded from prompts.py) -----------------------------------

SYSTEM_PROMPT = get_prompt()  # default prompt — kept for backward compat


# -- Agent factory ------------------------------------------------------------


def create_planner_agent(prompt_name: str | None = None) -> Agent:
    """Create a planner agent with the given prompt.

    Args:
        prompt_name: Key from PROMPTS dict, or None for default.

    Returns:
        Configured Agent instance with tools registered via _register_tools().
    """
    prompt = get_prompt(prompt_name)
    agent = Agent(
        get_agent_model("deep_search_planner"),
        output_type=PlannerResult,
        deps_type=SearchDeps,
        instructions=prompt,
        model_settings={"max_tokens": 50_000},
        retries=2,
        end_strategy="early",
    )

    # Re-apply dynamic instructions
    @agent.instructions
    def _inject_case_memory(ctx: RunContext[SearchDeps]) -> str:
        if ctx.deps.case_memory:
            return f"""
سياق القضية (من ذاكرة القضية):
{ctx.deps.case_memory}

استخدم هذا السياق لتوجيه تحليل المحاور. إذا تضمّنت القضية أنظمة أو مجالات قانونية محددة، أعطها أولوية في بحثك.
"""
        return ""

    # Register tools from tools.py on this new agent instance
    from .tools import register_tools
    register_tools(agent)

    return agent


# -- Default agent instance (used by orchestrator) ----------------------------


planner_agent = Agent(
    get_agent_model("deep_search_planner"),
    output_type=PlannerResult,
    deps_type=SearchDeps,
    instructions=SYSTEM_PROMPT,
    model_settings={"max_tokens": 50_000},
    retries=2,
    end_strategy="early",
)


# -- Dynamic instructions -----------------------------------------------------


@planner_agent.instructions
def inject_case_memory(ctx: RunContext[SearchDeps]) -> str:
    """Inject case-specific memory context when the search is within a lawyer's case."""
    if ctx.deps.case_memory:
        return f"""
سياق القضية (من ذاكرة القضية):
{ctx.deps.case_memory}

استخدم هذا السياق لتوجيه تحليل المحاور. إذا تضمّنت القضية أنظمة أو مجالات قانونية محددة، أعطها أولوية في بحثك.
"""
    return ""


# -- Backward-compat re-exports ----------------------------------------------
# The orchestrator imports from agents.deep_search.agent directly:
#   from agents.deep_search.agent import handle_deep_search_turn, build_search_deps
# These re-exports keep that import path working after the monolith split.

from .deps import build_search_deps as build_search_deps  # noqa: F401,E402
from .runner import handle_deep_search_turn as handle_deep_search_turn  # noqa: F401,E402
