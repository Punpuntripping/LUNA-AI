"""Runner for the item_analyzer (Layer-4 Memory) agent.

One ``analyze()`` invocation → 0/1/2 LLM calls → merged ``AnalyzeOutput``.

Flow:
    1. Load ``workspace_items`` rows for the requested ids (RLS-scoped via
       the backend ``service_role`` filter ``.eq("user_id", deps.user_id)``).
    2. Partition the loaded rows into ``REFS_KINDS`` vs ``META_KINDS``.
    3. Fire at most one LLM call per family (the runner makes no parallel
       gather call — keeping it sequential keeps the rate-limit footprint
       small and the spans cleanly nested).
    4. Merge verdicts back into ``targeted_wi`` input order.

Failure contract: a per-family LLM failure degrades that family's WIs to
all-``none`` verdicts (silently logged). The other family still runs. The
caller always gets a valid ``AnalyzeOutput``. Out-of-scope ids are silently
dropped + logged (RLS / service-role filter already gated them out).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Literal, Sequence

from supabase import Client as SupabaseClient

from agents.runs import AgentRunRecord, record_agent_run
from shared.observability import get_logfire

from .agent import (
    ANALYZER_LIMITS,
    create_meta_analyzer,
    create_refs_analyzer,
)
from .deps import AnalyzerDeps
from .models import (
    AnalyzeOutput,
    AnalyzerCall,
    AnalyzerError,
    MetaVerdictFull,
    MetaVerdictNone,
    MetaVerdictPartial,
    RefsVerdictFull,
    RefsVerdictNone,
    RefsVerdictPartial,
    WIVerdict,
    WorkspaceItemRow,
    _MetaAnalyzeOutputLLM,
    _MetaVerdictLLM,
    _RefsAnalyzeOutputLLM,
    _RefsVerdictLLM,
)
from .prompt_registry import render_meta_user_msg, render_refs_user_msg

logger = logging.getLogger(__name__)
_logfire = get_logfire()


# ---------------------------------------------------------------------------
# Kind taxonomy — keep in lock-step with the family literals in models.py.
# ---------------------------------------------------------------------------

REFS_KINDS: set[str] = {"agent_search", "agent_writer"}
META_KINDS: set[str] = {"attachment", "notes"}


# Soft text used on LLM failure (Arabic for parity with the verdict prose).
_LLM_FAIL_RATIONAL = "تعذّر التحليل"


# ===========================================================================
# Public entrypoint
# ===========================================================================


async def analyze(call: AnalyzerCall, deps: AnalyzerDeps) -> AnalyzeOutput:
    """Layer-4 analyze entrypoint.

    Best-effort: silently drops out-of-scope item_ids, returns an empty
    result if everything was dropped. Never raises for caller-recoverable
    conditions; only an unsupported ``kind`` reaching this layer (defense in
    depth) surfaces as ``AnalyzerError``.
    """
    t0 = time.perf_counter()
    log = deps.logger or logger

    with _logfire.span(
        "item_analyzer.analyze",
        caller_id=deps.caller_id,
        targeted_count=len(call.targeted_wi),
        query_chars=len(call.query or ""),
        conversation_id=str(deps.conversation_id) if deps.conversation_id else None,
    ) as span:
        # --- Empty input → no SELECT, no LLM, no cost row --------------------
        if not call.targeted_wi:
            try:
                span.set_attributes({
                    "refs_count": 0,
                    "meta_count": 0,
                    "dropped_count": 0,
                    "verdict_full_count": 0,
                    "verdict_partial_count": 0,
                    "verdict_none_count": 0,
                    "duration_s": round(time.perf_counter() - t0, 3),
                })
            except Exception:
                pass
            return AnalyzeOutput(
                query_echo=call.query,
                items=[],
                overall_rational=None,
            )

        # --- Load workspace_items in scope -----------------------------------
        wis = await _load_workspace_items(
            deps.supabase, call.targeted_wi, user_id=deps.user_id,
        )

        loaded_ids = {w.item_id for w in wis}
        dropped = [iid for iid in call.targeted_wi if iid not in loaded_ids]
        if dropped:
            log.warning(
                "item_analyzer: dropped %d out-of-scope item_id(s): %s",
                len(dropped), dropped,
            )

        # --- Partition -------------------------------------------------------
        refs_wis = [w for w in wis if w.kind in REFS_KINDS]
        meta_wis = [w for w in wis if w.kind in META_KINDS]
        other = [w for w in wis if w.kind not in REFS_KINDS | META_KINDS]
        if other:
            # Defense-in-depth: unsupported kinds shouldn't reach here. The
            # router and writer-planner are responsible for filtering — but if
            # a future caller forgets, we surface it loudly with an Arabic
            # message so the upstream Logfire span carries the cause.
            raise AnalyzerError(
                "أنواع غير مدعومة في الاستدعاء: "
                + ", ".join(sorted({w.kind for w in other}))
            )

        try:
            span.set_attributes({
                "refs_count": len(refs_wis),
                "meta_count": len(meta_wis),
                "dropped_count": len(dropped),
            })
        except Exception:
            pass

        # --- Fan-out (at most 2 LLM calls) -----------------------------------
        verdicts: list[WIVerdict] = []
        overall_chunks: list[str] = []

        if refs_wis:
            v_refs, overall_refs = await _run_refs_family(call, deps, refs_wis)
            verdicts.extend(v_refs)
            if overall_refs:
                overall_chunks.append(overall_refs)

        if meta_wis:
            v_meta, overall_meta = await _run_meta_family(call, deps, meta_wis)
            verdicts.extend(v_meta)
            if overall_meta:
                overall_chunks.append(overall_meta)

        # --- Re-order to input order for caller predictability ---------------
        order = {iid: idx for idx, iid in enumerate(call.targeted_wi)}
        verdicts.sort(key=lambda v: order.get(v.item_id, len(order)))

        # --- Final span attrs ------------------------------------------------
        try:
            span.set_attributes({
                "verdict_full_count": sum(1 for v in verdicts if v.need == "full"),
                "verdict_partial_count": sum(1 for v in verdicts if v.need == "partial"),
                "verdict_none_count": sum(1 for v in verdicts if v.need == "none"),
                "duration_s": round(time.perf_counter() - t0, 3),
            })
        except Exception:
            pass

        return AnalyzeOutput(
            query_echo=call.query,
            items=verdicts,
            overall_rational="\n\n".join(overall_chunks) or None,
        )


# ===========================================================================
# Per-family runners
# ===========================================================================


async def _run_refs_family(
    call: AnalyzerCall,
    deps: AnalyzerDeps,
    wis: Sequence[WorkspaceItemRow],
) -> tuple[list[WIVerdict], str | None]:
    """One LLM call against the refs-family prompt for ``deps.caller_id``.

    The LLM emits ``wi="WI-{seq}"`` aliases (never raw UUIDs). This runner
    builds the alias → item_id map locally, calls the LLM, and resolves
    each emitted ``wi`` back to its ``item_id`` UUID before returning
    public-shaped verdicts. Hallucinated / unknown aliases are dropped
    with a WARNING log — same defensive policy the protocol mandates.

    On any exception, degrades to all-``none`` verdicts for the supplied WIs
    and returns ``overall_rational=None``. Never raises.
    """
    log = deps.logger or logger
    t0 = time.perf_counter()

    alias_to_item = _build_alias_map(wis, log=log, family="refs")

    with _logfire.span(
        "item_analyzer.refs",
        caller_id=deps.caller_id,
        wi_count=len(wis),
        conversation_id=str(deps.conversation_id) if deps.conversation_id else None,
    ) as span:
        try:
            agent = create_refs_analyzer(deps.caller_id)
            user_msg = render_refs_user_msg(
                caller_id=deps.caller_id, query=call.query, wis=wis,
            )
        except Exception as exc:  # noqa: BLE001
            # Programmer error (caller_id not registered, etc.). Degrade to
            # all-`none` so the analyze() contract is preserved, but log
            # loudly — this is a configuration bug, not a runtime LLM hiccup.
            log.warning(
                "item_analyzer.refs: agent build failed (%s) — degrading to all-'none'",
                exc,
            )
            _set_span_attrs(span, {
                "outcome": "build_failed",
                "fallback_used": True,
                "error": str(exc),
                "error.type": type(exc).__name__,
                "duration_s": round(time.perf_counter() - t0, 3),
            })
            return _none_verdicts_for(wis, kind_family="refs", rational=_LLM_FAIL_RATIONAL), None

        try:
            result = await agent.run(user_msg, usage_limits=ANALYZER_LIMITS)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "item_analyzer.refs: LLM call failed (%s) — degrading to all-'none'",
                exc,
            )
            _set_span_attrs(span, {
                "outcome": "llm_failed",
                "fallback_used": True,
                "error": str(exc),
                "error.type": type(exc).__name__,
                "duration_s": round(time.perf_counter() - t0, 3),
            })
            return _none_verdicts_for(wis, kind_family="refs", rational=_LLM_FAIL_RATIONAL), None

        out: _RefsAnalyzeOutputLLM = result.output
        duration_s = time.perf_counter() - t0

        _record_run(deps, "item_analyzer.refs", call, result, wi_count=len(wis))

        # Resolve aliases → UUIDs before handing verdicts to the caller.
        resolved = _resolve_refs_verdicts(out.items, alias_to_item, log)

        usage = _safe_usage(result)
        _set_span_attrs(span, {
            "outcome": "ok",
            "fallback_used": False,
            "model_used": _model_label_from_result(result),
            "tokens_in": usage["input"],
            "tokens_out": usage["output"],
            "tokens_reasoning": usage["reasoning"],
            "duration_s": round(duration_s, 3),
            "verdicts_emitted": len(out.items),
            "verdicts_dropped_unknown_wi": len(out.items) - len(resolved),
        })

        return resolved, out.overall_rational


async def _run_meta_family(
    call: AnalyzerCall,
    deps: AnalyzerDeps,
    wis: Sequence[WorkspaceItemRow],
) -> tuple[list[WIVerdict], str | None]:
    """One LLM call against the meta-family prompt for ``deps.caller_id``.

    Symmetric to ``_run_refs_family``. The LLM emits ``wi="WI-{seq}"`` aliases;
    this runner resolves them back to ``item_id`` UUIDs before returning.
    """
    log = deps.logger or logger
    t0 = time.perf_counter()

    alias_to_item = _build_alias_map(wis, log=log, family="meta")

    with _logfire.span(
        "item_analyzer.meta",
        caller_id=deps.caller_id,
        wi_count=len(wis),
        conversation_id=str(deps.conversation_id) if deps.conversation_id else None,
    ) as span:
        try:
            agent = create_meta_analyzer(deps.caller_id)
            user_msg = render_meta_user_msg(
                caller_id=deps.caller_id, query=call.query, wis=wis,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "item_analyzer.meta: agent build failed (%s) — degrading to all-'none'",
                exc,
            )
            _set_span_attrs(span, {
                "outcome": "build_failed",
                "fallback_used": True,
                "error": str(exc),
                "error.type": type(exc).__name__,
                "duration_s": round(time.perf_counter() - t0, 3),
            })
            return _none_verdicts_for(wis, kind_family="meta", rational=_LLM_FAIL_RATIONAL), None

        try:
            result = await agent.run(user_msg, usage_limits=ANALYZER_LIMITS)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "item_analyzer.meta: LLM call failed (%s) — degrading to all-'none'",
                exc,
            )
            _set_span_attrs(span, {
                "outcome": "llm_failed",
                "fallback_used": True,
                "error": str(exc),
                "error.type": type(exc).__name__,
                "duration_s": round(time.perf_counter() - t0, 3),
            })
            return _none_verdicts_for(wis, kind_family="meta", rational=_LLM_FAIL_RATIONAL), None

        out: _MetaAnalyzeOutputLLM = result.output
        duration_s = time.perf_counter() - t0

        _record_run(deps, "item_analyzer.meta", call, result, wi_count=len(wis))

        resolved = _resolve_meta_verdicts(out.items, alias_to_item, log)

        usage = _safe_usage(result)
        _set_span_attrs(span, {
            "outcome": "ok",
            "fallback_used": False,
            "model_used": _model_label_from_result(result),
            "tokens_in": usage["input"],
            "tokens_out": usage["output"],
            "tokens_reasoning": usage["reasoning"],
            "duration_s": round(duration_s, 3),
            "verdicts_emitted": len(out.items),
            "verdicts_dropped_unknown_wi": len(out.items) - len(resolved),
        })

        return resolved, out.overall_rational


# ===========================================================================
# Alias map + resolution helpers
# ===========================================================================


def _build_alias_map(
    wis: Sequence[WorkspaceItemRow],
    *,
    log: logging.Logger,
    family: str,
) -> dict[str, str]:
    """Build the ``"WI-{seq}" → item_id`` resolution table for one family run.

    Rows whose ``wi_seq`` is ``None`` are excluded with a WARNING — they
    won't appear in the rendered prompt either (the renderers skip them
    symmetrically), so the LLM can't fabricate a verdict for a row it
    never saw.
    """
    alias_to_item: dict[str, str] = {}
    for wi in wis:
        if wi.wi_seq is None:
            log.warning(
                "item_analyzer.%s: dropping wi without wi_seq from alias map "
                "(item_id=%s, kind=%s) — won't be sent to LLM",
                family, wi.item_id, wi.kind,
            )
            continue
        alias_to_item[f"WI-{wi.wi_seq}"] = wi.item_id
    return alias_to_item


def _resolve_refs_verdicts(
    llm_items: Sequence[_RefsVerdictLLM],
    alias_to_item: dict[str, str],
    log: logging.Logger,
) -> list[WIVerdict]:
    """Convert each LLM-emitted refs verdict to its public, UUID-bearing shape.

    Hallucinated aliases (the LLM emits a ``wi`` not in the input set) are
    dropped with a WARNING — the public verdict list comes back shorter
    than ``llm_items`` when this happens. The merged-order step in
    ``analyze()`` then naturally has nothing to sort for that slot.
    """
    out: list[WIVerdict] = []
    for v in llm_items:
        wi_alias = (getattr(v, "wi", "") or "").strip()
        iid = alias_to_item.get(wi_alias)
        if not iid:
            log.warning(
                "item_analyzer.refs: LLM emitted unknown wi=%r — dropping verdict",
                wi_alias,
            )
            continue
        if v.need == "full":
            out.append(RefsVerdictFull(
                need="full",
                item_id=iid,
                kind=v.kind,
                rational=v.rational,
            ))
        elif v.need == "partial":
            out.append(RefsVerdictPartial(
                need="partial",
                item_id=iid,
                kind=v.kind,
                distilled=v.distilled,
                refs_needed=list(v.refs_needed or []),
                rational=v.rational,
            ))
        else:  # "none"
            out.append(RefsVerdictNone(
                need="none",
                item_id=iid,
                kind=v.kind,
                rational=v.rational,
            ))
    return out


def _resolve_meta_verdicts(
    llm_items: Sequence[_MetaVerdictLLM],
    alias_to_item: dict[str, str],
    log: logging.Logger,
) -> list[WIVerdict]:
    """Meta-family mirror of ``_resolve_refs_verdicts``."""
    out: list[WIVerdict] = []
    for v in llm_items:
        wi_alias = (getattr(v, "wi", "") or "").strip()
        iid = alias_to_item.get(wi_alias)
        if not iid:
            log.warning(
                "item_analyzer.meta: LLM emitted unknown wi=%r — dropping verdict",
                wi_alias,
            )
            continue
        if v.need == "full":
            out.append(MetaVerdictFull(
                need="full",
                item_id=iid,
                kind=v.kind,
                rational=v.rational,
            ))
        elif v.need == "partial":
            out.append(MetaVerdictPartial(
                need="partial",
                item_id=iid,
                kind=v.kind,
                distilled=v.distilled,
                extracted_metadata=dict(v.extracted_metadata or {}),
                rational=v.rational,
            ))
        else:  # "none"
            out.append(MetaVerdictNone(
                need="none",
                item_id=iid,
                kind=v.kind,
                rational=v.rational,
            ))
    return out


# ===========================================================================
# Workspace-items loader
# ===========================================================================


async def _load_workspace_items(
    supabase: SupabaseClient,
    item_ids: list[str],
    *,
    user_id: str,
) -> list[WorkspaceItemRow]:
    """SELECT analyzer-relevant columns for ``item_ids`` in this user's scope.

    Established pattern in this codebase (mirrors ``orchestrator._load_attached_items``,
    ``base/context.py``, ``memory/summarize.py``): the backend Supabase client
    runs as ``service_role`` and bypasses RLS, so the ``.eq("user_id", user_id)``
    filter is **load-bearing scope enforcement** — not a defense-in-depth
    supplement. ``item_ids`` is matched in batch via ``.in_("item_id", ids)``
    (the same shape used in ``deep_search_v4/ura/enrich.py`` and
    ``deep_search_v4/case_search/unfold_ura.py``).

    Out-of-scope / nonexistent ids are silently dropped — caller diffs the
    returned ``item_id`` set against ``targeted_wi`` to know which were skipped.

    Note: ``async def`` for caller-side symmetry; the underlying ``supabase-py``
    v2 sync client is called synchronously (same convention as every other
    Layer-4 loader in this repo — see ``base/context.py``).
    """
    if not item_ids:
        return []

    try:
        resp = (
            supabase.table("workspace_items")
            .select("item_id, kind, title, content_md, word_count, wi_seq")
            .in_("item_id", item_ids)
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "item_analyzer._load_workspace_items: SELECT failed for %d id(s): %s",
            len(item_ids), exc,
        )
        return []

    rows = getattr(resp, "data", None) or []
    out: list[WorkspaceItemRow] = []
    for row in rows:
        # Defensive: drop rows that lack the columns we depend on rather than
        # surfacing a Pydantic validation error to the caller.
        item_id = row.get("item_id")
        kind = row.get("kind")
        if not item_id or not kind:
            continue
        raw_wi_seq = row.get("wi_seq")
        wi_seq: int | None
        try:
            wi_seq = int(raw_wi_seq) if raw_wi_seq is not None else None
        except (TypeError, ValueError):
            wi_seq = None
        try:
            out.append(WorkspaceItemRow(
                item_id=str(item_id),
                kind=str(kind),
                title=row.get("title"),
                content_md=row.get("content_md") or "",
                word_count=int(row.get("word_count") or 0),
                wi_seq=wi_seq,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "item_analyzer._load_workspace_items: skipping malformed row "
                "item_id=%s: %s",
                item_id, exc,
            )
    return out


# ===========================================================================
# Degraded-output factory
# ===========================================================================


def _none_verdicts_for(
    wis: Sequence[WorkspaceItemRow],
    *,
    kind_family: Literal["refs", "meta"],
    rational: str,
) -> list[WIVerdict]:
    """Build all-``none`` verdicts for ``wis`` when their family's LLM failed.

    Picks the right concrete ``VerdictNone`` class per family so the resulting
    objects round-trip cleanly through the merged ``WIVerdict`` discriminator.
    Kinds outside the requested family are skipped defensively (shouldn't
    happen — runner already partitioned).
    """
    out: list[WIVerdict] = []
    if kind_family == "refs":
        for wi in wis:
            if wi.kind not in REFS_KINDS:
                continue
            out.append(RefsVerdictNone(
                need="none",
                item_id=wi.item_id,
                kind=wi.kind,  # type: ignore[arg-type]
                rational=rational,
            ))
    else:  # "meta"
        for wi in wis:
            if wi.kind not in META_KINDS:
                continue
            out.append(MetaVerdictNone(
                need="none",
                item_id=wi.item_id,
                kind=wi.kind,  # type: ignore[arg-type]
                rational=rational,
            ))
    return out


# ===========================================================================
# Cost recording
# ===========================================================================


def _record_run(
    deps: AnalyzerDeps,
    subtype: str,
    call: AnalyzerCall,
    result: Any,
    *,
    wi_count: int,
) -> None:
    """Write one tier_2 ``agent_runs`` row per family LLM call.

    Mirrors ``memory/summarize.py::_record_cost`` — same shared
    ``record_agent_run`` writer, same ``per_phase_stats`` shape (one
    ``per_tier`` block under the family name so cost dashboards split
    refs vs meta and per caller).

    Best-effort: any failure is logged and swallowed; the analyzer's
    contract is to return a verdict, not to be a reliable bookkeeper.
    """
    try:
        usage = _safe_usage(result)
        verdict_counts = _count_verdicts(result)
        phase_key = subtype.split(".", 1)[-1]  # "refs" or "meta"

        record_agent_run(
            deps.supabase,
            AgentRunRecord(
                user_id=str(deps.user_id),
                conversation_id=str(deps.conversation_id),
                agent_family="memory",
                subtype=subtype,
                input_summary=(call.query or "")[:300],
                tokens_in=usage["input"],
                tokens_out=usage["output"],
                tokens_reasoning=usage["reasoning"],
                model_used=_model_label_from_result(result),
                per_phase_stats={
                    phase_key: {
                        "caller_id": deps.caller_id,
                        "family": phase_key,
                        "wi_count": wi_count,
                        "verdict_counts": verdict_counts,
                        "per_tier": {
                            "tier_2": {
                                "input": usage["input"],
                                "output": usage["output"],
                                "reasoning": usage["reasoning"],
                            },
                        },
                    },
                },
                status="ok",
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "item_analyzer._record_run failed for %s (non-blocking): %s",
            subtype, exc,
        )


# ===========================================================================
# Small helpers
# ===========================================================================


def _safe_usage(result: Any) -> dict[str, int]:
    """Pull ``input/output/reasoning`` tokens from an AgentRunResult.

    Never raises; missing fields fall back to 0. ``reasoning_tokens`` lives
    in ``usage.details`` per pydantic_ai's convention (the artifact_summarizer
    runner uses the same extraction).
    """
    try:
        usage = result.usage()
        details = dict(usage.details) if getattr(usage, "details", None) else {}
        return {
            "input": int(getattr(usage, "input_tokens", 0) or 0),
            "output": int(getattr(usage, "output_tokens", 0) or 0),
            "reasoning": int(details.get("reasoning_tokens", 0) or 0),
        }
    except Exception:
        return {"input": 0, "output": 0, "reasoning": 0}


def _count_verdicts(result: Any) -> dict[str, int]:
    """Tally verdict needs in a per-family AnalyzeOutput (best-effort)."""
    counts = {"full": 0, "partial": 0, "none": 0}
    try:
        for item in result.output.items:
            need = getattr(item, "need", None)
            if need in counts:
                counts[need] += 1
    except Exception:
        pass
    return counts


def _model_label_from_result(result: Any) -> str:
    """Best-effort: pull a provenance label from the AgentRunResult.

    Same shape as ``artifact_summarizer/runner.py::_model_label_from_result``.
    FallbackModel doesn't reliably surface the model that actually fielded
    the request, so we fall back to the slot's intent label — accurate
    enough for telemetry since the slot is fixed.
    """
    try:
        for attr in ("_model", "model"):
            model = getattr(result, attr, None)
            if model is None:
                continue
            name = getattr(model, "model_name", None) or getattr(model, "name", None)
            if name:
                return str(name)
    except Exception:
        pass
    return "item_analyzer:tier_2"


def _set_span_attrs(span: Any, attrs: dict[str, Any]) -> None:
    """``span.set_attributes`` wrapper that never lets telemetry break a run."""
    try:
        span.set_attributes(attrs)
    except Exception:
        pass
