"""Base class and strategy helpers for per-query reranker loops.

Each domain reranker runs one or more LLM classification rounds and
then assembles the final kept-result set. The three domains differ in
their *feedback strategy*:

    reg_search   -- ``db_unfold``: after each round, unfold items the LLM
                    tagged "unfold" into child rows via Supabase; feed those
                    children to the next round.

    case_search  -- ``none``: single-pass, no inter-round feedback, no
                    programmatic expansion.

    compliance   -- ``re_expand``: single-pass; when ``sufficient=False``
                    the outer loop (pydantic_graph) re-runs the expander
                    with weak_axes injected.  The reranker itself does not
                    loop — it exposes ``weak_axes`` as the feedback signal.

This module provides:

    RerankerLoopBase        -- Abstract base class with shared bookkeeping.
    SinglePassMixin         -- Zero-overhead mixin for case + compliance
                               (``_feedback`` is a no-op that returns None).
    H8IntraQueryDedup       -- Mixin that deduplicates kept results by a
                               stable ID before cap truncation (fixes H8).

Domain rerankers DON'T have to subclass these classes directly; they can
call the helpers as standalone utilities.  The intent is to centralise:

    * ``rounds_used`` tracking.
    * Per-round usage logging (``inner_usage``).
    * Accumulation of kept-set across rounds with the correct dedup key.
    * Cap truncation (max_high / max_medium), applied once after all rounds.

The concrete looping code (``run_reranker_for_query``) lives in each
domain's ``reranker.py`` and can gradually delegate to these helpers.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# H8 dedup helper
# ---------------------------------------------------------------------------


def dedup_kept_by_id(
    kept_pairs: list[tuple[dict, dict]],
    id_key: str = "id",
) -> tuple[list[tuple[dict, dict]], int]:
    """Deduplicate kept (block, decision) pairs by *id_key*.

    Returns the deduplicated list and the number of items removed.
    Items with an empty / missing ID are kept as-is (no dedup applied).

    Args:
        kept_pairs: List of (block_dict, decision_dict) as accumulated by
            the reranker loop.
        id_key: Block dict key to use for identity (default "id").
    """
    seen: set[str] = set()
    result: list[tuple[dict, dict]] = []
    for block, dec in kept_pairs:
        bid = block.get(id_key, "")
        if bid and bid in seen:
            continue
        if bid:
            seen.add(bid)
        result.append((block, dec))
    removed = len(kept_pairs) - len(result)
    return result, removed


# ---------------------------------------------------------------------------
# Cap truncation helper (shared across all three domains)
# ---------------------------------------------------------------------------


def apply_keep_caps(
    kept_pairs: list[tuple[dict, dict]],
    *,
    max_high: int,
    max_medium: int,
    rrf_key: str = "rrf",
    relevance_key_in_dec: str = "relevance",
) -> tuple[list[tuple[dict, dict]], int]:
    """Sort each relevance tier by RRF score and truncate to cap.

    Returns the trimmed list and the number of items truncated.

    Args:
        kept_pairs: ``[(block, dec_dict), ...]`` as accumulated by the loop.
        max_high: Maximum high-relevance results to keep.
        max_medium: Maximum medium-relevance results to keep.
        rrf_key: Block dict key for RRF score (default ``"rrf"``).
        relevance_key_in_dec: Decision dict key for relevance tier.
    """
    def _rrf(pair: tuple) -> float:
        block, _dec = pair
        return float(block.get(rrf_key, 0.0) or 0.0)

    high = [
        p for p in kept_pairs
        if (p[1].get(relevance_key_in_dec) or "medium") == "high"
    ]
    med = [
        p for p in kept_pairs
        if (p[1].get(relevance_key_in_dec) or "medium") != "high"
    ]
    high.sort(key=_rrf, reverse=True)
    med.sort(key=_rrf, reverse=True)

    truncated = max(0, len(high) - max_high) + max(0, len(med) - max_medium)
    return high[:max_high] + med[:max_medium], truncated


# ---------------------------------------------------------------------------
# Usage accumulator
# ---------------------------------------------------------------------------


def accumulate_usage(
    usage_entries: list[dict],
    result_usage: Any,
    *,
    agent: str = "reranker",
    round_num: int = 1,
    extra: dict | None = None,
) -> dict:
    """Build a usage entry from a pydantic_ai RunResult.usage() and append it.

    Returns the entry dict so callers can attach it to round_trace.
    """
    ru = result_usage
    entry: dict = {
        "agent": agent,
        "reranker_round": round_num,
        "requests": ru.requests,
        "input_tokens": ru.input_tokens,
        "output_tokens": ru.output_tokens,
        "total_tokens": ru.total_tokens,
        "details": dict(ru.details) if ru.details else {},
    }
    if extra:
        entry.update(extra)
    usage_entries.append(entry)
    return entry


# ---------------------------------------------------------------------------
# Minimal base class (optional — domain rerankers may call helpers directly)
# ---------------------------------------------------------------------------


class RerankerLoopBase:
    """Shared bookkeeping for per-query reranker loops.

    Subclasses implement ``_feedback`` to plug in domain-specific
    inter-round behaviour (unfold, re-expand, or none).

    Typical usage in a domain reranker::

        loop = MyDomainRerankerLoop(agent, max_high=8, max_medium=4)
        result, usage, decisions = await loop.run(
            query, rationale, blocks, supabase=..., round_trace=rt,
        )

    Alternatively, call the standalone helpers
    (:func:`dedup_kept_by_id`, :func:`apply_keep_caps`,
    :func:`accumulate_usage`) directly from the domain's
    ``run_reranker_for_query`` function.
    """

    #: Strategy tag — set by subclasses for logging / tracing.
    strategy: str = "base"

    def __init__(
        self,
        *,
        max_high: int,
        max_medium: int,
        max_rounds: int = 1,
    ) -> None:
        self.max_high = max_high
        self.max_medium = max_medium
        self.max_rounds = max_rounds

        # Bookkeeping populated by run()
        self.rounds_used: int = 0
        self.usage_entries: list[dict] = []
        self.decision_log: list[dict] = []

    async def _feedback(
        self,
        kept: list[tuple[dict, dict]],
        to_act: list[tuple[dict, dict]],
        classification: Any,
        round_num: int,
        **kwargs: Any,
    ) -> list[dict] | None:
        """Domain-specific inter-round action.

        Returns a list of new blocks for the next round, or ``None`` / ``[]``
        to stop after this round.

        Args:
            kept: (block, dec_dict) pairs marked "keep" this round.
            to_act: (block, dec_dict) pairs marked "unfold" (reg) or
                carrying weak_axes (compliance).
            classification: The raw pydantic_ai ``RerankerClassification``
                output for this round.
            round_num: Current round number (1-based).
            **kwargs: Domain-specific extras (e.g. ``supabase``).
        """
        return None

    def log_round(
        self,
        round_num: int,
        user_msg: str,
        classification: Any,
        unfolds: list[dict],
        usage: dict,
        round_trace: list[dict] | None,
    ) -> None:
        if round_trace is not None:
            round_trace.append({
                "round_num": round_num,
                "user_msg": user_msg,
                "classification": classification.model_dump() if hasattr(classification, "model_dump") else {},
                "unfolds": unfolds,
                "usage": usage,
            })


class SinglePassMixin:
    """Mixin for rerankers that never loop (case_search, compliance_search).

    ``_feedback`` always returns ``None`` so the loop terminates after
    the first classification round.
    """

    strategy: str = "none"

    async def _feedback(self, *_args: Any, **_kwargs: Any) -> None:
        return None


__all__ = [
    "RerankerLoopBase",
    "SinglePassMixin",
    "accumulate_usage",
    "apply_keep_caps",
    "dedup_kept_by_id",
]
