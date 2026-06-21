"""Reranker for reg_search v2 — single-pass keep-only LLM over chunk results.

Architecture: a classification-only LLM (no tools). Per sub-query,
``run_reranker_for_query`` :

1. Renders each search-result chunk as a labelled markdown block — PRECISE for
   the top rank band, SIMPLE for the rest. The mode is decided by ``search.py``
   and carried on each chunk row as ``_mode``; the reranker does the actual
   unfold + render here so it owns the ``label -> chunk`` map.
2. Asks the LLM, in a SINGLE pass, to emit one entry only for each chunk it
   KEEPS (with a relevance tier). Chunks it does not list are dropped.
3. Code derives the drop set by set-difference (``candidates − keeps``) and
   records each derived drop as a forensic row.
4. Assembles a ``RerankerQueryResult`` from the kept chunks.

Chunks are addressed by a stable label (``C1``, ``C2`` …) assigned once and
never renumbered — the LLM never sees a UUID. Code holds the ``label -> block``
map; the UUID is used only for dedup.

The legacy multi-round ``unfold`` action (neighbour prev/next fetch) was removed
— reg is now single-pass like case/compliance. The chunk context-window *view*
(``unfold_chunk_precise``/``simple``/``format_chunk``) that the renderer uses
stays; only the neighbour-fetch loop is gone.

Replaces the legacy 3-tier (article / section / regulation) reranker. The
search -> reranker contract changed: ``run_reranker_for_query`` now receives
chunk rows (``chunks``), not pre-rendered markdown.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic_ai import Agent, TextOutput
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from agents.utils.structured_output import make_json_salvager
from supabase import Client as SupabaseClient

from .models import (
    RegRerankerClassification,
    RerankedResult,
    RerankerQueryResult,
)
from .prompts import build_reranker_user_message, get_reranker_prompt
from .unfold_reranker import (
    format_chunk,
    unfold_chunk_precise,
    unfold_chunk_simple,
)

logger = logging.getLogger(__name__)

RERANKER_LIMITS = UsageLimits(
    # 25k accommodates: 15k thinking budget (capped via model_settings below) +
    # ~10k for per-chunk decision text on ~15 chunks. Without a cap, one rogue
    # worker in run 1779196337 emitted 9784 reasoning + 11910 text = 21694 tokens.
    # With thinking budget capped, expected total stays around 12-18k.
    # (`response_tokens_limit` was the deprecated alias — switched.)
    output_tokens_limit=25_000,
    request_limit=3,
)


# -- Agent factory -------------------------------------------------------------

# qwen3.5-flash with enable_thinking sometimes finalises as text
# (``<thinking>…</thinking>{json}``) instead of calling the output tool. The
# salvager rescues a schema-complete JSON without a (large) retry; a genuine
# omission still raises ModelRetry. See agents/utils/structured_output.py.
_REG_RERANKER_RETRY_MSG = (
    "Re-emit the output as a single valid JSON object matching the schema "
    "(sufficient, query_axes, keeps[label, relevance, reasoning, "
    "satisfies_axes], summary_note) only — no prose or <thinking> tag "
    "outside the JSON. Emit one entry only for each chunk you KEEP."
)


def _reg_text_output() -> TextOutput:
    """``TextOutput`` salvage member for the reranker's ``output_type`` union."""
    return TextOutput(
        make_json_salvager(RegRerankerClassification, retry_msg=_REG_RERANKER_RETRY_MSG)
    )


def create_reranker_agent(
    prompt_key: str = "prompt_1",
    model_override: str | None = None,
) -> Agent[None, RegRerankerClassification]:
    """Create a classification-only reranker agent. No tools, no deps.

    ``model_override`` is a tier override token (``qwen``/``deepseek``/
    ``alibaba``/``openrouter``) applied to the slot's policy; tier stays fixed.
    """
    system_prompt = get_reranker_prompt(prompt_key)
    model = get_agent_model("reg_search_reranker", model_override)

    return Agent(
        model,
        name="reg_search_reranker",
        output_type=[RegRerankerClassification, _reg_text_output()],
        instructions=system_prompt,
        retries=2,
        # Cap reasoning at 15k — DashScope thinking on qwen3.5-flash is otherwise
        # unbounded and one rogue worker can hit 9k+ reasoning tokens, dominating
        # the concurrent fan-out (RerankerNode wall-clock = slowest worker).
        model_settings={
            "extra_body": {
                "enable_thinking": True,
                "thinking_budget": 15_000,
            },
        },
    )


