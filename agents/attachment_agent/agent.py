"""Agent factory. Built per call so a test or an override never binds a live model."""
from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .deps import AttachmentDeps
from .edit_tools import register_edit_tools
from .output import AttachmentOutputLLM
from .prompts import SYSTEM_PROMPT
from .tools import register_tools
from .verify import retry_reasons

SLOT = "attachment_agent"

#: Own limits — never the router's ``ctx.usage``. 12 tool calls (the prompt says
#: so); 10 requests leave room for reads, the quote retry and a salvage retry.
ATTACHMENT_AGENT_LIMITS = UsageLimits(
    request_limit=10,
    tool_calls_limit=12,
    output_tokens_limit=OUTPUT_TOKENS_CAP,
)

_RETRY_MSG = (
    "Reply through the output schema only (request, outcome, points, ask_user, note, "
    "rationale), with no text outside it."
)


def create_attachment_agent() -> Agent[AttachmentDeps, Any]:
    agent: Agent[AttachmentDeps, Any] = Agent(
        get_agent_model(SLOT),
        name=SLOT,
        deps_type=AttachmentDeps,
        output_type=[
            AttachmentOutputLLM,
            TextOutput(make_json_salvager(AttachmentOutputLLM, retry_msg=_RETRY_MSG)),
        ],
        instructions=SYSTEM_PROMPT,
        retries=2,
        output_retries=3,
    )
    register_tools(agent)
    register_edit_tools(agent)

    @agent.output_validator
    def _check_output(ctx: RunContext[AttachmentDeps], value: Any) -> Any:
        """ONE chance to fix the quotes (named); after it ``finalize`` drops what
        still fails. Tracked in deps, not ``ctx.retry``: a salvage retry before it
        must not use the quotes' chance up."""
        if not isinstance(value, AttachmentOutputLLM) or ctx.deps.quote_retry_used:
            return value
        reasons = retry_reasons(ctx.deps, value)
        if reasons:
            ctx.deps.quote_retry_used = True
            raise ModelRetry("\n\n".join(reasons))
        return value

    return agent


__all__ = ["ATTACHMENT_AGENT_LIMITS", "SLOT", "create_attachment_agent"]
