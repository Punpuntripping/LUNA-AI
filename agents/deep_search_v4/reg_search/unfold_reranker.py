"""Chunk unfolders for reg_search (v2 corpus).

The v2 corpus has one retrieval unit — the **chunk** (`chunks_v2`) — linked to
one parent regulation (`regulations_v2`) and to its neighbours by a linked list
(`prev_chunk_id` / `next_chunk_id`). This module turns a bare chunk row into a
readable, labelled block for the reranker, in one of two shapes:

    SIMPLE  — regulation name, regulation scope, chunk summary.
              The form the reranker sees for every keep/drop/unfold decision,
              and the form an unfolded prev/next neighbour arrives in.

    PRECISE — regulation name, regulation scope, prev-chunk context,
              current-chunk context, current-chunk summary, next-chunk context.
              Built for the chunks the reranker keeps. No raw `content` body —
              the summary carries enough signal to decide.

The SIMPLE / PRECISE choice is the *caller's* (search.py) — made from the
chunk's search rank: a top band renders PRECISE, a mid band renders SIMPLE.

Chunks are addressed by a short stable **label** (``C1``, ``C2`` …) assigned
once at render time and never renumbered — the reranker references the label,
never the UUID (too long to transcribe) and never a per-round position (which
renumbers and caused the legacy "article ×6" dedup artifact). Code holds the
``label -> chunk`` map; the UUID is used only for DB hops and dedup.

Public surface:
    unfold_chunk_simple / unfold_chunk_precise   — chunk row -> unfolded dict
    format_chunk                                 — unfolded dict -> markdown block
    fetch_chunk                                  — chunk_id -> chunk row
                                                   (used to pull a neighbour in)
    CHUNK_SELECT                                 — the column list a chunk row
                                                   needs to be unfoldable

Replaces the legacy 3-tier (article / section / regulation) unfolder.
"""
from __future__ import annotations

import logging
from typing import Any

from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)


# -- Truncation caps (these blocks are LLM-prompt payloads) -------------------

MAX_SCOPE_CHARS = 1_500
MAX_SUMMARY_CHARS = 2_000
MAX_CONTEXT_CHARS = 800


# Columns a chunk row must carry to be unfoldable (and re-unfoldable after a
# neighbour hop). search.py selects these from `chunks_v2`; `fetch_chunk` below
# uses the same list so a pulled-in neighbour is interchangeable with a
# search-result chunk.
CHUNK_SELECT = (
    "id, chunk_ref, regulation_id, position, "
    "prev_chunk_id, next_chunk_id, title, summary, context"
)


