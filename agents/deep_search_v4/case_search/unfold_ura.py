"""Aggregator-side unfolder for case_search (sectioned pipeline).

What the URA / aggregator sees — the case's **structured summary**
(`cases.summary`) with court metadata, referenced_regulations and appeal
history. This runs AFTER the reranker has picked the keepers, batch-fetches
the case rows, and builds `RerankedCaseResult` objects for the shared
deep_search_v3 aggregator to synthesize from.

Counterpart to `unfold_reranker.py`, which produces the compact markdown the
reranker LLM grades. Here we hand the aggregator everything it needs to write
the synthesis: the summary (clipped), court / court_level, legal_domains,
referenced_regulations (clipped), and appeal_result.

`summary` replaces `cases.content` as the synthesis payload (plan
`case_topics_loop.md` §8.1, decision D2 — a HARD replacement). `summary` is
structured markdown (`## الملخص / ## الوقائع / ## المطالبات / ## اسانيد … /
## التسبيب / ## المنطوق`), ~4× cheaper than the raw ruling text and better
organised. Accepted consequence of D2: the aggregator post-validator grounds
against the summary, so summaries are now the citable substrate. The full
ruling text is still served to the *user* view by `source_viewer.py`, which
re-fetches `cases.content` from the DB.

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

# Cap for the aggregator payload (now `cases.summary`, not `cases.content`).
# Measured over 30,531 rows: summary p50 = 2,035 / p90 = 3,290 / p99 = 4,856 /
# max = 21,735 chars. 6,000 clears p99 and clips only the long tail.
MAX_AGGREGATOR_CONTENT_CHARS = 6_000

# How many referenced-regulation entries to surface. Most cases cite 2–5.
MAX_REFERENCED_REGULATIONS = 8

# `cases.court_level` vocabulary + the three-value passthrough live in
# `shared/court_levels.py` — the single canonical home. Re-exported here for
# the callers/tests that already import them from this module.
#
# History: the two-value coercion this replaced
# (``"appeal" if raw == "appeal" else "first_instance"``) silently relabelled
# all 125 supreme-court rulings as first_instance, so the aggregator told the
# user a court-of-last-resort ruling came from a court of first instance. The
# same collapse existed independently in three other modules — hence one
# shared home rather than a per-module constant.
from agents.deep_search_v4.shared.court_levels import (  # noqa: E402
    CASE_COURT_LEVELS,
    normalize_court_level,
)

# Fields the aggregator actually reads. Everything else stays in the DB.
# `summary` (not `content`) is the synthesis payload — D2. `short_summary` is
# carried only as the NULL-summary fallback (summary is NULL on 18 of 30,531
# cases; short_summary is NULL/empty on 964).
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
    "summary",
    "short_summary",
    # Last-resort payload only (see _resolve_summary): 18 cases have neither
    # summary field. NOT the synthesis payload — D2 replaced it with `summary`.
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


def _resolve_summary(full_row: dict[str, Any]) -> str:
    """Return the aggregator payload for one case row, clipped.

    `summary` → `short_summary` → `content` → ``""``. Never returns ``None``
    and never stringifies a NULL into ``"None"``: `summary` is NULL on 18 of
    30,531 rows and `short_summary` on a further 964, and either would
    otherwise land in the synthesis prompt verbatim.

    The `content` rung is a deliberate carve-out from decision D2 ("summary is
    a hard replacement for content"). D2's intent is that the aggregator reads
    the structured summary instead of raw ruling text — NOT that a case with no
    summary ships as an empty citable reference. 18 cases have neither summary
    field; 17 of them do have `content`. Without this rung those 17 reach the
    aggregator as a numbered `<reference>` with an empty `<content>`, which is
    strictly worse than the pre-D2 behaviour: the model can cite them and has
    nothing to ground the citation in.

    This matters more than 18/30,531 suggests. Every one of those rows sits
    inside the 9,861-case dark set that this whole retarget exists to make
    reachable — the null-payload rows and the newly-reachable rows are the SAME
    rows. Before Wave 1 they were unreachable, so the empty-payload path could
    never fire; after Wave 1 it fires for the first time.

    The 18th case has empty `content` too and legitimately yields ``""``.
    """
    summary = str(full_row.get("summary") or "").strip()
    if not summary:
        summary = str(full_row.get("short_summary") or "").strip()
    if not summary:
        # Last resort only — see the D2 carve-out above.
        summary = str(full_row.get("content") or "").strip()
    return _truncate(summary, MAX_AGGREGATOR_CONTENT_CHARS)


def _build_reranked_case_result(
    full_row: dict[str, Any],
    *,
    channel_ranks: dict[str, int] | None,
    fused_score: float,
    relevance: str,
    reasoning: str,
) -> "RerankedCaseResult":
    """Construct one RerankedCaseResult from a case row + reranker decision.

    ``RerankedCaseResult.content`` carries the **summary** (D2), not the ruling
    text — the field name is historical.
    """
    from .models import RerankedCaseResult

    court_level = normalize_court_level(full_row.get("court_level"))

    legal_domains = full_row.get("legal_domains") or []
    if isinstance(legal_domains, str):
        legal_domains = [legal_domains]

    refs = full_row.get("referenced_regulations") or []
    if isinstance(refs, list):
        refs = refs[:MAX_REFERENCED_REGULATIONS]
    else:
        refs = []

    # D2: `cases.summary` replaces `cases.content` as the synthesis payload.
    content = _resolve_summary(full_row)

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
        db_uuid=str(full_row.get("id") or ""),
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
