"""Agent factory. Built per call so a test or override never binds a live model."""
from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager
from agents.utils.today import today_line
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .deps import AnalyzerDeps
from .models import AnalyzerOutputLLM
from .prompts import SYSTEM_PROMPT
from .tools import register_open_sources
from .verify import unverified_quotes

SLOT = "artifact_analyzer"

# request_limit bounds tool ROUNDS, not sources: one open_sources call takes any
# number of labels. Room for open → output, plus the salvage and quote retries.
ANALYZER_LIMITS = UsageLimits(output_tokens_limit=OUTPUT_TOKENS_CAP, request_limit=6)

_RETRY_MSG = (
    "Reply through the output schema only (restatement, verdict, quotes, missing, "
    "ask_user, rationale), with no text outside it."
)


def create_artifact_analyzer() -> Agent[AnalyzerDeps, Any]:
    agent: Agent[AnalyzerDeps, Any] = Agent(
        get_agent_model(SLOT),
        name=SLOT,
        deps_type=AnalyzerDeps,
        output_type=[
            AnalyzerOutputLLM,
            TextOutput(make_json_salvager(AnalyzerOutputLLM, retry_msg=_RETRY_MSG)),
        ],
        instructions=SYSTEM_PROMPT,
        retries=2,
        output_retries=3,
    )

    @agent.instructions
    def _today() -> str:
        """Which law is in force depends on today's date."""
        return today_line()

    register_open_sources(agent)

    @agent.output_validator
    def _check_quotes(ctx: RunContext[AnalyzerDeps], value: Any) -> Any:
        """One chance to fix a quote. After that ``finalize`` drops it."""
        if not isinstance(value, AnalyzerOutputLLM) or ctx.retry >= 1:
            return value
        bad = unverified_quotes(ctx.deps, value)
        if bad:
            listed = "\n".join(f"- {q.source}: {q.text[:120]}" for q in bad)
            raise ModelRetry(
                "These quotes are not word for word in that report or opened "
                f"source:\n{listed}\nCopy the exact text, open the source first, "
                "or remove the quote and reconsider the verdict."
            )
        return value

    return agent
