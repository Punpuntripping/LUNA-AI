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
from .full_text import register_read_full_text
from .models import AnalyzerOutputFullLLM, AnalyzerOutputLLM
from .prompts import SYSTEM_PROMPT, SYSTEM_PROMPT_FULL_TEXT
from .tools import register_open_sources
from .verify import unread_citations, unverified_points, unverified_quotes, unverified_reading_errors

SLOT = "artifact_analyzer"

# request_limit bounds tool ROUNDS, not sources: one open_sources call takes any
# number of labels. Room for open → output, plus the salvage and quote retries.
ANALYZER_LIMITS = UsageLimits(output_tokens_limit=OUTPUT_TOKENS_CAP, request_limit=6)
# read_full_text adds a round, and its check (D10) may send the model back to read.
FULL_TEXT_LIMITS = UsageLimits(output_tokens_limit=OUTPUT_TOKENS_CAP, request_limit=8)

_RETRY_MSG = (
    "Reply through the output schema only (restatement, points, verdict, quotes, "
    "missing, ask_user, rationale), with no text outside it."
)
_RETRY_MSG_FULL_TEXT = (
    "Reply through the output schema only (restatement, points, verdict, quotes, "
    "missing, ask_user, rationale, reading_errors), with no text outside it."
)


def limits_for(full_text: bool) -> UsageLimits:
    return FULL_TEXT_LIMITS if full_text else ANALYZER_LIMITS


def _retry_reasons(deps: AnalyzerDeps, value: AnalyzerOutputLLM) -> list[str]:
    reasons: list[str] = []
    bad = unverified_quotes(deps, value)
    if bad:
        listed = "\n".join(f"- {q.source}: {q.text[:120]}" for q in bad)
        reasons.append(
            "These quotes are not word for word in that report or opened "
            f"source:\n{listed}\nCopy the exact text, open the source first, "
            "or remove the quote and reconsider the verdict."
        )
    points = unverified_points(deps, value)
    if points:
        listed = "\n".join(f"- {p.point[:80]}: «{p.query[:120]}»" for p in points)
        reasons.append(
            "These queries are not word for word in <searched>:\n"
            f"{listed}\nCopy the query exactly from <searched>, or leave `query` empty "
            "when no query asked about that point."
        )
    errors = unverified_reading_errors(deps, value)
    if errors:
        listed = "\n".join(f"- {e.report} / {e.source}: {e.report_text[:80]}" for e in errors)
        reasons.append(
            "These reading errors are not word for word on both sides, or their "
            f"source was not read with read_full_text:\n{listed}\nCopy both texts "
            "exactly, or remove the entry."
        )
    return reasons + _unread_reasons(deps, value)


def _unread_reasons(deps: AnalyzerDeps, value: AnalyzerOutputLLM) -> list[str]:
    """D10. Checked again on the second try: a fixed answer may add such a sentence."""
    unread = unread_citations(deps, value)
    if not unread:
        return []
    listed = "\n".join(f"- {label}: «{text[:80]}»" for text, label in unread)
    return [
        "You rely on report sentences that cite a ruling or a service with a guide "
        f"you have not read in full:\n{listed}\nRead them with read_full_text and "
        "check each sentence against the full text before you answer."
    ]


def create_artifact_analyzer(full_text: bool = False) -> Agent[AnalyzerDeps, Any]:
    output_model = AnalyzerOutputFullLLM if full_text else AnalyzerOutputLLM
    agent: Agent[AnalyzerDeps, Any] = Agent(
        get_agent_model(SLOT),
        name=SLOT,
        deps_type=AnalyzerDeps,
        output_type=[
            output_model,
            TextOutput(make_json_salvager(
                output_model,
                retry_msg=_RETRY_MSG_FULL_TEXT if full_text else _RETRY_MSG,
            )),
        ],
        instructions=SYSTEM_PROMPT_FULL_TEXT if full_text else SYSTEM_PROMPT,
        retries=2,
        output_retries=3,
    )

    @agent.instructions
    def _today() -> str:
        """Which law is in force depends on today's date."""
        return today_line()

    register_open_sources(agent)
    if full_text:
        register_read_full_text(agent)

    @agent.output_validator
    def _check_output(ctx: RunContext[AnalyzerDeps], value: Any) -> Any:
        """One chance to fix quotes and reading errors; after that ``finalize`` drops
        them. A second chance only to read a cited ruling (D10), when the tool is on."""
        if not isinstance(value, AnalyzerOutputLLM) or ctx.retry >= 2:
            return value
        if ctx.retry == 0:
            reasons = _retry_reasons(ctx.deps, value)
        else:
            reasons = _unread_reasons(ctx.deps, value)
        if reasons:
            raise ModelRetry("\n\n".join(reasons))
        return value

    return agent
