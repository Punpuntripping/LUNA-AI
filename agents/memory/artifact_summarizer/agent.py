"""Pydantic AI agent factory for the artifact_summarizer.

Single tier_2 DeepSeek-primary slot with reasoning enabled via the
``extra_body.enable_thinking`` model setting. Reasoning tokens are pulled
from ``usage.details.reasoning_tokens`` by the runner.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, ModelRetry, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model

from .models import ArtifactSummaryLLMOutput
from .prompts import SYSTEM_PROMPT_AR

logger = logging.getLogger(__name__)


# Generous output cap — the summary itself is short but reasoning_tokens
# can spike for dense legal content. request_limit=2 covers one retry.
SUMMARIZER_LIMITS = UsageLimits(
    output_tokens_limit=20_000,
    request_limit=2,
)


def _text_as_summary(text: str) -> ArtifactSummaryLLMOutput:
    """Plain-text → ArtifactSummaryLLMOutput fallback.

    `ArtifactSummaryLLMOutput` has a single semantic field (``summary_md``), so
    any plain-text emission from the model maps loss-free to the structured
    output. This eliminates the retry round when reasoning-mode models
    occasionally finalise as text instead of calling the output tool.
    """
    text = (text or "").strip()
    if len(text) < 40:
        raise ModelRetry(
            "الملخّص قصير جداً. أعد كتابته بصيغة ماركداون عربية كاملة "
            "تصف ما يغطّيه العنصر وما لا يغطّيه."
        )
    return ArtifactSummaryLLMOutput(summary_md=text)


def create_artifact_summarizer() -> Agent[None, ArtifactSummaryLLMOutput]:
    """Build the artifact_summarizer agent."""
    model = get_agent_model("artifact_summarizer")
    return Agent(
        model,
        name="artifact_summarizer",
        # TextOutput absorbs plain-text emissions as a valid summary, sparing
        # a Pydantic AI ModelRetry round. The structured tool path is still
        # the preferred route — text only kicks in when the model forgets.
        output_type=[ArtifactSummaryLLMOutput, TextOutput(_text_as_summary)],
        instructions=SYSTEM_PROMPT_AR,
        model_settings={
            "extra_body": {
                "enable_thinking": True,
            },
        },
        retries=1,
    )
