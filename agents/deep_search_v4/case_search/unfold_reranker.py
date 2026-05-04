"""Reranker-side unfolder for case_search (sectioned pipeline).

The `search_case_sections` RPC returns only four fields per row:
    (case_id, case_ref, score, section_text)

The reranker LLM is fed pure ruling text — no court/case#/date/RRF/URL
metadata, no channel labels, no legal-domain tags. Each candidate is
rendered as just `### [N]` followed by the full `cases.content`. The
reranker's question is "does this text answer the sub-query?", and giving
it raw text without metadata anchors keeps it from latching onto surface
features (court name, RRF rank) instead of substance.

The full ruling (not the matching section) is used so the reranker sees the
whole picture — تسبيب + منطوق + وقائع + اسانيد in one block — instead of
guessing keep/drop from one channel slice.

Position N is preserved as the only stable handle: `assemble_kept_cases`
(unfold_ura.py) maps position → case_id via the bucket order, then
re-fetches every keeper from the DB. Nothing is parsed back out of the
markdown.

Shapes this module produces:
    - `enrich_candidates` — decorates ChannelCandidate.row in place with
      `cases.content` via one batched SELECT.
    - `format_bucket_for_reranker` — top-level markdown rendering (the string
      consumed by `build_reranker_user_message`).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from supabase import Client as SupabaseClient
    from .models import ChannelCandidate, FusedCandidate

logger = logging.getLogger(__name__)

# Per-candidate content cap. Saudi rulings are typically 1.5-3k chars; 8k
# absorbs the long tail without blowing reranker context.
MAX_CONTENT_CHARS = 8_000

# Only `id` (for the join) and `content` (for the body) are needed. Header
# fields (court/city/case_number/...) are intentionally NOT pulled — the
# reranker sees text-only.
RERANKER_CASE_FIELDS = (
    "id",
    "case_ref",
    "content",
)


# ─── DB enrichment ────────────────────────────────────────────────────────────


async def fetch_case_headers(
    supabase: "SupabaseClient",
    case_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Batch-fetch `cases.content` for the given case_ids.

    Returns:
        Mapping case_id → row dict containing `id`, `case_ref`, `content`.
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
    """Populate ChannelCandidate.row in place with `cases.content`.

    The RPC row carries `case_id`, `case_ref`, `score`, `section_text`. We
    overwrite that with the full ruling content from `cases` (what the
    reranker actually grades) while keeping `score` so downstream order/log
    code that reads it still works.
    """
    case_ids = {c.case_id for c in candidates}
    headers = await fetch_case_headers(supabase, case_ids)

    for c in candidates:
        hdr = headers.get(c.case_id)
        if hdr:
            c.row = {
                "case_ref": hdr.get("case_ref") or c.row.get("case_ref", ""),
                "content": hdr.get("content") or "",
                "score": c.score,
            }
        else:
            logger.debug("enrich_candidates: no cases row for %s", c.case_id)


# ─── Formatters ───────────────────────────────────────────────────────────────


def _truncate(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text or ""
    return text[:max_chars] + "..."


def format_candidate_for_reranker(
    cand: "FusedCandidate | ChannelCandidate",
    position: int,
) -> str:
    """Render one candidate as `### [N]` + full ruling content.

    No metadata, no scores, no URLs, no domain tags. Position is the only
    handle the reranker uses; `assemble_kept_cases` resolves it back to a
    `case_id` via the bucket order.
    """
    content = (cand.row.get("content") or "").strip()
    body = _truncate(content, MAX_CONTENT_CHARS) if content else "(لا يوجد نص للحكم)"
    return f"### [{position}]\n\n{body}\n"


def format_bucket_for_reranker(
    candidates: list["FusedCandidate | ChannelCandidate"],
    *,
    bucket_label: str = "fused",
) -> tuple[str, int]:
    """Top-level rendering for a sectioned-retrieval bucket.

    Returns:
        (markdown, count)
    """
    if not candidates:
        return "لم يتم العثور على سوابق قضائية مطابقة للاستعلام.", 0

    lines: list[str] = [
        f"## نتائج البحث في السوابق القضائية ({bucket_label}) — {len(candidates)} نتيجة\n"
    ]
    for i, cand in enumerate(candidates, start=1):
        lines.append(format_candidate_for_reranker(cand, i))

    return "\n".join(lines), len(candidates)
