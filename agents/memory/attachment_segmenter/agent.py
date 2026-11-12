"""Pydantic AI agent factory for the attachment segmenter.

Tier_2 slot ``attachment_segmenter`` on the ``_FLASH`` policy (Baseten DeepSeek V4
Flash 0731 head) — the policy the experiments ran on (as ``artifact_summarizer``),
in its own slot so it can move independently.

No agent-level ``model_settings`` (same reason as ``artifact_summarizer/agent.py``:
pydantic-ai merges settings shallowly, so an agent ``extra_body`` would drop the
cell's thinking settings). Output cap = the shared ``OUTPUT_TOKENS_CAP`` — never a
per-agent cap (reasoning tokens count against it).
"""
from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .deps import SegmenterDeps
from .models import SegmentMap

MODEL_SLOT = "attachment_segmenter"

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
    )
