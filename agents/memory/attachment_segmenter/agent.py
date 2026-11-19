"""Pydantic AI agent factory for the attachment segmenter.

Tier_2 slot ``attachment_segmenter`` on the ``_FLASH`` policy (Baseten DeepSeek V4
Flash 0731 head) — the policy the experiments ran on (as ``artifact_summarizer``),
in its own slot so it can move independently.

No agent-level ``extra_body`` (same reason as ``artifact_summarizer/agent.py``:
pydantic-ai merges settings shallowly, so an agent ``extra_body`` would drop the
cell's thinking settings). The agent sets ONLY ``max_tokens`` — its own key, so the
cells' ``extra_body`` survives the merge. Without it no output limit is sent and
Baseten cuts every response at 16,384 tokens (thinking included): a dense file then
always comes back cut and is re-sent inside the call (2026-11-18 tests: every one-call
run on a 53–63k expert report).

The limit is 200,000 (owner, 2026-11-19). Baseten serves 0731 with a 1,048,576-token
context (its /models, 2026-11-19), so input + limit always fits; the registry's
163,840 for that cell is out of date. Alibaba's ``deepseek-v4-flash-0731`` accepted
200,000 too; OpenRouter was not tested (its key is dead since 2026-10-23). The largest
answer seen is 35k. The run's total also stays under the shared ``OUTPUT_TOKENS_CAP``
(usage limit below), so in practice one answer can use up to 100k.
"""
from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .deps import SegmenterDeps
from .models import SegmentMap

MODEL_SLOT = "attachment_segmenter"

#: Per-response output limit sent to the model (thinking + map) — owner, 2026-11-19.
MAX_OUTPUT_TOKENS = 200_000

#: One output-validation retry inside a run (``retries=1``) → at most 2 requests.
SEGMENTER_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,
    request_limit=2,
)


def create_attachment_segmenter(instructions: str) -> Agent[SegmenterDeps, SegmentMap]:
    """Build the segmenter for one call. Only the instructions vary (doc_type label
    list, whole document vs one part); model, retries and limits never do."""
    return Agent(
        get_agent_model(MODEL_SLOT),
        name="attachment_segmenter",
        deps_type=SegmenterDeps,
        output_type=SegmentMap,
        instructions=instructions,
        retries=1,
        model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
    )
