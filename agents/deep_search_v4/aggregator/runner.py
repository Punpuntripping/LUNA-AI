"""Runner — orchestrates the full aggregator turn.

handle_aggregator_turn pipeline:

    preprocess → LLM (primary) → postvalidate
              ↓ on hard-gate failure (citation_ok / gap_honesty_ok)
              LLM (self-correction with message_history) → postvalidate
              ↓
           strip <thinking> → build artifact → log → return

Self-correction (2026-06) — when the primary output fails one of the two hard
post-validator gates, the runner re-invokes the SAME agent (same prompt mode,
same model) with the prior message history threaded through, plus a targeted
Arabic instruction listing only the gate-specific violations. The model patches
its own previous output surgically instead of regenerating on a different
prompt/model. Capped at one corrective turn; if the corrected output still
fails, ships primary with ``validation.passed=False`` for observability.

For prompt_3 (Draft-Critique-Rewrite), the primary path runs three LLM calls
sequentially on the primary model. Self-correction is SKIPPED for DCR (the
DCR chain has its own exception fallback at ``_run_primary_path``).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .agent import create_aggregator_agent, create_dcr_agents
from .artifact_builder import build_artifact, render_reference_block
from .correction import build_correction_prompt, failing_gate_names
from .deps import AggregatorDeps
from .models import (
    AggregatorInput,
    AggregatorLLMOutput,
    AggregatorOutput,
    Reference,
    ValidationReport,
)
from .postvalidator import (
    extract_cited_numbers,
    strip_thinking_block,
    validate_llm_output,
)
from .preprocessor import (
    attach_source_views,
    collect_ordered_ura_results,
    preprocess_references,
)
from .prompts import build_aggregator_user_message, get_aggregator_prompt

logger = logging.getLogger(__name__)


def _accrue_agg_usage(deps: Any, result: Any) -> None:
    """Append one aggregator LLM call's usage to ``deps._usage_entries``.
    Best-effort; the aggregator's many call paths each call this so the final
    ledger row sums them all.

    Captures the ACTUALLY-FIRED model from the result (last ModelResponse's
    model_name) — NOT ``deps.primary_model``, which is a vestigial provenance
    label ("qwen3.6-plus") that does not select the model. The real model is the
    ``aggregator`` tier FallbackModel (``AGENT_MODELS['aggregator']`` →
    deepseek-v4-flash, or its fallback). usage_by_model prices off this.
    """
    try:
        u = result.usage()
        model = None
        try:
            for m in (result.all_messages() or []):
                mn = getattr(m, "model_name", None)
                if mn:
                    model = mn
        except Exception:
            pass
        deps._usage_entries.append({
            "agent": "aggregator",
            "model": model,  # actual fired model; None → usage_by_model uses the slot default
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
            "cached_tokens": int(getattr(u, "cache_read_tokens", 0) or 0),
            "details": {"reasoning_tokens": int((getattr(u, "details", None) or {}).get("reasoning_tokens", 0) or 0)},
        })
    except Exception:
        pass


def _emit_aggregator_ledger(deps: Any, model_used: str | None = None) -> None:
    """Emit ``deep_search.aggregator`` ledger rows summing every LLM call this
    turn made (single-shot / correction / DCR draft+critique+rewrite), priced per
    ACTUAL fired model via usage_by_model. ``model_used`` (the vestigial
    provenance label) is intentionally ignored. Runs inside the dispatch capture
    scope; no-op outside it. Never raises."""
    try:
        from agents.utils.usage_sink import record_call
        from agents.utils.agent_models import usage_by_model
        ents = deps._usage_entries or []
        if not ents:
            return
        for model, toks in usage_by_model(ents).items():
            ti = int(toks.get("input", 0) or 0)
            to = int(toks.get("output", 0) or 0)
            if not ti and not to:
                continue
            record_call(
                agent="deep_search.aggregator",
                model=model,
                agent_family="deep_search",
                tokens_in=ti,
                tokens_out=to,
                tokens_reasoning=int(toks.get("reasoning", 0) or 0),
                tokens_cached=int(toks.get("cached", 0) or 0),
            )
    except Exception:
        logger.debug("aggregator ledger emit failed", exc_info=True)


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

    # 1b. Stage-3 source-view resolution (parallel Supabase lookups). Pure
    # additive UX metadata -- failures are swallowed in attach_source_views and
    # the pipeline proceeds with source_view=None for the affected refs.
    if deps.supabase is not None and agg_input.ura is not None:
        ura_results = collect_ordered_ura_results(agg_input.ura)
        try:
            await attach_source_views(deps.supabase, references, ura_results)
            _emit(
                deps,
                {
                    "event": "source_views_attached",
                    "count": sum(1 for r in references if r.source_view is not None),
                },
            )
        except Exception as exc:  # noqa: BLE001
            # Defense in depth -- attach_source_views already catches per-ref
            # failures. This guards against gather/loop-level errors.
            logger.warning("aggregator: attach_source_views failed: %s", exc)

    user_message = build_aggregator_user_message(agg_input, references)

    # 2. Primary path — per-prompt routing. ``primary_result`` is the
    # AgentRunResult from the single-shot path (carries message_history for
    # self-correction); None for DCR / DCR-exception-fallback paths.
    llm_output, model_used, raw_logs, primary_result = await _run_primary_path(
        agg_input, deps, user_message, references
    )

    # 3. Validate primary output.
    primary_final_refs = _compute_final_references(llm_output, references)
    validation = _validate(llm_output, references, agg_input, ref_to_sub_queries,
                           agg_input.prompt_key,
                           final_references=primary_final_refs)

    # 4. Self-correct on hard-gate failure (one bounded turn).
    #
    # Hard gates after 2026-06: ``citation_ok`` (no dangling [N]) AND
    # ``gap_honesty_ok`` (insufficient sub-queries surfaced in gaps[]).
    # ``arabic_only_ok`` and ``structure_ok`` ride along in
    # ``validation.notes`` but never trigger correction.
    #
    # Correction uses the SAME prompt mode + SAME model + the prior
    # message history so the model patches its own output surgically.
    # If the corrected output still fails, we ship it with
    # ``validation.passed=False`` for observability (no infinite loop).
    if not validation.passed and primary_result is not None:
        correction_msg = build_correction_prompt(
            validation, agg_input, llm_output, references,
        )
        if correction_msg is not None:
            _emit(deps, {
                "event": "correction_triggered",
                "failing_gates": failing_gate_names(validation),
                "notes": validation.notes,
            })
            try:
                corrected_output, corrected_raw = await _run_correction(
                    deps,
                    model_name=model_used,
                    prompt_key=agg_input.prompt_key,
                    prior_messages=primary_result.new_messages(),
                    correction_msg=correction_msg,
                )
                # Preserve primary raw output under a distinct prefix so the
                # log diff between primary and corrected output is auditable.
                primary_logs = {f"primary_{k}": v for k, v in raw_logs.items()}
                raw_logs = {
                    **primary_logs,
                    "correction": corrected_raw,
                    "correction_notes": "\n".join(validation.notes),
                }
                llm_output = corrected_output
                corrected_final_refs = _compute_final_references(
                    llm_output, references,
                )
                validation = _validate(
                    llm_output, references, agg_input, ref_to_sub_queries,
                    agg_input.prompt_key,  # KEEP planner-chosen mode
                    final_references=corrected_final_refs,
                )
                _emit(deps, {
                    "event": "correction_done",
                    "passed": validation.passed,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "aggregator: self-correction failed (%s) — shipping primary",
                    exc,
                )
                _emit(deps, {
                    "event": "correction_failed",
                    "error": str(exc),
                })
                # validation stays the failing report; raw_logs unchanged.

    # 5. Strip thinking, assemble output, build artifact.
    clean_synthesis = strip_thinking_block(llm_output.synthesis_md).strip()
    thinking_block = _extract_thinking(llm_output.synthesis_md)

    # ``final_prompt_key`` always reflects the planner's chosen mode now.
    # Pre-2026-06 this collapsed to ``prompt_1`` on validation failure
    # because the fallback always ran on CRAC; self-correction preserves
    # the original mode so the metadata stays accurate.
    final_prompt_key = agg_input.prompt_key

    # H1 — filter references[] to those the LLM actually cited.
    # Aggregator picks (used_refs); runner enforces. Take the union of
    # LLM-declared used_refs and regex-extracted cited numbers so a
    # malformed used_refs[] can't accidentally erase the panel.
    final_references = _compute_final_references(llm_output, references)

    artifact = None
    if deps.build_artifact:
        artifact = build_artifact(
            agg_input=agg_input,
            synthesis_md=clean_synthesis,
            references=final_references,
            confidence=llm_output.confidence,
            disclaimer_ar=deps.disclaimer_ar,
            prompt_key=final_prompt_key,
            model_used=model_used,
        )

    # L4 — programmatic gap enumeration. Append a gap entry for every
    # insufficient sub-query the LLM didn't already flag, so coverage
    # holes can't disappear from the output even when the model is
    # over-confident.
    final_gaps = _enrich_gaps(llm_output.gaps or [], agg_input)

    output = AggregatorOutput(
        synthesis_md=clean_synthesis,
        references=final_references,
        confidence=llm_output.confidence,
        gaps=final_gaps,
        disclaimer_ar=deps.disclaimer_ar,
        prompt_key=final_prompt_key,
        model_used=model_used,
        validation=validation,
        artifact=artifact,
        # Migration 049: pass the preprocessor's mapping out so the publisher
        # can populate workspace_item_references.sub_queries[]. Filter to refs
        # that survived the citation post-filter so the persisted state
        # matches the panel the user actually sees.
        ref_to_sub_queries={
            r.n: list(ref_to_sub_queries.get(r.n, [])) for r in final_references
        },
    )

    duration_s = time.perf_counter() - t0

    # 5. Log if logger attached.
    if deps.logger is not None:
        try:
            deps.logger.write_synthesis(clean_synthesis,
                                         render_reference_block(final_references))
            deps.logger.write_references(final_references)
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
        "ref_count": len(final_references),
        "ref_count_pre_filter": len(references),
        "cited": len(validation.cited_numbers),
    })

    # One deep_search.aggregator ledger row summing every LLM call above.
    _emit_aggregator_ledger(deps, model_used)

    return output


# ---------------------------------------------------------------------------
# Primary path routing — single-shot vs DCR chain
# ---------------------------------------------------------------------------


async def _run_primary_path(
    agg_input: AggregatorInput,
    deps: AggregatorDeps,
    user_message: str,
    references: list[Reference],
) -> tuple[AggregatorLLMOutput, str, dict[str, str], Any]:
    """Dispatch to single-shot or DCR based on prompt_key / enable_dcr.

    Returns ``(llm_output, model_used, raw_logs, primary_result)``.

    ``primary_result`` is the Pydantic AI ``AgentRunResult`` from the
    single-shot run when self-correction is allowed; ``None`` for DCR paths
    (DCR has its own internal exception fallback and self-correction is
    skipped for it), for DCR's exception-fallback path (same reason), and
    on LLM exceptions in the single-shot path (no usable history to thread).
    """
    use_dcr = (
        agg_input.enable_dcr
        or agg_input.prompt_key.startswith("prompt_3")
    )

    if use_dcr:
        try:
            output, model, raw = await _run_dcr_chain(
                agg_input, deps, user_message, references,
            )
            return output, model, raw, None  # DCR skips self-correction
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "aggregator: DCR chain failed (%s) — falling back to single-shot %s",
                exc, deps.fallback_model,
            )
            output, model, raw, _ = await _run_single_shot(
                agg_input, deps, user_message,
                stage_key="dcr_fallback",
                model_name=deps.fallback_model,
                prompt_key="prompt_1",
            )
            # DCR exception-fallback also skips self-correction — already a
            # mode-collapse path, layering correction on top adds complexity
            # without clear benefit.
            return output, model, raw, None

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
) -> tuple[AggregatorLLMOutput, str, dict[str, str], Any]:
    """Run one LLM call with the given model + prompt.

    Returns ``(llm_output, model_name, raw_logs, agent_run_result)``.

    On hard failure (LLM call raises) returns a degraded placeholder and
    ``agent_run_result=None`` — there's no usable message history to thread
    into a correction turn.
    """
    agent = create_aggregator_agent(prompt_key=prompt_key, model_name=model_name)
    _log_prompt(deps, prompt_key, user_message, stage_key)
    try:
        result = await agent.run(user_message)
        _accrue_agg_usage(deps, result)
        raw = _stringify_result(result)
        return result.output, model_name, {stage_key: raw}, result
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
        return placeholder, model_name, {stage_key: f"ERROR: {exc!r}"}, None


async def _run_correction(
    deps: AggregatorDeps,
    *,
    model_name: str,
    prompt_key: str,
    prior_messages: Any,
    correction_msg: str,
) -> tuple[AggregatorLLMOutput, str]:
    """One bounded corrective turn that threads the primary's message history.

    The agent is recreated with the SAME prompt and SAME model as the primary
    call so the planner-chosen mode and model billing are preserved. The
    Pydantic AI ``message_history`` parameter carries the prior user prompt
    and the model's prior assistant turn (with its ``final_result`` tool
    call / text output) — the model then sees its own previous output and
    can patch it surgically.

    The ``correction_msg`` is the per-gate Arabic instruction emitted by
    :func:`aggregator.correction.build_correction_prompt`.

    Note: ``agent.instructions=`` (not ``system_prompt=``) means the system
    instructions are injected fresh by the framework each call and are NOT
    stored in message history — there is no duplication when re-running.
    """
    agent = create_aggregator_agent(prompt_key=prompt_key, model_name=model_name)
    _log_prompt(deps, prompt_key, correction_msg, stage="correction")
    result = await agent.run(correction_msg, message_history=prior_messages)
    _accrue_agg_usage(deps, result)
    raw = _stringify_result(result)
    return result.output, raw


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
    _accrue_agg_usage(deps, draft_result)
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
    _accrue_agg_usage(deps, critique_result)
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
    _accrue_agg_usage(deps, rewrite_result)
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
    final_references: list[Reference] | None = None,
) -> ValidationReport:
    return validate_llm_output(
        llm_output=llm_output,
        references=references,
        agg_input=agg_input,
        ref_to_sub_queries=ref_to_sub_queries,
        prompt_key=prompt_key,
        final_references=final_references,
    )


def _enrich_gaps(
    llm_gaps: list[str],
    agg_input: AggregatorInput,
) -> list[str]:
    """Append a programmatic gap entry per insufficient sub-query.

    The LLM's ``gaps`` field is preserved; we only add entries for
    insufficient sub-queries that the LLM didn't already mention. Match
    is by Arabic substring on the rationale or query text — strict
    enough to avoid false-positives, lenient enough to skip the LLM's
    paraphrases.
    """
    enriched: list[str] = list(llm_gaps)
    sub_queries = agg_input.sub_queries or []
    existing_blob = " || ".join(g for g in enriched if g)

    for sq in sub_queries:
        if getattr(sq, "sufficient", True):
            continue
        rationale = (getattr(sq, "rationale", "") or "").strip()
        sq_text = (getattr(sq, "query", "") or "").strip()
        marker = rationale or sq_text
        if not marker:
            continue
        # Skip if the LLM already surfaced this rationale/query in any gap.
        head = marker[:24]
        if head and head in existing_blob:
            continue
        domain = getattr(sq, "domain", "") or ""
        prefix = f"[{domain}] " if domain else ""
        enriched.append(f"لم تُغطَّ بشكل كافٍ: {prefix}{marker}")
        existing_blob = f"{existing_blob} || {marker}"

    return enriched


def _compute_final_references(
    llm_output: AggregatorLLMOutput,
    references: list[Reference],
) -> list[Reference]:
    """Post-filter references to those the LLM actually cited.

    Mirrors the filter applied later when building the published artifact;
    extracted as a helper so validation can score against the same set
    that ships to the user.
    """
    cited_set: set[int] = set(llm_output.used_refs or []) | set(
        extract_cited_numbers(llm_output.synthesis_md or "")
    )
    if cited_set:
        return [r for r in references if r.n in cited_set]
    # Defensive: nothing detected as cited — keep the full list rather
    # than scoring against an empty panel.
    return references


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
