"""Reciprocal-Rank Fusion for case_search sectioned retrieval.

Takes per-channel candidate lists (principle / facts / basis) and produces a
4-bucket result set downstream consumers can surface to the reranker:

    top_principle   top N of the principle channel only
    top_facts       top N of the facts channel only
    top_basis       top N of the basis channel only
    top_fused       top N of the RRF-fused cross-channel list

RRF formula (standard):
    score(case) = Σ_channel  channel_weight / (k + rank_in_channel)

`k = 60` is the canonical RRF constant (Cormack et al.). Per-channel weights
are 1.0 by default — Wave 5 tuning adjusts them based on measured channel
quality (e.g., lower `basis` weight if short sections dominate).

Design choices:
- Candidate dedup is by `case_id` across channels — a case that ranks in
  all three channels rewards the fused score multiplicatively.
- `channel_ranks` on each FusedCandidate preserves provenance so callers can
  show "appeared in principle #3, facts #7" for debug / XAI.
- Candidates missing from a channel contribute 0 to the fused score (they
  still surface via the per-channel top buckets).
- Row metadata is merged with "first seen wins" — all channels return the
  same case row, so they should match.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ChannelCandidate, FusedCandidate

logger = logging.getLogger(__name__)

# Standard RRF constant — Cormack et al.
DEFAULT_RRF_K = 60

# Per-channel weights. Kept equal at Wave 3 — Wave 5 tuning may lower `basis`
# if short-section noise hurts the fused ranking.
DEFAULT_CHANNEL_WEIGHTS: dict[str, float] = {
    "principle": 1.0,
    "facts": 1.0,
    "basis": 1.0,
}

# How many cases to surface per bucket after fusion.
DEFAULT_TOP_PER_CHANNEL = 10
DEFAULT_TOP_FUSED = 15


def rrf_fuse(
    channel_results: dict[str, list["ChannelCandidate"]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: dict[str, float] | None = None,
) -> list["FusedCandidate"]:
    """Reciprocal Rank Fusion across channel result lists.

    Args:
        channel_results: channel name → ranked ChannelCandidate list. Channels
            absent from the dict are simply skipped.
        k: RRF constant (default 60).
        weights: per-channel multiplier. Defaults to 1.0 for every channel.

    Returns:
        FusedCandidate list sorted by fused_score descending. Includes every
        unique case seen in any channel (caller slices down to the bucket size).
    """
    from .models import FusedCandidate  # local import to avoid cycle

    weights = {**DEFAULT_CHANNEL_WEIGHTS, **(weights or {})}

    by_case: dict[str, dict] = {}

    for channel, candidates in channel_results.items():
        w = weights.get(channel, 1.0)
        for c in candidates:
            entry = by_case.setdefault(
                c.case_id,
                {
                    "fused_score": 0.0,
                    "channel_ranks": {},
                    "channel_scores": {},
                    "row": c.row,
                },
            )
            entry["fused_score"] += w / (k + c.rank)
            entry["channel_ranks"][channel] = c.rank
            entry["channel_scores"][channel] = c.score
            # First-seen row wins — all channels should carry the same case
            # metadata, but if they differ (e.g. RPC omitted a field in one
            # channel), prefer the row that has the most keys populated.
            if len(c.row) > len(entry["row"]):
                entry["row"] = c.row

    fused: list[FusedCandidate] = [
        FusedCandidate(
            case_id=case_id,
            fused_score=data["fused_score"],
            channel_ranks=dict(data["channel_ranks"]),
            channel_scores=dict(data["channel_scores"]),
            row=data["row"],
        )
        for case_id, data in by_case.items()
    ]
    fused.sort(key=lambda f: f.fused_score, reverse=True)
    return fused


def wrap_as_fused(candidates: list["ChannelCandidate"]) -> list["FusedCandidate"]:
    """Wrap a single-channel candidate list as FusedCandidate-shape.

    Used by the per-query rerank path so `assemble_kept_cases` (which takes
    `list[FusedCandidate]`) can score a query's own retrieval without going
    through `rrf_fuse`. The resulting "fused_score" is just the channel
    similarity and `channel_ranks` is a one-entry dict.
    """
    from .models import FusedCandidate  # local import to avoid cycle

    return [
        FusedCandidate(
            case_id=c.case_id,
            fused_score=c.score,
            channel_ranks={c.channel: c.rank},
            channel_scores={c.channel: c.score},
            row=c.row,
        )
        for c in candidates
    ]


def assemble_buckets(
    channel_results: dict[str, list["ChannelCandidate"]],
    fused: list["FusedCandidate"],
    *,
    top_per_channel: int = DEFAULT_TOP_PER_CHANNEL,
    top_fused: int = DEFAULT_TOP_FUSED,
) -> dict[str, list["FusedCandidate"]]:
    """Build the 4-bucket output from channel results + the fused list.

    Per-channel buckets re-use FusedCandidate for a uniform downstream type;
    their `fused_score` is the single-channel RRF contribution (weight /
    (k + rank)) so within-bucket ordering still matches the channel ranks.

    Returns a dict with keys:
        "principle"  — top per-channel candidates from the principle channel
        "facts"      — top per-channel candidates from the facts channel
        "basis"      — top per-channel candidates from the basis channel
        "fused"      — top cross-channel RRF consensus
    """
    from .models import FusedCandidate

    buckets: dict[str, list[FusedCandidate]] = {}

    for channel in ("principle", "facts", "basis"):
        candidates = channel_results.get(channel, [])[:top_per_channel]
        buckets[channel] = [
            FusedCandidate(
                case_id=c.case_id,
                fused_score=1.0 / (DEFAULT_RRF_K + c.rank),
                channel_ranks={channel: c.rank},
                channel_scores={channel: c.score},
                row=c.row,
            )
            for c in candidates
        ]

    buckets["fused"] = fused[:top_fused]
    return buckets
