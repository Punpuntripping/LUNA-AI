"""The merger agent — slot ``extended_merger`` (the aggregator's policy: V4.1 on Wafer, reasoning max).

A retry regenerates the whole answer (≈ 5 minutes on this model, 2026-11-24 offline
run), so the output validator sends the model back (``ModelRetry``) only for what code
cannot repair:
- a citation outside the shared list (code would have to drop the claim's support);
- an empty answer.
Everything else is repaired by code in the runner, with no second generation: a
scoreboard that misses an angle (filled as ``open`` with a code note) or names an
unknown / repeated one, a ``resolved`` entry naming a report that does not exist,
``[n]`` tags in notes, Arabic-Indic digits in citation tags.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager

from .models import MergerLLMOutput
from .prompts import MERGER_SYSTEM_PROMPT, RETRY_MSG
from .references import cited_numbers, western_citations

MODEL_SLOT = "extended_merger"


def missing_angles(out: MergerLLMOutput, expected: Sequence[str]) -> list[str]:
    have = {(s.angle or "").strip() for s in out.angle_status}
    return [a for a in expected if a not in have]


def create_merger_agent(*, n_shared: int, model: Any = None) -> Agent[None, MergerLLMOutput]:
    agent: Agent[None, MergerLLMOutput] = Agent(
        model if model is not None else get_agent_model(MODEL_SLOT),
        name="extended_merger",
        output_type=[MergerLLMOutput, TextOutput(make_json_salvager(MergerLLMOutput, retry_msg=RETRY_MSG))],
        instructions=MERGER_SYSTEM_PROMPT,
        retries=2,
        output_retries=2,
    )

    @agent.output_validator
    def _validate(ctx: RunContext[None], out: MergerLLMOutput) -> MergerLLMOutput:
        problems: list[str] = []
        bad = sorted({n for n in cited_numbers(western_citations(out.synthesis_md)) if not 1 <= n <= n_shared})
        if bad:
            problems.append(
                f"Citations {bad} are not in <references> (1..{n_shared}). Use only those numbers."
                if n_shared else f"Citations {bad}: there are no references — write no [n] tag."
            )
        if not (out.synthesis_md or "").strip():
            problems.append("`synthesis_md` is empty. Write the merged answer in Arabic.")
        if problems:
            raise ModelRetry(" ".join(problems))
        return out

    return agent


__all__ = ["MODEL_SLOT", "create_merger_agent", "missing_angles"]
