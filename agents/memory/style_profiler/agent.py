"""Pydantic AI agent factory for the style_profiler (Layer-4 Memory, tier_2).

Slot ``style_profiler`` = ``_FLASH`` (deepseek-v4-flash head). Five-field
structured output; deepseek-flash sometimes finalises as plain text
(``<thinking>…</thinking>{json}``), so the shared ``make_json_salvager``
TextOutput coercer recovers it without a retry (same pattern as
``template_ingester``). Shared ``OUTPUT_TOKENS_CAP`` only — no per-agent cap.
"""
from __future__ import annotations

from pydantic_ai import Agent, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .models import StyleProfile
from .prompts import SYSTEM_PROMPT_AR

_RETRY_MSG = (
    "Return the output as ONE valid JSON object with exactly these fields: "
    "register, length_pref, writes_as, terms, notes — no text or <thinking> "
    "tag outside the JSON, and no names, numbers or case facts."
)

STYLE_PROFILER_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,
    request_limit=2,
)


def create_style_profiler() -> Agent[None, StyleProfile]:
    return Agent(
        get_agent_model("style_profiler"),
        name="style_profiler",
        output_type=[
            StyleProfile,
            TextOutput(make_json_salvager(StyleProfile, retry_msg=_RETRY_MSG)),
        ],
        instructions=SYSTEM_PROMPT_AR,
        retries=1,
    )


__all__ = ["create_style_profiler", "STYLE_PROFILER_LIMITS"]
