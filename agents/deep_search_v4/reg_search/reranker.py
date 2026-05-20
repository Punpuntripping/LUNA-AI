"""Reranker for reg_search v2 — classification-only LLM over chunk results.

Architecture: a classification-only LLM (no tools). Per sub-query,
``run_reranker_for_query`` :

1. Renders each search-result chunk as a labelled markdown block — PRECISE for
   the top rank band, SIMPLE for the rest. The mode is decided by ``search.py``
   and carried on each chunk row as ``_mode``; the reranker does the actual
   unfold + render here so it owns the ``label -> chunk`` map.
2. Asks the LLM to classify every chunk: ``keep`` / ``drop`` / ``unfold``.
3. For each ``unfold`` (direction ``prev`` | ``next``), fetches that neighbour,
   renders it SIMPLE, and feeds it into the next round as a fresh candidate.
4. Repeats up to ``MAX_RERANKER_ROUNDS``.
5. Assembles a ``RerankerQueryResult`` from the kept chunks.

Chunks are addressed by a stable label (``C1``, ``C2`` …) assigned once and
never renumbered — the LLM never sees a UUID. Code holds the ``label -> block``
map; the UUID is used only for neighbour fetches and dedup.

Replaces the legacy 3-tier (article / section / regulation) reranker. The
search -> reranker contract changed: ``run_reranker_for_query`` now receives
chunk rows (``chunks``), not pre-rendered markdown.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from agents.utils.agent_models import get_agent_model
from supabase import Client as SupabaseClient

from .models import (
    RegRerankerClassification,
    RerankedResult,
    RerankerQueryResult,
)
from .prompts import build_reranker_user_message, get_reranker_prompt
from .unfold_reranker import (
    fetch_chunk,
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

MAX_RERANKER_ROUNDS = 3

# An unfolded neighbour inherits its parent's rrf, attenuated — it ranks below
# the chunk that pulled it in but still carries ordering signal.
_NEIGHBOUR_RRF_DECAY = 0.5


# -- Agent factory -------------------------------------------------------------


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
        output_type=RegRerankerClassification,
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


def _assemble_result(block: dict[str, Any], decision: dict[str, Any]) -> RerankedResult:
    """Assemble a chunk ``RerankedResult`` from a kept block + its decision."""
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
    """Run up to 3 classification rounds with neighbour unfolding between.

    Args:
        query: The sub-query string.
        rationale: Why this query was generated by the expander.
        chunks: Search-result ``chunks_v2`` rows. Each row must carry the
            columns in ``unfold_reranker.CHUNK_SELECT`` plus two routing keys:
            ``_mode`` (``"precise"`` | ``"simple"``, set by search.py from the
            chunk's rank band) and ``_rrf`` (the fused retrieval score).
        supabase: Supabase client for neighbour fetches.
        max_keep: Per-sub-query keep cap — a single flat cap (default 8).
        model_override: Optional model registry key.
        round_trace: Optional list; one dict appended per classification round.

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

    all_kept: list[tuple[dict, dict]] = []   # (block, decision_dict) pairs
    total_dropped = 0
    total_unfolds = 0
    usage_entries: list[dict] = []
    decision_log: list[dict] = []
    seen_chunk_ids: set[str] = {b["chunk"]["id"] for b in active_blocks}
    final_summary = ""
    last_sufficient = False
    round_num = 0

    for round_num in range(1, MAX_RERANKER_ROUNDS + 1):
        by_label = {b["label"]: b for b in active_blocks}
        trimmed_md = _assemble_markdown(active_blocks)
        user_msg = build_reranker_user_message(
            query, rationale, trimmed_md, round_num,
        )

        logger.info(
            "Reranker round %d: %d active blocks, %d chars",
            round_num, len(active_blocks), len(trimmed_md),
        )

        # Run the agent with retries.
        result = None
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                result = await agent.run(user_msg, usage_limits=RERANKER_LIMITS)
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    "Reranker round %d attempt %d/3 failed: %s",
                    round_num, attempt + 1, e,
                )
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))

        if result is None:
            logger.error(
                "Reranker round %d gave up after 3 attempts: %s", round_num, last_err
            )
            break

        classification = result.output

        ru = result.usage()
        usage_entries.append({
            "agent": "reranker",
            "reranker_round": round_num,
            "requests": ru.requests,
            "input_tokens": ru.input_tokens,
            "output_tokens": ru.output_tokens,
            "total_tokens": ru.total_tokens,
            "details": dict(ru.details) if ru.details else {},
        })

        # Process decisions.
        to_unfold: list[tuple[dict, dict]] = []
        decided_labels: set[str] = set()
        for dec in classification.decisions:
            # The LLM often copies the label with its bracket/whitespace ("[C7]",
            # " C7 ") — the markdown header shows "### [C7]". Normalise to the
            # bare "C7" the by_label map is keyed on.
            norm_label = (dec.label or "").strip().strip("[]").strip()
            block = by_label.get(norm_label)
            if block is None:
                logger.warning(
                    "Reranker: decision references unknown label %r", dec.label
                )
                continue
            decided_labels.add(norm_label)

            dec_dict = {
                "action": dec.action,
                "relevance": dec.relevance,
                "direction": dec.direction,
                "reasoning": dec.reasoning,
            }
            log_entry = {
                "label": norm_label,
                "rrf": block.get("rrf", 0.0),
                "action": dec.action,
            }

            if dec.action == "keep":
                all_kept.append((block, dec_dict))
                log_entry["relevance"] = dec.relevance or "medium"
            elif dec.action == "unfold":
                # The LLM sometimes picks `unfold` but omits/garbles `direction`.
                # Default to "next" (a chapter most often continues forward)
                # rather than losing the unfold.
                if dec.direction not in ("prev", "next"):
                    logger.warning(
                        "Reranker [%s]: unfold on %s with direction %r — "
                        "defaulting to 'next'",
                        query[:40], norm_label, dec.direction,
                    )
                    dec_dict["direction"] = "next"
                to_unfold.append((block, dec_dict))
            else:
                total_dropped += 1

            decision_log.append(log_entry)

        # Completeness check — the LLM sometimes returns fewer decisions than
        # blocks shown (under-emission), silently losing chunks. Auto-drop every
        # block it left unclassified so nothing vanishes and dropped_count stays
        # honest; warn (loudly when most of the round was skipped).
        undecided = [lbl for lbl in by_label if lbl not in decided_labels]
        if undecided:
            for lbl in undecided:
                total_dropped += 1
                decision_log.append({
                    "label": lbl,
                    "rrf": by_label[lbl].get("rrf", 0.0),
                    "action": "drop",
                    "undecided": True,
                })
            lvl = (
                logging.ERROR if len(undecided) * 2 > len(by_label)
                else logging.WARNING
            )
            logger.log(
                lvl,
                "Reranker round %d [%s]: LLM classified %d/%d chunks — "
                "%d left unclassified, auto-dropped: %s",
                round_num, query[:40], len(decided_labels), len(by_label),
                len(undecided), ", ".join(undecided),
            )

        final_summary = classification.summary_note
        last_sufficient = classification.sufficient

        logger.info(
            "Reranker round %d: %d kept, %d unfold, %d dropped, sufficient=%s",
            round_num,
            sum(1 for d in classification.decisions if d.action == "keep"),
            len(to_unfold), total_dropped, classification.sufficient,
        )

        # 80% rule, or nothing to unfold → done.
        if classification.sufficient or not to_unfold:
            if round_trace is not None:
                round_trace.append({
                    "round_num": round_num,
                    "user_msg": user_msg,
                    "classification": classification.model_dump(),
                    "unfolds": [],
                    "usage": usage_entries[-1] if usage_entries else {},
                })
            break

        # Neighbour unfold — fetch each requested prev/next in parallel.
        async def _fetch_neighbour(
            block: dict, dec_dict: dict
        ) -> dict | None:
            chunk = block["chunk"]
            # `direction` is normalised to "prev"/"next" in the decision loop.
            if dec_dict.get("direction") == "prev":
                nid = chunk.get("prev_chunk_id")
            else:
                nid = chunk.get("next_chunk_id")
            if not nid:
                return None  # corpus boundary — no such neighbour
            return await asyncio.to_thread(fetch_chunk, supabase, nid)

        neighbours = await asyncio.gather(*[
            _fetch_neighbour(b, d) for b, d in to_unfold
        ])

        new_blocks: list[dict[str, Any]] = []
        unfold_summary: list[dict] = []
        for (block, dec_dict), neighbour in zip(to_unfold, neighbours):
            direction = dec_dict.get("direction")
            if neighbour is None:
                total_dropped += 1
                unfold_summary.append({
                    "label": block["label"], "direction": direction,
                    "result": "no_neighbour",
                })
                continue
            nid = neighbour.get("id")
            if not nid or nid in seen_chunk_ids:
                unfold_summary.append({
                    "label": block["label"], "direction": direction,
                    "result": "duplicate",
                })
                continue
            seen_chunk_ids.add(nid)
            nb = await asyncio.to_thread(
                _make_block, supabase, neighbour, _next_label(),
                "simple", block["rrf"] * _NEIGHBOUR_RRF_DECAY,
            )
            new_blocks.append(nb)
            total_unfolds += 1
            unfold_summary.append({
                "label": block["label"], "direction": direction,
                "new_label": nb["label"], "result": "ok",
            })

        if round_trace is not None:
            round_trace.append({
                "round_num": round_num,
                "user_msg": user_msg,
                "classification": classification.model_dump(),
                "unfolds": unfold_summary,
                "usage": usage_entries[-1] if usage_entries else {},
            })

        if not new_blocks:
            # Every unfold yielded nothing (all boundaries / duplicates) — stop.
            break

        # Next round classifies only the freshly unfolded neighbours.
        active_blocks = new_blocks

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
            unfold_rounds=min(round_num, MAX_RERANKER_ROUNDS),
            total_unfolds=total_unfolds,
            caps_applied={
                "max_keep": max_keep,
                "truncated_by_cap": truncated,
            },
        ),
        usage_entries,
        decision_log,
    )