# -- Block construction --------------------------------------------------------
#
# A "block" is one labelled candidate the reranker reasons over:
#   {
#     "label":    "C7",                 stable handle shown to the LLM
#     "chunk":    {chunks_v2 row},       full row incl. prev/next ids
#     "mode":     "precise" | "simple",
#     "rrf":      float,
#     "unfolded": {dict from unfold_chunk_*},
#     "markdown": str,                   rendered LLM-facing block
#   }


def _make_block(
    supabase: SupabaseClient,
    chunk: dict[str, Any],
    label: str,
    mode: str,
    rrf: float,
) -> dict[str, Any]:
    """Unfold + render one chunk into a labelled reranker block (blocking)."""
    rrf = float(rrf or 0.0)
    if mode == "precise":
        unfolded = unfold_chunk_precise(supabase, chunk)
    else:
        mode = "simple"
        unfolded = unfold_chunk_simple(supabase, chunk)
    unfolded["_score"] = rrf
    return {
        "label": label,
        "chunk": chunk,
        "mode": mode,
        "rrf": rrf,
        "unfolded": unfolded,
        "markdown": format_chunk(unfolded, label),
    }


def _assemble_markdown(blocks: list[dict[str, Any]]) -> str:
    """Concatenate the labelled blocks into one markdown document."""
    parts: list[str] = [f"## نتائج البحث — {len(blocks)} مقطعاً\n"]
    for b in blocks:
        parts.append(b["markdown"])
        parts.append("")
    return "\n".join(parts)


# -- Result assembly -----------------------------------------------------------


def _dropped_row(
    block: dict[str, Any], reasoning: str, drop_reason: str
) -> dict[str, Any]:
    """Forensic descriptor for one dropped chunk (LLM drop or cap truncation).

    ``db_id`` is the bare ``chunks_v2.id`` UUID; the adapter copies it straight
    into ``ref_id`` (reg's citation seed already IS the chunk UUID).
    """
    chunk = block.get("chunk", {})
    unfolded = block.get("unfolded", {})
    return {
        "db_id": chunk.get("id", "") or "",
        "title": unfolded.get("title") or chunk.get("title", "") or "",
        "reasoning": reasoning or "",
        "drop_reason": drop_reason,
        "source_type": "chunk",
    }


def _assemble_result(block: dict[str, Any], decision: dict[str, Any]) -> RerankedResult:
    """Assemble a chunk ``RerankedResult`` from a kept block + its keep dict."""
    unfolded = block["unfolded"]
    chunk = block["chunk"]

    relevance = decision.get("relevance") or "medium"
    if relevance not in ("high", "medium"):
        relevance = "medium"

    return RerankedResult(
        source_type="chunk",
        title=unfolded.get("title") or chunk.get("title", ""),
        # PRECISE has no content body by design; the summary is the richest
        # text the reranker held. The URA stage re-fetches full content by id.
        content=unfolded.get("summary", ""),
        regulation_title=unfolded.get("regulation_name", ""),
        # `article_context` doubles as chunk context (present for precise mode).
        article_context=unfolded.get("context", "") or "",
        relevance=relevance,
        reasoning=decision.get("reasoning", "") or "",
        db_id=chunk.get("id", "") or "",
        rrf=float(block.get("rrf", 0.0) or 0.0),
    )


# -- Main entry point ----------------------------------------------------------


