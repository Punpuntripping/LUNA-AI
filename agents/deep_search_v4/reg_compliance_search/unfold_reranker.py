"""Reranker block builders for reg_search (unified-topic corpus).

reg_search now retrieves across FOUR source types through ``search_topics``:
regulation + appendix chunks (`chunks_v2`), circulars (`public.circulars`), and
government services (`public.services`). This module renders each into the
labelled markdown block the reranker grades, dispatched on ``source_type``:

- **chunk** (regulation/appendix) — the historical two-shape unfold below
  (`unfold_chunk_*` + `format_chunk`); appendix chunks get a ``(ملحق)`` tag.
- **circular** — `_format_circular_block` (title + entity + 200-char snippet,
  clamped < 1k chars).
- **service** — `_format_service_block` (ported from the retired compliance
  loop, minus its sector line).

The chunk (`chunks_v2`) unit is linked to one parent regulation
(`regulations_v2`) and to its neighbours by a linked list (`prev_chunk_id` /
`next_chunk_id`). A chunk row is turned into a readable, labelled block for the
reranker in one of two shapes:

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
    CHUNK_SELECT                                 — the column list a chunk row
                                                   needs to be unfoldable

Replaces the legacy 3-tier (article / section / regulation) unfolder.

(The neighbour-fetch helper ``fetch_chunk`` was removed with the reranker's
multi-round ``unfold`` action — reg is single-pass now.)
"""
from __future__ import annotations

import logging
from typing import Any

from supabase import Client as SupabaseClient

# The ONE repeal vocabulary («ملغي»). Shared with the URA enrichment so a
# repealed law can never be phrased two different ways to two different agents
# in the same turn. Returns "" for every non-repealed regulation.
from shared.library.reg_status import status_line

logger = logging.getLogger(__name__)


# -- Truncation caps (these blocks are LLM-prompt payloads) -------------------

MAX_SCOPE_CHARS = 1_500
MAX_SUMMARY_CHARS = 2_000
MAX_CONTEXT_CHARS = 800

# Circular reranker block: title + entity + first ~200 chars of content, whole
# block clamped < 1,000 chars (D2). No sectors (D17), no link.
MAX_CIRCULAR_TITLE_CHARS = 300
MAX_CIRCULAR_CONTENT_CHARS = 200
MAX_CIRCULAR_BLOCK_CHARS = 1_000

# Service reranker block: compact ``service_context`` (ported from the retired
# compliance loop, MINUS the القطاع line — D17).
MAX_SERVICE_CONTEXT_CHARS = 600


# Columns a chunk row must carry to be unfoldable. search.py selects these from
# `chunks_v2` (the prev/next ids remain so the PRECISE view can render the
# three-chunk context window).
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


def _fetch_regulation_meta(
    supabase: SupabaseClient, regulation_id: str | None
) -> dict[str, str]:
    """Return ``{"name", "scope", "status"}`` for a regulation; empties on miss.

    ``name`` is ``clean_title`` (the normalised title) falling back to the raw
    ``title``. ``scope`` is ``regulations_v2.scope`` — left empty when null
    (~a few % of the corpus); callers / the formatter tolerate that.

    ``status`` is the repeal line (``shared.library.reg_status.status_line``)
    and is ``""`` for every regulation that was NOT repealed — which is almost
    all of them. So this adds a field to the candidate block only where it
    matters, and leaves the in-force block byte-identical to before. Without
    it the reranker ranked a «ملغي» chunk against a live one on topical
    similarity alone, with nothing in its input to tell the two apart.
    """
    if not regulation_id:
        return {"name": "", "scope": "", "status": ""}
    try:
        resp = (
            supabase.table("regulations_v2")
            .select("clean_title, title, scope, status_class, status_raw")
            .eq("id", regulation_id)
            .maybe_single()
            .execute()
        )
        if resp and resp.data:
            d = resp.data
            return {
                "name": d.get("clean_title") or d.get("title") or "",
                "scope": d.get("scope") or "",
                "status": status_line(
                    d.get("status_class"), d.get("status_raw")
                ),
            }
    except Exception as e:
        logger.warning("_fetch_regulation_meta(%s) failed: %s", regulation_id, e)
    return {"name": "", "scope": "", "status": ""}


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
        "corpus": chunk.get("corpus"),
        "regulation_id": chunk.get("regulation_id"),
        "regulation_name": reg["name"],
        "regulation_scope": _truncate(reg["scope"], MAX_SCOPE_CHARS),
        "regulation_status": reg["status"],
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
        "corpus": chunk.get("corpus"),
        "regulation_id": chunk.get("regulation_id"),
        "regulation_name": reg["name"],
        "regulation_scope": _truncate(reg["scope"], MAX_SCOPE_CHARS),
        "regulation_status": reg["status"],
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
    """Shared header lines: title, regulation name, status, scope, score.

    Appendix chunks (``corpus == "appendix"``, D13) get a ``(ملحق)`` tag on the
    title line so the reranker knows the material is appendix-level, not the
    main statutory body.

    **حالة النظام** appears ONLY when the parent regulation was repealed
    (``status_line`` returns "" for every other state). An in-force candidate's
    block is therefore unchanged, and the line's mere presence is the signal —
    which is also why the prompt describes it as an exception rather than as a
    field to compare candidates on.
    """
    apx = " (ملحق)" if result.get("corpus") == "appendix" else ""
    lines = [f"### [{label}] {result.get('title') or 'بدون عنوان'}{apx}"]
    name = result.get("regulation_name", "")
    if name:
        lines.append(f"**النظام:** {name}")
    status = result.get("regulation_status", "")
    if status:
        lines.append(f"**حالة النظام:** {status}")
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
    # NULL-context guard (D17 / §1b): appendix chunks have ``context = NULL`` by
    # design, and their appendix-local neighbours likewise. ``unfold_chunk_precise``
    # already normalises those to ``""`` via ``_truncate``; here every سياق line
    # is emitted only when its text is truthy, so an appendix precise block
    # degrades to "summary only" — it never renders "None" and never crashes.
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


