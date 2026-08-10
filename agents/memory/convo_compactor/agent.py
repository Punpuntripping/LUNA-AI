"""Pydantic AI agent factory for the convo_compactor.

Single tier_2 DeepSeek-primary slot (``_FLASH_MEDIUM``). Reasoning tokens are
pulled from ``usage.details.reasoning_tokens`` by the runner.

NOTE — no agent-level ``model_settings`` here, deliberately. The
``artifact_summarizer`` sibling sets ``extra_body.enable_thinking`` at the
agent level, but ``enable_thinking`` is a **Qwen** control and is inert on the
DeepSeek head this slot runs on. Reasoning for this agent comes entirely from
the ``reasoning="medium"`` field of the ``_FLASH_MEDIUM`` policy, which
``_reasoning_settings`` (``agents/utils/agent_models.py:115-156``) bakes onto
every cell of the fallback chain in that cell's own dialect —
``reasoning_effort="high"`` + ``thinking.type`` for DeepSeek-on-DashScope,
``enable_thinking`` + ``thinking_budget=8000`` for Qwen, and
``reasoning.effort="medium"`` for the OpenRouter net. An agent-level dict
cannot serve all three, and would merge OVER the per-cell defaults.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, ModelRetry, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from .models import CompactionLLMOutput
from .prompts import SYSTEM_PROMPT_AR

logger = logging.getLogger(__name__)


# Generous output cap — the summary targets 200-500 words but reasoning_tokens
# can spike over a long noisy span. request_limit=2 covers one retry.
COMPACTOR_LIMITS = UsageLimits(
    output_tokens_limit=20_000,
    request_limit=2,
)


# Floor for the text-salvage path. A summary shorter than this is not carrying
# a ~10k-token span of conversation, so it is worth one retry rather than
# being accepted as the replacement for those messages.
_MIN_SUMMARY_CHARS = 150


def _text_as_summary(text: str) -> CompactionLLMOutput:
    """Plain-text → CompactionLLMOutput fallback.

    ``CompactionLLMOutput`` has a single semantic field (``summary_md``), so
    any plain-text emission from the model maps loss-free to the structured
    output — the three prescribed sections live inside the text and survive
    intact. This eliminates the retry round when reasoning-mode models
    occasionally finalise as text instead of calling the output tool.
    """
    text = (text or "").strip()
    if len(text) < _MIN_SUMMARY_CHARS:
        raise ModelRetry(
            "The summary is too short to replace the compacted messages. "
            "Rewrite it in full Arabic Markdown with the three required "
            "sections: نية المستخدم، ما أُنتج من عناصر، خيوط مفتوحة."
        )
    return CompactionLLMOutput(summary_md=text)


def create_convo_compactor() -> Agent[None, CompactionLLMOutput]:
    """Build the convo_compactor agent.

    TextOutput absorbs plain-text emissions as a valid summary, sparing a
    Pydantic AI ModelRetry round. The structured tool path is still the
    preferred route — text only kicks in when the model forgets.
    """
    model = get_agent_model("convo_compactor")
    return Agent(
        model,
        name="convo_compactor",
        output_type=[CompactionLLMOutput, TextOutput(_text_as_summary)],
        instructions=SYSTEM_PROMPT_AR,
        retries=1,
    )