async def run_reranker_for_query(
    query: str,
    rationale: str,
    chunks: list[dict[str, Any]],
    supabase: SupabaseClient,
    *,
    max_keep: int = 8,
    model_override: str | None = None,
    round_trace: list[dict] | None = None,
) -> tuple[RerankerQueryResult, list[dict], list[dict]]:
    """Run a single keep-only classification pass and derive the drop set.

    The LLM emits one entry only for each chunk it keeps; chunks it does not
    list are dropped, and code derives the drop set by set-difference.

    Args:
        query: The sub-query string.
        rationale: Why this query was generated by the expander.
        chunks: Search-result ``chunks_v2`` rows. Each row must carry the
            columns in ``unfold_reranker.CHUNK_SELECT`` plus two routing keys:
            ``_mode`` (``"precise"`` | ``"simple"``, set by search.py from the
            chunk's rank band) and ``_rrf`` (the fused retrieval score).
        supabase: Supabase client for the candidate-view render hops.
        max_keep: Per-sub-query keep cap — a single flat cap (default 8).
        model_override: Optional model registry key.
        round_trace: Optional list; one trace dict appended for the pass.

    Returns:
        (RerankerQueryResult, usage_entries, decision_log)
    """
    agent = create_reranker_agent(model_override=model_override)

    # -- Stable label minting --------------------------------------------------
    label_state = {"n": 0}

    def _next_label() -> str:
        label_state["n"] += 1
        return f"C{label_state['n']}"

    # -- Round-1 blocks (built in parallel) ------------------------------------
    valid_chunks = [c for c in chunks if c.get("id")]
    if not valid_chunks:
        return (
            RerankerQueryResult(
                query=query, rationale=rationale, sufficient=False,
                results=[], dropped_count=0,
                summary_note="لم يتم العثور على نتائج قابلة للتحليل",
            ),
            [],
            [],
        )

    first_labels = [_next_label() for _ in valid_chunks]
    active_blocks: list[dict[str, Any]] = list(
        await asyncio.gather(*[
            asyncio.to_thread(
                _make_block, supabase, ch, lbl,
                ch.get("_mode", "simple"), ch.get("_rrf", 0.0),
            )
            for ch, lbl in zip(valid_chunks, first_labels)
        ])
    )

    all_kept: list[tuple[dict, dict]] = []   # (block, keep_dict) pairs
    all_dropped: list[dict] = []             # forensic: derived-drop + cap-trunc
    total_dropped = 0
    usage_entries: list[dict] = []
    decision_log: list[dict] = []
    final_summary = ""
    last_sufficient = False

    by_label = {b["label"]: b for b in active_blocks}
    trimmed_md = _assemble_markdown(active_blocks)
    user_msg = build_reranker_user_message(query, rationale, trimmed_md)

    logger.info(
        "Reranker: %d candidate blocks, %d chars",
        len(active_blocks), len(trimmed_md),
    )

    # Run the agent with retries. The integrity gate (kept==0 of N>0) may append
    # ONE dynamic note and re-run; that is the only retained retry signal.
    classification = None
    retried_for_zero_keep = False
    extra_note = ""
    last_err: Exception | None = None

    for attempt in range(3):
        run_msg = user_msg + extra_note
        try:
            result = await agent.run(run_msg, usage_limits=RERANKER_LIMITS)
        except Exception as e:
            last_err = e
            logger.warning(
                "Reranker attempt %d/3 failed: %s", attempt + 1, e,
            )
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
            continue

        cand = result.output

        ru = result.usage()
        usage_entries.append({
            "agent": "reranker",
            "reranker_round": 1,
            "requests": ru.requests,
            "input_tokens": ru.input_tokens,
            "output_tokens": ru.output_tokens,
            "total_tokens": ru.total_tokens,
            "details": dict(ru.details) if ru.details else {},
        })

        # Integrity gate: kept==0 of N>0 → ONE retry with a dynamic note. If the
        # model truly keeps nothing, accept the empty list on the second pass.
        n_keeps = len(cand.keeps)
        if n_keeps == 0 and len(by_label) > 0 and not retried_for_zero_keep:
            retried_for_zero_keep = True
            extra_note = (
                f"\n\n---\nNote: you classified 0 of {len(by_label)} candidates "
                f"as keep. If truly none apply, return an empty `keeps` list — "
                f"otherwise reconsider and keep the chunks whose parent system "
                f"scope governs the sub-query."
            )
            logger.warning(
                "Reranker [%s]: kept 0 of %d — retrying once with a reconsider note",
                query[:40], len(by_label),
            )
            continue

        classification = cand
        break

    if classification is None:
        logger.error(
            "Reranker gave up after 3 attempts: %s", last_err
        )
        return (
            RerankerQueryResult(
                query=query, rationale=rationale, sufficient=False,
                results=[], dropped_count=0,
                summary_note=f"تعذّر تصنيف النتائج: {str(last_err)[:100]}",
            ),
            usage_entries,
            decision_log,
        )

    # -- Apply the keeps + integrity gate (invalid/duplicate label) -----------
    kept_labels: set[str] = set()
    for keep in classification.keeps:
        # The LLM often copies the label with its bracket/whitespace ("[C7]",
        # " C7 ") — the markdown header shows "### [C7]". Normalise to the bare
        # "C7" the by_label map is keyed on.
        norm_label = (keep.label or "").strip().strip("[]").strip()
        block = by_label.get(norm_label)
        if block is None:
            logger.warning(
                "Reranker [%s]: keep references unknown label %r — skipped",
                query[:40], keep.label,
            )
            continue
        if norm_label in kept_labels:
            logger.warning(
                "Reranker [%s]: duplicate keep for label %r — keeping first",
                query[:40], norm_label,
            )
            continue
        kept_labels.add(norm_label)

        keep_dict = {
            "relevance": keep.relevance,
            "reasoning": keep.reasoning,
        }
        all_kept.append((block, keep_dict))
        decision_log.append({
            "label": norm_label,
            "rrf": block.get("rrf", 0.0),
            "action": "keep",
            "relevance": keep.relevance,
        })

    # -- Derive the drop set by difference (candidates − keeps) ---------------
    dropped_labels = set(by_label) - kept_labels
    for lbl in dropped_labels:
        block = by_label[lbl]
        total_dropped += 1
        all_dropped.append(_dropped_row(block, "", "llm"))
        decision_log.append({
            "label": lbl,
            "rrf": block.get("rrf", 0.0),
            "action": "drop",
        })

    final_summary = classification.summary_note
    last_sufficient = classification.sufficient

    logger.info(
        "Reranker [%s]: %d kept, %d dropped (derived), sufficient=%s",
        query[:40], len(all_kept), len(dropped_labels), last_sufficient,
    )

    if round_trace is not None:
        round_trace.append({
            "round_num": 1,
            "user_msg": user_msg,
            "classification": classification.model_dump(),
            "unfolds": [],
            "usage": usage_entries[-1] if usage_entries else {},
        })

    # -- Dedup kept blocks by chunk id (keep the higher-rrf copy) --------------
    by_id: dict[str, tuple[dict, dict]] = {}
    for block, dec in all_kept:
        cid = block["chunk"].get("id", "") or ""
        existing = by_id.get(cid)
        if existing is None or block["rrf"] > existing[0]["rrf"]:
            by_id[cid] = (block, dec)
    if len(by_id) < len(all_kept):
        total_dropped += len(all_kept) - len(by_id)
        logger.info(
            "Reranker [%s]: deduped %d repeated chunk ids",
            query[:40], len(all_kept) - len(by_id),
        )
    all_kept = list(by_id.values())

    # -- Apply the per-sub-query keep cap (single flat cap) -------------------
    # One flat cap over all kept chunks. Within the cap, high-relevance chunks
    # are ordered ahead of medium; ties broken by rrf score (descending).
    def _cap_sort_key(p: tuple[dict, dict]) -> tuple[int, float]:
        block, dec = p
        rel = dec.get("relevance") or "medium"
        return (0 if rel == "high" else 1, -block["rrf"])

    all_kept.sort(key=_cap_sort_key)
    truncated = max(0, len(all_kept) - max_keep)
    all_kept_capped = all_kept[:max_keep]
    total_dropped += truncated

    if truncated > 0:
        # Cap-truncated chunks were keep-worthy to the LLM — record them as
        # forensic drops (reason "cap", no per-item reasoning).
        for block, _dec in all_kept[max_keep:]:
            all_dropped.append(_dropped_row(block, "", "cap"))
        logger.info(
            "Reranker [%s]: cap truncated %d results (max_keep=%d)",
            query[:40], truncated, max_keep,
        )

    results = [_assemble_result(block, dec) for block, dec in all_kept_capped]

    return (
        RerankerQueryResult(
            query=query,
            rationale=rationale,
            sufficient=last_sufficient,
            results=results,
            dropped_count=total_dropped,
            summary_note=final_summary,
            unfold_rounds=1,  # vestigial: single-pass now (kept for adapter compat)
            total_unfolds=0,  # vestigial: neighbour-fetch loop removed
            caps_applied={
                "max_keep": max_keep,
                "truncated_by_cap": truncated,
            },
            dropped_results=all_dropped,
        ),
        usage_entries,
        decision_log,
    )
