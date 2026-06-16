"""Pydantic AI agent factory for the template_ingester (Layer-4 Memory).

Single tier_2 DeepSeek-primary slot. One LLM call per ingestion: a raw legal
document in, a :class:`CleanedTemplate` out.

deepseek-flash sometimes FINALISES a structured output as plain text
(``<thinking>…</thinking>{json}``) instead of calling the output tool, which
forces a costly validation retry. ``CleanedTemplate`` carries TWO semantic
fields (``title`` + ``content_md``), so a naive "treat the whole text as the
body" fallback would silently drop the title. Instead we wire the shared
``make_json_salvager`` TextOutput coercer (same pattern as ``agents/writer`` and
the deep_search aggregator) so a plain-text JSON emission is salvaged into the
two-field model without a retry, while a genuinely malformed output still
retries. See ``agents/utils/structured_output.py``.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager

from .models import CleanedTemplate
from .prompts import SYSTEM_PROMPT_AR

logger = logging.getLogger(__name__)


# Retry hint surfaced when the salvager can't recover a valid object — steers
# deepseek-flash back to a clean two-field JSON emission.
_INGEST_RETRY_MSG = (
    "Return the output as a valid JSON object per the schema (title, "
    "content_md) only — with no text or <thinking> tag outside the JSON."
)


# Generous output cap — a cleaned template body can be long for a dense legal
# document; reasoning tokens can also spike. request_limit=2 covers the single
# permitted retry.
INGESTER_LIMITS = UsageLimits(
    output_tokens_limit=32_000,
    request_limit=2,
)


def create_template_ingester() -> Agent[None, CleanedTemplate]:
    """Build the template_ingester agent.

    Resolves the model via ``get_agent_model("template_ingester")`` (tier_2,
    deepseek-primary FallbackModel). The ``output_type`` pairs the structured
    ``CleanedTemplate`` with a ``TextOutput`` salvager so a plain-text JSON
    emission doesn't eat a retry.
    """
    return Agent(
        get_agent_model("template_ingester"),
        name="template_ingester",
        output_type=[
            CleanedTemplate,
            TextOutput(make_json_salvager(CleanedTemplate, retry_msg=_INGEST_RETRY_MSG)),
        ],
        instructions=SYSTEM_PROMPT_AR,
        retries=1,
    )


__all__ = ["create_template_ingester", "INGESTER_LIMITS"]