# -- Circular / service reranker blocks (flat, one-hop — no unfold) -----------
#
# Circular and service rows arrive from search.py fully hydrated (the row IS
# the reranker view), so — unlike chunks — they need no DB unfold. Each builder
# turns one fetched row into a labelled markdown block for the reranker,
# dispatched on ``source_type`` by ``reranker._make_block``.


def _circular_entity_name(row: dict[str, Any]) -> str:
    """Resolve the issuing entity name from a fetched circular row.

    search.py embeds ``entities(entity_name)`` via the ``circulars_entity_id_fkey``
    FK, so the name arrives nested under ``row["entities"]``. PostgREST returns a
    to-one embed as an object, but a list is tolerated defensively.
    """
    ent = row.get("entities")
    if isinstance(ent, dict):
        return (ent.get("entity_name") or "").strip()
    if isinstance(ent, list) and ent and isinstance(ent[0], dict):
        return (ent[0].get("entity_name") or "").strip()
    return ""


def _format_circular_block(row: dict[str, Any], label: str, score: float) -> str:
    """Render one circular row as a reranker block (§1c, D2, D17).

    Shape: ``### [Cn] تعميم: {title}`` · **الجهة:** {entity} · **درجة الصلة:**
    {score} · first 200 chars of ``content`` + ``...``. No sectors, no link. The
    whole block is clamped < 1,000 chars.
    """
    title = (row.get("title") or "").strip() or "بدون عنوان"
    if len(title) > MAX_CIRCULAR_TITLE_CHARS:
        title = title[:MAX_CIRCULAR_TITLE_CHARS] + "..."
    entity = _circular_entity_name(row)
    content = (row.get("content") or "").strip()
    head = content[:MAX_CIRCULAR_CONTENT_CHARS]
    if len(content) > MAX_CIRCULAR_CONTENT_CHARS:
        head += "..."

    lines = [f"### [{label}] تعميم: {title}"]
    if entity:
        lines.append(f"**الجهة:** {entity}")
    lines.append(f"**درجة الصلة:** {round(float(score or 0.0), 4)}")
    lines.append("")
    lines.append(head)

    block = "\n".join(lines)
    if len(block) > MAX_CIRCULAR_BLOCK_CHARS:
        block = block[:MAX_CIRCULAR_BLOCK_CHARS - 3] + "..."
    return block


def _format_service_block(row: dict[str, Any], label: str, score: float) -> str:
    """Render one service row as a reranker block (§1d, D17).

    Ported from the retired compliance loop's ``_format_service_block`` MINUS
    its القطاع line (D17): header (name + ref), **الجهة** (provider_name),
    **درجة الصلة**, the compact ``service_context`` (≤600 chars), then **الرابط**.
    """
    name = (row.get("service_name_ar") or row.get("intro_title") or "").strip()
    ref = row.get("service_ref") or ""
    lines = [f"### [{label}] خدمة: {name} [ref:{ref}]"]

    provider = (row.get("provider_name") or "").strip()
    if provider:
        lines.append(f"**الجهة:** {provider}")

    lines.append(f"**درجة الصلة:** {round(float(score or 0.0), 4)}")
    lines.append("")

    context = row.get("service_context") or ""
    if len(context) > MAX_SERVICE_CONTEXT_CHARS:
        context = context[:MAX_SERVICE_CONTEXT_CHARS] + "..."
    lines.append(context)
    lines.append("")

    url = row.get("service_url") or row.get("url") or ""
    lines.append(f"**الرابط:** {url if url else '—'}")

    return "\n".join(lines)


__all__ = [
    "CHUNK_SELECT",
    "unfold_chunk_simple",
    "unfold_chunk_precise",
    "format_chunk",
    "_format_circular_block",
    "_format_service_block",
    "_circular_entity_name",
]
