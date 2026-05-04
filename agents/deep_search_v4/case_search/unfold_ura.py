"""Aggregator-side unfolder for case_search (sectioned pipeline).

What the URA / aggregator sees — full ruling content with
referenced_regulations and appeal history. This runs AFTER the reranker has
picked the keepers, batch-fetches the full case rows, and builds
`RerankedCaseResult` objects for the shared deep_search_v3 aggregator to
synthesize from.

Counterpart to `unfold_reranker.py`, which produces the compact section-only
markdown the reranker LLM grades. Here we hand the aggregator everything it
needs to write the synthesis: full content (clipped), legal_domains,
referenced_regulations (clipped), and appeal_result.

Shapes this module produces:
    - `fetch_full_cases` — batched `cases` SELECT with all aggregator fields.
    - `assemble_kept_cases` — public entry point: takes the reranker's keep
      list + the original fused bucket, returns RerankedCaseResult objects
      in bucket order.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from supabase import Client as SupabaseClient
    from .models import FusedCandidate, RerankedCaseResult

logger = logging.getLogger(__name__)

MAX_AGGREGATOR_CONTENT_CHARS = 8_000

# How many referenced-regulation entries to surface. Most cases cite 2–5.
MAX_REFERENCED_REGULATIONS = 8

# Fields the aggregator actually reads. Everything else stays in the DB.
AGGREGATOR_CASE_FIELDS = (
    "id",
    "case_ref",
    "court",
    "city",
    "court_level",
    "case_number",
    "judgment_number",
    "date_hijri",
    "date_gregorian",
    "details_url",
    "content",
    "legal_domains",
    "referenced_regulations",
    "appeal_court",
    "appeal_city",
    "appeal_judgment_number",
    "appeal_date_hijri",
    "appeal_result",
)


# ─── DB fetch ─────────────────────────────────────────────────────────────────


async def fetch_full_cases(
    supabase: "SupabaseClient",
    case_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Batch-fetch full case rows for the given case_ids.

    Returns:
        Mapping case_id → full case row (AGGREGATOR_CASE_FIELDS).
    """
    ids = [cid for cid in {*case_ids} if cid]
    if not ids:
        return {}

    def _call() -> list[dict]:
        try:
            resp = (
                supabase.table("cases")
                .select(",".join(AGGREGATOR_CASE_FIELDS))
                .in_("id", ids)
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.warning(
                "fetch_full_cases failed for %d ids: %s", len(ids), e,
            )
            return []

    rows = await asyncio.to_thread(_call)
    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        cid = r.get("id")
        if cid:
            by_id[str(cid)] = r
    return by_id


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _truncate(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars] + "..."


def _assemble_title(row: dict[str, Any]) -> str:
    """court | case_number | date_hijri — consistent with legacy reranker."""
    court = row.get("court", "") or ""
    case_num = row.get("case_number", "") or ""
    date = row.get("date_hijri", "") or ""
    return " | ".join(p for p in (court, case_num, date) if p)


def _build_reranked_case_result(
    full_row: dict[str, Any],
    *,
    channel_ranks: dict[str, int] | None,
    fused_score: float,
    relevance: str,
    reasoning: str,
) -> "RerankedCaseResult":
    """Construct one RerankedCaseResult from a full-case row + reranker decision."""
    from .models import RerankedCaseResult

    court_level_raw = full_row.get("court_level", "") or ""
    court_level = "appeal" if court_level_raw == "appeal" else "first_instance"

    legal_domains = full_row.get("legal_domains") or []
    if isinstance(legal_domains, str):
        legal_domains = [legal_domains]

    refs = full_row.get("referenced_regulations") or []
    if isinstance(refs, list):
        refs = refs[:MAX_REFERENCED_REGULATIONS]
    else:
        refs = []

    content = _truncate(full_row.get("content", "") or "", MAX_AGGREGATOR_CONTENT_CHARS)

    return RerankedCaseResult(
        title=_assemble_title(full_row),
        court=full_row.get("court") or None,
        city=full_row.get("city") or None,
        court_level=court_level,
        case_number=full_row.get("case_number") or None,
        judgment_number=full_row.get("judgment_number") or None,
        date_hijri=full_row.get("date_hijri") or None,
        content=content,
        legal_domains=list(legal_domains),
        referenced_regulations=list(refs),
        appeal_result=full_row.get("appeal_result") or None,
        score=fused_score,
        relevance=relevance if relevance in ("high", "medium") else "medium",
        reasoning=reasoning or "",
        db_id=full_row.get("case_ref") or str(full_row.get("id") or ""),
    )


# ─── Public API ───────────────────────────────────────────────────────────────


async def assemble_kept_cases(
    supabase: "SupabaseClient",
    *,
    kept_decisions: list[dict[str, Any]],
    fused_bucket: list["FusedCandidate"],
) -> list["RerankedCaseResult"]:
    """Build RerankedCaseResult objects for the reranker's keep list.

    Args:
        supabase: client for the batched `cases` SELECT.
        kept_decisions: list of `{position, relevance, reasoning}` dicts —
            one per reranker decision tagged `action == "keep"`.
        fused_bucket: the exact FusedCandidate list the reranker saw
            (the one rendered via format_bucket_for_reranker). Positions
            are 1-based indices into this list.

    Returns:
        RerankedCaseResult list in the original bucket order (by position).
        Positions that point outside the bucket or reference a case the DB
        can't find are skipped (logged as a warning).
    """
    if not kept_decisions or not fused_bucket:
        return []

    # Map position → (candidate, decision metadata)
    by_position: dict[int, dict[str, Any]] = {}
    for d in kept_decisions:
        pos = int(d.get("position", 0))
        if 1 <= pos <= len(fused_bucket):
            by_position[pos] = d
        else:
            logger.warning(
                "assemble_kept_cases: position %s out of bucket range (size=%d)",
                pos, len(fused_bucket),
            )

    if not by_position:
        return []

    # Resolve case_ids and batch-fetch full rows
    selected = [(pos, fused_bucket[pos - 1]) for pos in sorted(by_position)]
    case_ids = [c.case_id for _, c in selected]
    full_rows = await fetch_full_cases(supabase, case_ids)

    out: list["RerankedCaseResult"] = []
    for pos, cand in selected:
        full = full_rows.get(cand.case_id)
        if not full:
            logger.warning(
                "assemble_kept_cases: cases row missing for case_id=%s (pos=%d)",
                cand.case_id, pos,
            )
            continue
        dec = by_position[pos]
        out.append(
            _build_reranked_case_result(
                full,
                channel_ranks=cand.channel_ranks,
                fused_score=cand.fused_score,
                relevance=str(dec.get("relevance") or "medium"),
                reasoning=str(dec.get("reasoning") or ""),
            )
        )
    return out


# ─── Optional: standalone markdown for the aggregator prompt ─────────────────
# reg_search builds the aggregator message inside aggregator_prompts.py from
# RerankedResult fields directly. The shared deep_search_v3 aggregator does
# the same for case_search, so we don't format markdown here — we only hand
# back RerankedCaseResult objects. Keeping this note so the contract is
# explicit: the aggregator owns its own rendering.
