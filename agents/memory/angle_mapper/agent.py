"""Pydantic AI agent factory for the angle mapper.

Slot ``angle_mapper`` on ``_WAFER_V41_DEFAULT`` — DeepSeek V4.1 Flash on Wafer, the
Baseten V4.1 backup behind it — the model every angle-map run was tested on
(``run_angles.py``, 2026-11-21).

No agent-level ``extra_body`` (pydantic-ai merges settings shallowly, so it would
drop the cell's reasoning settings — see ``attachment_segmenter/agent.py``). The
agent sets ONLY ``max_tokens``: Wafer's output maximum, 65,536 (the registry's
``wafer-deepseek-v4.1-flash-1017`` cell), the limit the tests ran with. The run's
total still stays under the shared ``OUTPUT_TOKENS_CAP`` (usage limit below) —
never a per-agent output cap.

One output-validator retry (``retries=1``): a map that names a ref that is not a
section of the group (or a document that is not in it), an angle that names no
section of the group at all, or a ``legal_question`` that asks more than one
question (a second «؟», or a second question chained with «و» / «،» / «أم» —
angles-v5) is sent back once (:mod:`.validate`). Before the check, a bare ``S7``
becomes ``D1.S7`` when the group has one document (``validate.qualify_refs``) — the
validator returns that qualified map. What is still wrong after the retry is the
runner's: unknown refs dropped, angles without a section dropped and counted (none
left → ``failed_invalid``); a second question is kept and counted. Gaps are never
sent back (retries did not fix them): the runner drops equal-value contradictions
and merges OCR gaps in code. The repair call runs the same agent, so its answer
gets the same retry and the same rules.
"""
from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .deps import AngleMapperDeps
from .models import AngleMap
from .prompts import bad_refs_message, multi_question_message, no_sections_message
from .validate import (
    fill_sections_from_text,
    invalid_refs,
    multi_question_angles,
    qualify_refs,
    sectionless_angles,
)

MODEL_SLOT = "angle_mapper"

#: Per-response output limit sent to the model (thinking + map): Wafer's maximum.
MAX_OUTPUT_TOKENS = 65_536

#: The first answer + one output-validation retry → at most 2 requests per run.
ANGLE_MAPPER_LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,
    request_limit=2,
)


def create_angle_mapper(instructions: str) -> Agent[AngleMapperDeps, AngleMap]:
    """Build the angle mapper for one message group. Only the instructions vary (the
    per-type lines of its documents); model, retries and limits never do."""
    agent: Agent[AngleMapperDeps, AngleMap] = Agent(
        get_agent_model(MODEL_SLOT),
        name="angle_mapper",
        deps_type=AngleMapperDeps,
        output_type=AngleMap,
        instructions=instructions,
        retries=1,
        model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
    )

    @agent.output_validator
    def _check_map(ctx: RunContext[AngleMapperDeps], value: AngleMap) -> AngleMap:
        """One chance to fix unknown refs, angles without a section and a
        `legal_question` that asks several questions; after it the runner drops the
        first two and keeps (and counts) the third."""
        refs = ctx.deps.section_refs
        if refs:
            value = qualify_refs(value, refs)
        if ctx.retry >= 1:
            return value
        parts = []
        if refs:
            if problems := invalid_refs(value, refs):
                parts.append(bad_refs_message(problems, sorted(refs)))
            # An angle whose own text names its sections is not sent back: the
            # runner fills them (fill_sections_from_text), so a retry is not spent.
            if empty := sectionless_angles(fill_sections_from_text(value, refs)[0], refs):
                parts.append(no_sections_message(empty))
        if several := multi_question_angles(value):
            parts.append(multi_question_message(several))
        if parts:
            raise ModelRetry(" ".join(parts))
        return value

    return agent


__all__ = ["ANGLE_MAPPER_LIMITS", "MAX_OUTPUT_TOKENS", "MODEL_SLOT", "create_angle_mapper"]
