"""Pydantic AI agent factory for the convo_memory_writer.

Single tier_2 DeepSeek-primary slot. deepseek-flash sometimes finalises as plain
JSON instead of calling the output tool, so the shared salvager is wired in, as
on ``template_ingester`` and ``style_profiler``.

The output validator dry-runs the ops and raises ``ModelRetry`` with the exact
problem, so a bad id, an oversized entry or a damaged masked number is corrected
here, before anything is written.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.conversation_memory import check_memory_ops
from agents.utils.structured_output import make_json_salvager
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .deps import MemoryWriterDeps
from .models import MemoryOps
from .prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

#: Set as ``output_retries``, not ``retries``: this agent has no tools, and from
#: pydantic-ai v2 ``retries`` stops reaching the output validator.
MEMORY_WRITER_RETRIES = 2

# One run plus its retries.
MEMORY_WRITER_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,
    request_limit=MEMORY_WRITER_RETRIES + 1,
)

#: One model request, on each provider in the chain. The registry sets no timeout,
#: so a stalled head would otherwise hold the run for 600 seconds and never reach
#: the fallback. A full-length message takes up to about 55 seconds, so this stays
#: above that. It is above the orchestrator's ceiling too: past that a task stops
#: waiting, and the fallback's entry lands for the next turn.
MEMORY_WRITER_REQUEST_TIMEOUT_S = 65.0
#: pydantic-ai's own connect limit. A bare number would raise it to the full 65.
_MEMORY_WRITER_CONNECT_TIMEOUT_S = 5.0

_RETRY_MSG = (
    "Return one JSON object with add, update and remove only, with no text "
    "outside the JSON."
)


def _chain_with_one_attempt_per_provider() -> Any:
    """This agent's model chain, with the OpenAI client's own retries off.

    That client retries a timed-out request twice before it raises. The timeout
    alone would hold a stalled head for three times its length, past
    ``MEMORY_WRITER_TIMEOUT_S``, and the fallback would never run. A 429 or a 5xx
    now moves to the next provider at once, which suits a run a task waits on.

    The slot is a ``ModelPolicy``, so ``get_agent_model`` builds new cells with new
    clients on every call, and no other agent shares what is changed here.
    """
    model = get_agent_model("convo_memory_writer")
    for cell in getattr(model, "models", ()):
        client = getattr(cell, "client", None)
        if client is not None:
            client.max_retries = 0
    return model


#: The model sometimes copies the shape of the prompt's examples as plain text.
#: With nothing in it that means "nothing to save", so it needs no retry.
_EMPTY_OPS_TEXT = re.compile(r"\s*(?:(?:add|update|remove)\s*:\s*\[\s*\]\s*)+")
_salvage_json = make_json_salvager(MemoryOps, retry_msg=_RETRY_MSG)


def _ops_from_text(text: str) -> MemoryOps:
    return MemoryOps() if _EMPTY_OPS_TEXT.fullmatch(text) else _salvage_json(text)


def _problems(deps: MemoryWriterDeps, ops: MemoryOps) -> list[str]:
    return check_memory_ops(
        deps.entries, source=deps.source, allowed=deps.allowed, **ops.model_dump()
    )


def _ops_that_pass(deps: MemoryWriterDeps, output: MemoryOps) -> MemoryOps | None:
    """The ops that are fine on their own, or ``None`` if together they still fail.

    One number the model will not fix must not cost every other entry from the
    message. What is dropped is still in the chat.
    """
    kept = MemoryOps(
        add=[op for op in output.add if not _problems(deps, MemoryOps(add=[op]))],
        update=[op for op in output.update if not _problems(deps, MemoryOps(update=[op]))],
        remove=output.remove,
    )
    if _problems(deps, kept):
        return None
    logger.warning(
        "convo_memory_writer: dropped ops after the last retry: conversation_id=%s count=%d",
        deps.conversation_id,
        len(output.add) + len(output.update) - len(kept.add) - len(kept.update),
    )
    return kept


def create_convo_memory_writer() -> Agent[MemoryWriterDeps, MemoryOps]:
    """Build the agent. One per run, like the other Layer-4 agents.

    ``timeout`` sits at agent level, and a cell's own settings merge under it key
    by key, so the reasoning settings baked on each cell (``extra_body``) stay.
    """
    agent = Agent(
        _chain_with_one_attempt_per_provider(),
        name="convo_memory_writer",
        deps_type=MemoryWriterDeps,
        output_type=[MemoryOps, TextOutput(_ops_from_text)],
        instructions=SYSTEM_PROMPT,
        output_retries=MEMORY_WRITER_RETRIES,
        model_settings={
            "timeout": httpx.Timeout(
                MEMORY_WRITER_REQUEST_TIMEOUT_S, connect=_MEMORY_WRITER_CONNECT_TIMEOUT_S
            )
        },
    )

    @agent.output_validator
    def _check_ops(ctx: RunContext[MemoryWriterDeps], output: MemoryOps) -> MemoryOps:
        problems = _problems(ctx.deps, output)
        if not problems:
            return output
        if ctx.retry >= MEMORY_WRITER_RETRIES:
            kept = _ops_that_pass(ctx.deps, output)
            if kept is not None:
                return kept
        raise ModelRetry("Nothing was saved. Fix this and return again: " + " ".join(problems))

    return agent


__all__ = ["MEMORY_WRITER_LIMITS", "create_convo_memory_writer"]
