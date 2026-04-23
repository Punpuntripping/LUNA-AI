"""Reranker-side unfolder for case_search (sectioned pipeline).

The `search_case_sections` RPC returns only four fields per row:
    (case_id, case_ref, score, section_text)

The reranker LLM needs a compact markdown block per candidate so it can
classify keep/drop against a typed sub-query. What it needs is:

    - A stable 1-based header `### [N] حكم: <court> — <city> (<level>)`
    - Minimal identification: case_number / judgment_number / date_hijri
    - The section_text that actually matched (principle / facts / basis) — NOT
      the full ruling. That is deliberate: the reranker's question is "does
      THIS text answer the sub-query?", so handing it the full ruling would
      blur the signal and cost tokens.
    - Legal-domain tags for at-a-glance domain check.
    - Channel provenance ("principle: #1 · facts: #3") when the candidate
      comes from the fused bucket.

Anything beyond that — full ruling content, referenced_regulations, appeal
history — belongs in `case_unfold_aggregator.py`, which runs AFTER the
reranker has picked the keepers.

Mirrors the split in reg_search:
    format_unfolded_result_precise (compact, for reranker)
    format_unfolded_result         (full, for aggregator / downstream)

Shapes this module produces:
    - `enrich_candidates` — decorates ChannelCandidate.row in place with
      minimal case-header metadata via one batched `cases` SELECT.
    - `format_bucket_for_reranker` — top-level markdown rendering (the string
      consumed by `build_reranker_user_message`).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from supabase import Client as SupabaseClient
    from .models import ChannelCandidate, FusedCandidate, RerankedCaseResult

logger = logging.getLogger(__name__)

# How much of the section text to include per candidate. Long basis sections
# can exceed 19k chars in the DB (statute concatenations); clip for token cost.
MAX_SECTION_CHARS = 2_500

# Minimal case-header fields the reranker needs. NB: `content` (full ruling)
# is intentionally excluded — that's aggregator-only.
RERANKER_CASE_FIELDS = (
    "id",
    "case_ref",
    "court",
    "city",
    "court_level",
    "case_number",
    "judgment_number",
    "date_hijri",
    "details_url",
    "legal_domains",
)

CHANNEL_LABELS = {
    "principle": "المبدأ (تسبيب + منطوق)",
    "facts":     "الوقائع (ملخص + وقائع + مطالبات)",
    "basis":     "الاسانيد (استنادات + أنظمة)",
}


# ─── DB enrichment ────────────────────────────────────────────────────────────


async def fetch_case_headers(
    supabase: "SupabaseClient",
    case_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Batch-fetch minimal case headers for the given case_ids.

    Returns:
        Mapping case_id → header dict (with RERANKER_CASE_FIELDS).
        Missing case_ids are simply absent from the dict.
    """
    ids = [cid for cid in {*case_ids} if cid]
    if not ids:
        return {}

    def _call() -> list[dict]:
        try:
            resp = (
                supabase.table("cases")
                .select(",".join(RERANKER_CASE_FIELDS))
                .in_("id", ids)
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.warning("fetch_case_headers failed for %d ids: %s", len(ids), e)
            return []

    rows = await asyncio.to_thread(_call)
    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        cid = r.get("id")
        if cid:
            by_id[str(cid)] = r
    return by_id


async def enrich_candidates(
    supabase: "SupabaseClient",
    candidates: list["ChannelCandidate"],
) -> None:
    """Populate ChannelCandidate.row in place with header metadata.

    The RPC row only carries `case_id`, `case_ref`, `score`, `section_text`.
    We keep `section_text` and `score` on the candidate and layer the header
    fields on top. Cases that are missing from the `cases` table (shouldn't
    happen, but defensive) retain whatever the RPC returned.
    """
    case_ids = {c.case_id for c in candidates}
    headers = await fetch_case_headers(supabase, case_ids)

    for c in candidates:
        hdr = headers.get(c.case_id)
        if hdr:
            merged = dict(hdr)
            # Preserve the channel section_text and score that the RPC gave us.
            # Don't let `cases.content` or other tables overwrite them.
            merged["section_text"] = c.row.get("section_text", "")
            merged["case_ref"] = hdr.get("case_ref") or c.row.get("case_ref", "")
            merged["score"] = c.score
            c.row = merged
        else:
            # Keep RPC row as-is; flag for log.
            logger.debug("enrich_candidates: no cases row for %s", c.case_id)


# ─── Formatters ───────────────────────────────────────────────────────────────


def _truncate(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars] + "..."


def _format_header(row: dict[str, Any], channel: str, position: int) -> list[str]:
    """Build the 1-based `### [N]` header + identification lines."""
    lines: list[str] = []
    court = row.get("court", "") or ""
    city = row.get("city", "") or ""
    court_level_raw = row.get("court_level", "") or ""
    level_label = "استئناف" if court_level_raw == "appeal" else "ابتدائي"

    header = f"### [{position}] حكم: {court}" if court else f"### [{position}] حكم"
    if city:
        header += f" — {city}"
    header += f" ({level_label})"
    lines.append(header)

    channel_label = CHANNEL_LABELS.get(channel, channel)
    lines.append(f"**القناة المُطابِقة:** {channel_label}")

    meta_parts: list[str] = []
    case_number = row.get("case_number", "") or ""
    judgment_number = row.get("judgment_number", "") or ""
    if case_number:
        meta_parts.append(f"**رقم القضية:** {case_number}")
    if judgment_number:
        meta_parts.append(f"**رقم الحكم:** {judgment_number}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    date_hijri = row.get("date_hijri", "") or ""
    if date_hijri:
        lines.append(f"**التاريخ:** {date_hijri}")

    return lines


def _format_score_and_provenance(
    row: dict[str, Any],
    channel_ranks: dict[str, int] | None,
) -> list[str]:
    """Score line + channel provenance ("appeared in X: #1, Y: #3")."""
    lines: list[str] = []
    score = row.get("score")
    if score is not None:
        lines.append(f"**درجة الصلة:** RRF: {round(float(score), 4)}")
    if channel_ranks:
        provenance = " · ".join(
            f"{ch}: #{rank}"
            for ch, rank in sorted(channel_ranks.items(), key=lambda kv: kv[1])
        )
        lines.append(f"**ظهر في القنوات:** {provenance}")
    return lines


def _format_domains(row: dict[str, Any]) -> list[str]:
    domains = row.get("legal_domains") or []
    if not domains:
        return []
    if isinstance(domains, list):
        return [f"**المجالات القانونية:** {' · '.join(str(d) for d in domains)}"]
    return [f"**المجالات القانونية:** {domains}"]


def format_candidate_for_reranker(
    cand: "FusedCandidate | ChannelCandidate",
    position: int,
) -> str:
    """Render one candidate as a reranker-ready markdown block.

    Works for both FusedCandidate (fused bucket, carries channel_ranks) and
    ChannelCandidate (single-channel bucket).
    """
    row = dict(cand.row)
    # Resolve which channel to label with
    if hasattr(cand, "channel_ranks") and cand.channel_ranks:
        # FusedCandidate — label with the channel where it ranked best
        primary_channel = min(cand.channel_ranks.items(), key=lambda kv: kv[1])[0]
        channel_ranks = cand.channel_ranks
    else:
        # ChannelCandidate
        primary_channel = getattr(cand, "channel", "")
        channel_ranks = None

    # Fused score override so reranker sees one signal
    if hasattr(cand, "fused_score"):
        row["score"] = cand.fused_score
    elif hasattr(cand, "score"):
        row["score"] = cand.score

    lines = _format_header(row, primary_channel, position)
    lines.extend(_format_score_and_provenance(row, channel_ranks))

    details_url = row.get("details_url", "") or ""
    if details_url:
        lines.append(f"**رابط التفاصيل:** {details_url}")

    lines.append("")

    section_text = row.get("section_text", "") or ""
    if section_text:
        lines.append(_truncate(section_text, MAX_SECTION_CHARS))
        lines.append("")

    lines.extend(_format_domains(row))
    return "\n".join(lines)


def _collect_references(candidates: list["FusedCandidate | ChannelCandidate"]) -> str:
    """Deduplicated `<references>` block — case_ref, court, city."""
    seen: set[str] = set()
    ref_lines: list[str] = []
    for c in candidates:
        row = c.row
        case_ref = row.get("case_ref", "") or ""
        court = row.get("court", "") or ""
        city = row.get("city", "") or ""
        key = case_ref or row.get("case_number", "")
        if not key or key in seen:
            continue
        seen.add(key)
        parts = [case_ref, court]
        if city:
            parts.append(city)
        ref_lines.append(f"- {' | '.join(p for p in parts if p)}")
    if not ref_lines:
        return ""
    return "<references>\n" + "\n".join(ref_lines) + "\n</references>"


def format_bucket_for_reranker(
    candidates: list["FusedCandidate | ChannelCandidate"],
    *,
    bucket_label: str = "fused",
) -> tuple[str, int]:
    """Top-level rendering for a sectioned-retrieval bucket.

    Mirrors the legacy `format_fused_bucket` signature but lives in its own
    module so the reranker/aggregator split stays explicit.

    Returns:
        (markdown, count)
    """
    if not candidates:
        return "لم يتم العثور على سوابق قضائية مطابقة للاستعلام.", 0

    lines: list[str] = [
        f"## نتائج البحث في السوابق القضائية (بعد الدمج — {bucket_label}) — {len(candidates)} نتيجة\n"
    ]
    for i, cand in enumerate(candidates, start=1):
        lines.append(format_candidate_for_reranker(cand, i))

    refs = _collect_references(candidates)
    if refs:
        lines.append("\n---")
        lines.append(refs)

    return "\n".join(lines), len(candidates)


# ============================================================================
# AGGREGATOR-SIDE UNFOLDER
# ============================================================================

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
