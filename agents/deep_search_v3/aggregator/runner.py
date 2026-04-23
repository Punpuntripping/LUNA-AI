"""Runner — orchestrates the full aggregator turn.

handle_aggregator_turn pipeline:

    preprocess → LLM (primary) → postvalidate
              ↓ on failure
              LLM (fallback) → postvalidate
              ↓
           strip <thinking> → build artifact → log → return

For prompt_3 (Draft-Critique-Rewrite), the primary path runs three LLM calls
sequentially on the primary model. If ANY stage fails (validation or exception),
the whole chain falls back to single-shot synthesis on the fallback model —
mid-chain switching produces inconsistent output.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .agent import create_aggregator_agent, create_dcr_agents
from .artifact_builder import build_artifact, render_reference_block
from .deps import AggregatorDeps
from .models import (
    AggregatorInput,
    AggregatorLLMOutput,
    AggregatorOutput,
    Reference,
    ValidationReport,
)
from .postvalidator import strip_thinking_block, validate_llm_output
from .preprocessor import preprocess_references
from .prompts import build_aggregator_user_message, get_aggregator_prompt

logger = logging.getLogger(__name__)


async def handle_aggregator_turn(
    agg_input: AggregatorInput,
    deps: AggregatorDeps,
) -> AggregatorOutput:
    """Run the full aggregator pipeline end to end.

    Args:
        agg_input: Reranker results + original query + domain/session metadata.
        deps: Model selection + logger + artifact toggle.

    Returns:
        AggregatorOutput with synthesis_md, references, validation, and
        (optionally) a frontend artifact.
    """
    t0 = time.perf_counter()

    # 1. Preprocess — assign citation numbers in code (NOT in LLM).
    references, ref_to_sub_queries = preprocess_references(agg_input)
    _emit(deps, {"event": "preprocess_done", "ref_count": len(references)})

    if not references:
        logger.warning(
            "aggregator: no references after preprocessing — returning empty synthesis"
        )
        return _empty_output(agg_input, deps, duration_s=time.perf_counter() - t0)

    user_message = build_aggregator_user_message(agg_input, references)

    # 2. Primary path — per-prompt routing.
    llm_output, model_used, raw_logs = await _run_primary_path(
        agg_input, deps, user_message, references
    )

    # 3. Validate. If primary failed, try fallback.
    validation = _validate(llm_output, references, agg_input, ref_to_sub_queries,
                           agg_input.prompt_key)

    if not validation.passed and model_used == deps.primary_model:
        logger.info(
            "aggregator: primary validation failed (%s) — falling back to %s",
            "; ".join(validation.notes) or "unspecified",
            deps.fallback_model,
        )
        _emit(deps, {"event": "fallback_triggered", "reason": validation.notes})
        # Preserve primary raw output under a distinct key so we can diff primary
        # vs fallback after the fact. Without this, the fallback's raw_logs
        # dict replaces the primary's and we lose all debugging evidence.
        primary_logs = {f"primary_{k}": v for k, v in raw_logs.items()}
        primary_validation_notes = list(validation.notes)
        llm_output, model_used, fallback_raw = await _run_single_shot(
            agg_input, deps, user_message, stage_key="fallback_single",
            model_name=deps.fallback_model,
            prompt_key="prompt_1",  # Fallback always uses CRAC
        )
        raw_logs = {**primary_logs, **fallback_raw}
        if primary_validation_notes:
            raw_logs["primary_validation_notes"] = "\n".join(primary_validation_notes)
        validation = _validate(
            llm_output, references, agg_input, ref_to_sub_queries, "prompt_1"
        )

    # 4. Strip thinking, assemble output, build artifact.
    clean_synthesis = strip_thinking_block(llm_output.synthesis_md).strip()
    thinking_block = _extract_thinking(llm_output.synthesis_md)

    final_prompt_key = (
        "prompt_1" if (not validation.passed or model_used == deps.fallback_model)
        else agg_input.prompt_key
    )

    artifact = None
    if deps.build_artifact:
        artifact = build_artifact(
            agg_input=agg_input,
            synthesis_md=clean_synthesis,
            references=references,
            confidence=llm_output.confidence,
            disclaimer_ar=deps.disclaimer_ar,
            prompt_key=final_prompt_key,
            model_used=model_used,
        )

    output = AggregatorOutput(
        synthesis_md=clean_synthesis,
        references=references,
        confidence=llm_output.confidence,
        gaps=llm_output.gaps,
        disclaimer_ar=deps.disclaimer_ar,
        prompt_key=final_prompt_key,
        model_used=model_used,
        validation=validation,
        artifact=artifact,
    )

    duration_s = time.perf_counter() - t0

    # 5. Log if logger attached.
    if deps.logger is not None:
        try:
            deps.logger.write_synthesis(clean_synthesis,
                                         render_reference_block(references))
            deps.logger.write_references(references)
            deps.logger.write_validation(validation, final_prompt_key, model_used)
            if thinking_block:
                deps.logger.write_thinking(thinking_block)
            for stage, raw_text in raw_logs.items():
                deps.logger.write_llm_raw(stage, raw_text)
            deps.logger.write_run_summary(agg_input, output, duration_s)
        except Exception as exc:  # noqa: BLE001
            logger.warning("aggregator: logger failed: %s", exc)

    _emit(deps, {
        "event": "aggregator_done",
        "duration_s": duration_s,
        "passed": validation.passed,
        "model": model_used,
        "ref_count": len(references),
        "cited": len(validation.cited_numbers),
    })

    return output


# ---------------------------------------------------------------------------
# Primary path routing — single-shot vs DCR chain
# ---------------------------------------------------------------------------


async def _run_primary_path(
    agg_input: AggregatorInput,
    deps: AggregatorDeps,
    user_message: str,
    references: list[Reference],
) -> tuple[AggregatorLLMOutput, str, dict[str, str]]:
    """Dispatch to single-shot or DCR based on prompt_key / enable_dcr."""
    use_dcr = (
        agg_input.enable_dcr
        or agg_input.prompt_key.startswith("prompt_3")
    )

    if use_dcr:
        try:
            return await _run_dcr_chain(agg_input, deps, user_message, references)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aggregator: DCR chain failed (%s) — falling back to single-shot %s",
                exc, deps.fallback_model,
            )
            return await _run_single_shot(
                agg_input, deps, user_message,
                stage_key="dcr_fallback",
                model_name=deps.fallback_model,
                prompt_key="prompt_1",
            )

    return await _run_single_shot(
        agg_input, deps, user_message,
        stage_key="single",
        model_name=deps.primary_model,
        prompt_key=agg_input.prompt_key,
    )


async def _run_single_shot(
    agg_input: AggregatorInput,
    deps: AggregatorDeps,
    user_message: str,
    stage_key: str,
    model_name: str,
    prompt_key: str,
) -> tuple[AggregatorLLMOutput, str, dict[str, str]]:
    """Run one LLM call with the given model + prompt. Always returns a valid
    AggregatorLLMOutput (on hard failure, a degraded placeholder so the pipeline
    can still produce *something*)."""
    agent = create_aggregator_agent(prompt_key=prompt_key, model_name=model_name)
    _log_prompt(deps, prompt_key, user_message, stage_key)
    try:
        result = await agent.run(user_message)
        raw = _stringify_result(result)
        return result.output, model_name, {stage_key: raw}
    except Exception as exc:  # noqa: BLE001
        logger.error("aggregator: %s LLM call failed: %s", stage_key, exc)
        placeholder = AggregatorLLMOutput(
            synthesis_md=(
                "## الخلاصة\n\nتعذّر توليد إجابة — يرجى إعادة المحاولة لاحقاً."
            ),
            used_refs=[],
            gaps=[f"model_failure: {exc.__class__.__name__}"],
            confidence="low",
        )
        return placeholder, model_name, {stage_key: f"ERROR: {exc!r}"}


async def _run_dcr_chain(
    agg_input: AggregatorInput,
    deps: AggregatorDeps,
    user_message: str,
    references: list[Reference],
) -> tuple[AggregatorLLMOutput, str, dict[str, str]]:
    """Draft → Critique → Rewrite on the primary model.

    Any stage raising propagates out (caller catches → single-shot fallback).
    """
    draft_agent, critique_agent, rewrite_agent = create_dcr_agents(
        model_name=deps.primary_model
    )

    raw_logs: dict[str, str] = {}

    _emit(deps, {"event": "dcr_draft_start"})
    _log_prompt(deps, "prompt_3_draft", user_message, "draft")
    draft_result = await draft_agent.run(user_message)
    raw_logs["draft"] = _stringify_result(draft_result)
    draft_output = draft_result.output

    critique_msg = (
        user_message
        + "\n\n<draft>\n"
        + draft_output.synthesis_md
        + "\n</draft>\n"
    )
    _emit(deps, {"event": "dcr_critique_start"})
    _log_prompt(deps, "prompt_3_critique", critique_msg, "critique")
    critique_result = await critique_agent.run(critique_msg)
    raw_logs["critique"] = _stringify_result(critique_result)
    critique_output = critique_result.output

    # Critique output's synthesis_md carries the critique JSON text; we pass it
    # verbatim into the rewrite stage so the model can act on it.
    rewrite_msg = (
        user_message
        + "\n\n<draft>\n" + draft_output.synthesis_md + "\n</draft>"
        + "\n\n<critique>\n" + critique_output.synthesis_md + "\n</critique>\n"
    )
    _emit(deps, {"event": "dcr_rewrite_start"})
    _log_prompt(deps, "prompt_3_rewrite", rewrite_msg, "rewrite")
    rewrite_result = await rewrite_agent.run(rewrite_msg)
    raw_logs["rewrite"] = _stringify_result(rewrite_result)

    return rewrite_result.output, deps.primary_model, raw_logs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate(
    llm_output: AggregatorLLMOutput,
    references: list[Reference],
    agg_input: AggregatorInput,
    ref_to_sub_queries: dict[int, list[int]],
    prompt_key: str,
) -> ValidationReport:
    return validate_llm_output(
        llm_output=llm_output,
        references=references,
        agg_input=agg_input,
        ref_to_sub_queries=ref_to_sub_queries,
        prompt_key=prompt_key,
    )


def _log_prompt(
    deps: AggregatorDeps,
    prompt_key: str,
    user_message: str,
    stage: str,
) -> None:
    """Persist the exact system prompt + user message used for one LLM call.

    Writes to `prompt_{stage}.md` in the run log directory. Safe to call with
    deps.logger=None (no-op). Any I/O failure is logged but never raised —
    a prompt log failure must not kill a synthesis run.
    """
    if deps.logger is None:
        return
    try:
        system_prompt = get_aggregator_prompt(prompt_key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("aggregator: could not resolve prompt '%s' for log: %s", prompt_key, exc)
        return
    try:
        deps.logger.write_prompt(
            prompt_key=prompt_key,
            system_prompt=system_prompt,
            user_message=user_message,
            stage=stage,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("aggregator: write_prompt(%s) failed: %s", stage, exc)


def _extract_thinking(synthesis_md: str) -> str:
    """Pull the first <thinking>...</thinking> block verbatim (for logs)."""
    import re
    m = re.search(r"<thinking>(.*?)</thinking>", synthesis_md,
                  re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _stringify_result(result: Any) -> str:
    """Serialize an AgentRunResult for raw logging."""
    try:
        return str(result.output.model_dump_json(indent=2, by_alias=False))
    except Exception:  # noqa: BLE001
        return str(result)


def _emit(deps: AggregatorDeps, event: dict) -> None:
    deps._events.append(event)
    if deps.emit_sse is not None:
        try:
            deps.emit_sse(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("aggregator: emit_sse failed: %s", exc)


def _empty_output(
    agg_input: AggregatorInput,
    deps: AggregatorDeps,
    duration_s: float,
) -> AggregatorOutput:
    """Return a minimal output when there are zero references to synthesize."""
    return AggregatorOutput(
        synthesis_md=(
            "## الخلاصة\n\n"
            "لا توجد نتائج قانونية كافية للإجابة عن هذا السؤال حالياً. "
            "يرجى إعادة صياغة السؤال أو تزويد مزيد من التفاصيل."
        ),
        references=[],
        confidence="low",
        gaps=["no_references_after_reranker"],
        disclaimer_ar=deps.disclaimer_ar,
        prompt_key=agg_input.prompt_key,
        model_used="none",
        validation=None,
        artifact=None,
    )
