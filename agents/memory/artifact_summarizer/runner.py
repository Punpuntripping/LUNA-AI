"""Runner for the artifact_summarizer agent.

Single LLM call. Best-effort: on any exception, returns a fallback output
with ``summary_md = content_md[:500]`` so the orchestrator always has
something to write to ``workspace_items.summary``.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .agent import SUMMARIZER_LIMITS, create_artifact_summarizer
from .deps import ArtifactSummaryDeps
from .models import ArtifactSummaryInput, ArtifactSummaryOutput
from .prompts import build_user_message

logger = logging.getLogger(__name__)


_FALLBACK_LEN = 500


async def handle_artifact_summary_turn(
    input: ArtifactSummaryInput,
    deps: ArtifactSummaryDeps,
) -> ArtifactSummaryOutput:
    """Run one summarize-artifact LLM call.

    Returns an ``ArtifactSummaryOutput`` whose ``summary_md`` is either the
    LLM's output or a truncated copy of ``content_md`` on failure. Never
    raises — the caller treats this as best-effort enrichment.
    """
    t0 = time.perf_counter()

    if not (input.content_md or "").strip():
        # Nothing to summarize. Return an empty fallback, no LLM call.
        return ArtifactSummaryOutput(
            summary_md="",
            fallback_used=True,
        )

    user_message = build_user_message(
        original_query=input.original_query,
        title=input.title,
        kind=input.kind,
        content_md=input.content_md,
    )

    agent = create_artifact_summarizer()

    try:
        result = await agent.run(user_message, usage_limits=SUMMARIZER_LIMITS)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "artifact_summarizer: LLM call failed (%s) — falling back to truncated content_md",
            exc,
        )
        return ArtifactSummaryOutput(
            summary_md=input.content_md[:_FALLBACK_LEN],
            fallback_used=True,
        )

    summary_md = (result.output.summary_md or "").strip()
    if not summary_md:
        logger.warning("artifact_summarizer: empty summary — using fallback")
        return ArtifactSummaryOutput(
            summary_md=input.content_md[:_FALLBACK_LEN],
            fallback_used=True,
        )

    usage = result.usage()
    details = dict(usage.details) if usage.details else {}
    tokens_reasoning = int(details.get("reasoning_tokens", 0) or 0)

    model_used = _model_label_from_result(result)

    output = ArtifactSummaryOutput(
        summary_md=summary_md,
        tokens_in=int(usage.input_tokens or 0),
        tokens_out=int(usage.output_tokens or 0),
        tokens_reasoning=tokens_reasoning,
        model_used=model_used,
        fallback_used=False,
    )

    duration_s = time.perf_counter() - t0

    if deps.logger is not None:
        try:
            deps.logger.write_run(input, output, duration_s)
        except Exception as exc:  # noqa: BLE001
            logger.debug("artifact_summarizer: logger failed: %s", exc)

    return output


def _model_label_from_result(result: Any) -> str:
    """Best-effort: pull a provenance label from the AgentRunResult.

    FallbackModel doesn't reliably surface the model that actually fielded
    the request after the fact, so we fall back to the slot's intent
    label — accurate enough for telemetry since the slot is fixed.
    """
    try:
        # Try the new pydantic_ai attribute first; older versions expose it as
        # `_model`. Either way, the FallbackModel returns its current head.
        for attr in ("_model", "model"):
            model = getattr(result, attr, None)
            if model is None:
                continue
            name = getattr(model, "model_name", None) or getattr(model, "name", None)
            if name:
                return str(name)
    except Exception:  # noqa: BLE001
        pass
    return "artifact_summarizer:tier_2"


# Public alias matching the spec in the planning doc.
run_artifact_summary = handle_artifact_summary_turn