def _truncate(text: str | None, max_chars: int) -> str:
    """Truncate text to max_chars, appending '...' if cut. None -> ''."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


# -- DB fetch helpers ---------------------------------------------------------


def fetch_chunk(supabase: SupabaseClient, chunk_id: str | None) -> dict[str, Any] | None:
    """Fetch one `chunks_v2` row with the columns the unfolders need.

    Used by the reranker to pull a chunk's prev/next neighbour into the
    candidate pool. The returned row is shaped exactly like a search-result
    chunk, so it can be fed straight into ``unfold_chunk_simple`` /
    ``unfold_chunk_precise``.
    """
    if not chunk_id:
        return None
    try:
        resp = (
            supabase.table("chunks_v2")
            .select(CHUNK_SELECT)
            .eq("id", chunk_id)
            .maybe_single()
            .execute()
        )
        return resp.data if resp and resp.data else None
    except Exception as e:
        logger.warning("fetch_chunk(%s) failed: %s", chunk_id, e)
        return None


def _fetch_regulation_meta(
    supabase: SupabaseClient, regulation_id: str | None
) -> dict[str, str]:
    """Return ``{"name", "scope"}`` for a regulation; empties on miss.

    ``name`` is ``clean_title`` (the normalised title) falling back to the raw
    ``title``. ``scope`` is ``regulations_v2.scope`` — left empty when null
    (~a few % of the corpus); callers / the formatter tolerate that.
    """
    if not regulation_id:
        return {"name": "", "scope": ""}
    try:
        resp = (
            supabase.table("regulations_v2")
            .select("clean_title, title, scope")
            .eq("id", regulation_id)
            .maybe_single()
            .execute()
        )
        if resp and resp.data:
            d = resp.data
            return {
                "name": d.get("clean_title") or d.get("title") or "",
                "scope": d.get("scope") or "",
            }
    except Exception as e:
        logger.warning("_fetch_regulation_meta(%s) failed: %s", regulation_id, e)
    return {"name": "", "scope": ""}


def _fetch_contexts(
    supabase: SupabaseClient, chunk_ids: list[str | None]
) -> dict[str, str]:
    """Fetch the `context` field for several chunks in one `in_` query.

    Returns a ``{chunk_id: context}`` map. Ids that are None or absent from the
    result simply don't appear in the map.
    """
    ids = [cid for cid in chunk_ids if cid]
    if not ids:
        return {}
    try:
        resp = (
            supabase.table("chunks_v2")
            .select("id, context")
            .in_("id", ids)
            .execute()
        )
        if resp and resp.data:
            return {r["id"]: (r.get("context") or "") for r in resp.data}
    except Exception as e:
        logger.warning("_fetch_contexts(%s) failed: %s", ids, e)
    return {}


# -- Unfolders ----------------------------------------------------------------


def unfold_chunk_simple(
    supabase: SupabaseClient, chunk: dict[str, Any]
) -> dict[str, Any]:
    """SIMPLE unfold — regulation name, regulation scope, chunk summary.

    The form the reranker sees for every keep/drop/unfold decision, and the
    form an unfolded prev/next neighbour arrives in. One DB hop: the parent
    regulation.

    Args:
        supabase: Supabase client.
        chunk: A `chunks_v2` row (see ``CHUNK_SELECT``).

    Returns:
        Unfolded dict with ``mode == "simple"``.
    """
    reg = _fetch_regulation_meta(supabase, chunk.get("regulation_id"))
    return {
        "mode": "simple",
        "id": chunk.get("id"),
        "chunk_ref": chunk.get("chunk_ref", ""),
        "title": chunk.get("title", ""),
        "regulation_id": chunk.get("regulation_id"),
        "regulation_name": reg["name"],
        "regulation_scope": _truncate(reg["scope"], MAX_SCOPE_CHARS),
        "summary": _truncate(chunk.get("summary"), MAX_SUMMARY_CHARS),
    }


def unfold_chunk_precise(
    supabase: SupabaseClient, chunk: dict[str, Any]
) -> dict[str, Any]:
    """PRECISE unfold — regulation name + scope, prev/current/next chunk
    context, and the current chunk summary.

    Built for chunks the reranker keeps. There is **no raw `content` body** by
    design — the chunk summary plus the three-chunk context window carry enough
    for the downstream stage. Two DB hops: the parent regulation, and both
    linked-list neighbours in one `in_` query.

    Args:
        supabase: Supabase client.
        chunk: A `chunks_v2` row (see ``CHUNK_SELECT``).

    Returns:
        Unfolded dict with ``mode == "precise"``. ``prev_context`` /
        ``next_context`` are ``None`` when there is no neighbour (corpus
        boundary) and ``""`` when the neighbour exists but has no stored
        context — the formatter renders the two cases differently.
    """
    reg = _fetch_regulation_meta(supabase, chunk.get("regulation_id"))

    prev_id = chunk.get("prev_chunk_id")
    next_id = chunk.get("next_chunk_id")
    ctx = _fetch_contexts(supabase, [prev_id, next_id])

    return {
        "mode": "precise",
        "id": chunk.get("id"),
        "chunk_ref": chunk.get("chunk_ref", ""),
        "title": chunk.get("title", ""),
        "regulation_id": chunk.get("regulation_id"),
        "regulation_name": reg["name"],
        "regulation_scope": _truncate(reg["scope"], MAX_SCOPE_CHARS),
        "prev_context": (
            _truncate(ctx.get(prev_id, ""), MAX_CONTEXT_CHARS)
            if prev_id else None
        ),
        "context": _truncate(chunk.get("context"), MAX_CONTEXT_CHARS),
        "summary": _truncate(chunk.get("summary"), MAX_SUMMARY_CHARS),
        "next_context": (
            _truncate(ctx.get(next_id, ""), MAX_CONTEXT_CHARS)
            if next_id else None
        ),
    }


# -- Formatting ---------------------------------------------------------------


def _format_scores(result: dict[str, Any]) -> str:
    """Relevance-score line from `_score` (fused/semantic) and `_reranker_score`."""
    parts: list[str] = []
    score = result.get("_score")
    if score is not None:
        parts.append(f"الترتيب: {round(float(score), 4)}")
    rerank = result.get("_reranker_score")
    if rerank is not None:
        parts.append(f"Jina: {round(float(rerank), 4)}")
    return f"**درجة الصلة:** {' | '.join(parts)}" if parts else ""


def format_chunk(result: dict[str, Any], label: str) -> str:
    """Render an unfolded chunk into a labelled markdown block.

    Args:
        result: An unfolded dict from ``unfold_chunk_simple`` /
            ``unfold_chunk_precise``.
        label: The chunk's stable short handle (e.g. ``C7``). The reranker
            references this in its decisions — never the UUID.

    Returns:
        A markdown block headed ``### [<label>] <title>``.
    """
    if result.get("mode") == "precise":
        return _format_precise(result, label)
    return _format_simple(result, label)


def _format_header(result: dict[str, Any], label: str) -> list[str]:
    """Shared header lines: title, regulation name, regulation scope, score."""
    lines = [f"### [{label}] {result.get('title') or 'بدون عنوان'}"]
    name = result.get("regulation_name", "")
    if name:
        lines.append(f"**النظام:** {name}")
    scope = result.get("regulation_scope", "")
    if scope:
        lines.append(f"**نطاق النظام:** {scope}")
    score = _format_scores(result)
    if score:
        lines.append(score)
    lines.append("")
    return lines


def _format_simple(result: dict[str, Any], label: str) -> str:
    lines = _format_header(result, label)
    summary = result.get("summary", "")
    lines.append(
        f"**ملخص المقطع:** {summary}" if summary
        else "**ملخص المقطع:** (لا يوجد ملخص)"
    )
    return "\n".join(lines)


def _format_precise(result: dict[str, Any], label: str) -> str:
    lines = _format_header(result, label)

    prev_ctx = result.get("prev_context")
    if prev_ctx is None:
        lines.append("**سياق المقطع السابق:** (بداية النظام — لا يوجد مقطع سابق)")
    elif prev_ctx:
        lines.append(f"**سياق المقطع السابق:** {prev_ctx}")

    ctx = result.get("context", "")
    if ctx:
        lines.append(f"**سياق المقطع الحالي:** {ctx}")

    summary = result.get("summary", "")
    lines.append(
        f"**ملخص المقطع الحالي:** {summary}" if summary
        else "**ملخص المقطع الحالي:** (لا يوجد ملخص)"
    )

    next_ctx = result.get("next_context")
    if next_ctx is None:
        lines.append("**سياق المقطع التالي:** (نهاية النظام — لا يوجد مقطع تالٍ)")
    elif next_ctx:
        lines.append(f"**سياق المقطع التالي:** {next_ctx}")

    return "\n".join(lines)


__all__ = [
    "CHUNK_SELECT",
    "fetch_chunk",
    "unfold_chunk_simple",
    "unfold_chunk_precise",
    "format_chunk",
]
