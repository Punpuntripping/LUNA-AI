"""Search pipeline for reg_search — unified topic search over ``search_topics``.

reg_search is the single retrieval executor over the unified
``public.search_topics`` layer, which spans FOUR source types sharing one
1024-d Alibaba ``text-embedding-v4`` vector space: **regulation** and
**appendix** chunks (content in ``chunks_v2``), **circular** documents
(``public.circulars``), and government **services** (``public.services``).

``search_regulations_pipeline`` for one sub-query:

1. **Embed** the query (or use the loop's precomputed batch embedding).
2. **Resolve sectors BEFORE the RPC** — await the shared ``sector_picker``
   future with its bounded grace (compliance precedent); ``None`` or a
   non-empty list. ``[]`` is never sent to the RPC (``sectors && '{}'`` is
   always false → zero rows).
3. **RPC** ``search_topics(p_query_embedding, p_per_type=15, [p_sectors])`` —
   ``p_types`` omitted (NULL = all four). Rows return per-type-deduped
   (server-side ``DISTINCT ON (doc_id)``) with ``score = 1 - cosine``. On 0
   rows WITH a sector filter, retry once unfiltered.
4. **Merge** — one pool: sort ALL rows by ``score`` DESC, cut the global top-15
   (no per-type quotas downstream — D1). A ``seen source_id`` guard belt-and-
   suspenders the RPC's per-call uniqueness.
5. **Per-type content fetch** — concurrent (``asyncio.gather``) through one
   shared ``_fetch_by_ids`` helper with per-type column trims (never
   ``select("*")`` — it would drag embeddings / original_markdown / fts).
6. **Band + tag** — precise for the top-5 CHUNK rows only (D12); simple for the
   rest of the chunks; flat for circular/service. ``_rrf`` = the RPC score
   copied directly. Every row carries ``source_type`` + merged RPC provenance.

Replaces the legacy ``search_chunk_titles``-only pipeline (that RPC is now
legacy — see ``planning/REG_SEARCH_V2_REFRAME.md`` and the upstream reference
``agentic_for_ministry/ingestion/search_topics/REFERENCE.md``).

The Supabase client is sync; this module is async — every Supabase call is
wrapped in ``asyncio.to_thread`` so it never blocks the event loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from agents.deep_search_v4.sector_picker.consume import resolve_sector_filter

from .unfold_reranker import CHUNK_SELECT

if TYPE_CHECKING:
    from .models import RegComplianceSearchDeps

logger = logging.getLogger(__name__)

# Per-type quota requested from the ``search_topics`` RPC (all four types). The
# RPC self-tunes ``hnsw.ef_search`` from ``p_overfetch``; the old MATCH_COUNT /
# EF_SEARCH truncation trap is gone.
PER_TYPE = 15

# Global top-N kept after merging all four types into ONE pool (D1). The type
# mix is whatever similarity says — no per-type quotas downstream.
TOP_K = 15

# Rank-band boundary: the top-5 CHUNK rows render "precise" (prev/next context);
# remaining chunks render "simple"; circular/service always render "flat" and
# never consume a precise slot (D12).
PRECISE_BAND = 5

# Batch size for the id-``in_`` content fetches.
ID_BATCH = 180

# Source types whose content lives in ``chunks_v2`` (regulation body + appendix).
_CHUNK_TYPES = ("regulation", "appendix")

# Per-type content SELECTs — payload trims (never ``select("*")``: that would
# drag ``services.embedding`` [1024-d vector], ``original_markdown`` and ``fts``).
# Sectors are NOT re-fetched here — they ride on the RPC row.
CHUNK_SELECT_TOPICS = CHUNK_SELECT + ", corpus"  # D13: appendix (ملحق) label
CIRCULAR_SELECT = (
    # Entity name is embedded via the single ``circulars_entity_id_fkey`` FK
    # (circulars.entity_id -> entities.id) so the reranker/aggregator blocks can
    # show the issuing authority without a second round-trip.
    "id, circ_ref, title, content, entity_ref, source, "
    "entities!circulars_entity_id_fkey(entity_name)"
)
SERVICE_SELECT = (
    "id, service_ref, service_name_ar, provider_name, service_context, "
    "intro_title, intro_description, steps, requirements, required_documents, "
    "service_url, url"
)


# -- Unified topic pipeline ---------------------------------------------------


async def search_regulations_pipeline(
    query: str,
    deps: RegComplianceSearchDeps,
    filter_sectors: list[str] | None = None,
    precomputed_embedding: list[float] | None = None,
    semaphore: asyncio.Semaphore | None = None,
    filter_sectors_future: "asyncio.Future[list[str] | None] | None" = None,
) -> tuple[list[dict], int]:
    """Search the unified topic layer for one sub-query.

    Args:
        query: Arabic search query.
        deps: RegComplianceSearchDeps with ``supabase`` and ``embedding_fn``.
        filter_sectors: Static sector list (CLI / smoke paths with no picker).
            Applied at the RPC when non-empty; ignored when
            ``filter_sectors_future`` is set.
        precomputed_embedding: Pre-computed 1024-dim embedding. Skips the embed
            step when provided (the loop.py batch path); ``deps.embedding_fn``
            is the single-query fallback.
        semaphore: Optional concurrency limiter for parallel pipeline calls.
        filter_sectors_future: Optional ``asyncio.Future`` resolving to the
            sector list emitted by the parallel ``sector_picker`` agent. When
            present it is awaited BEFORE the RPC (with the bounded grace), so the
            filter is applied inside ``search_topics`` (compliance-style).
            Resolved ``None`` means "no filter" — run unfiltered.

    Returns:
        ``(rows, result_count)``. Each ``row`` is a fetched content row
        (``chunks_v2`` / ``circulars`` / ``services``) plus routing/provenance
        keys: ``source_type``, ``_mode`` ("precise"/"simple"/"flat"), ``_rrf``
        (= RPC score), and merged RPC fields (``topic_title``, ``doc_ref``,
        ``doc_id``, ``entity_ref``, ``sectors``).
    """
    if semaphore:
        async with semaphore:
            return await _search_regulations_pipeline_inner(
                query, deps, filter_sectors, precomputed_embedding,
                filter_sectors_future,
            )
    return await _search_regulations_pipeline_inner(
        query, deps, filter_sectors, precomputed_embedding,
        filter_sectors_future,
    )


async def _search_regulations_pipeline_inner(
    query: str,
    deps: RegComplianceSearchDeps,
    filter_sectors: list[str] | None,
    precomputed_embedding: list[float] | None,
    filter_sectors_future: "asyncio.Future[list[str] | None] | None" = None,
) -> tuple[list[dict], int]:
    """Inner implementation of ``search_regulations_pipeline`` (§2 steps 1-7)."""
    events = deps._events

    try:
        topic_ev = {
            "type": "status",
            "text": f"جاري البحث في الأنظمة والتعاميم والخدمات: {query[:80]}...",
        }
        events.append(topic_ev)
        # Stream the topic line LIVE. Only this one line is streamed — the ~50
        # other mechanics status lines stay batched (message_service's sanitizer
        # drops them anyway). On a successful emit, tag the SAME dict `streamed`
        # so the orchestrator's terminal batch flush skips it (no double-send);
        # the object still lives on `_events` for forensic dumps.
        if deps.emit_sse is not None:
            try:
                deps.emit_sse(topic_ev)
                topic_ev["streamed"] = True
            except Exception:  # pragma: no cover - defensive; sink must not break search
                pass

        # Step 1: Embed query (skip if pre-computed).
        embedding = precomputed_embedding or await deps.embedding_fn(query)

        # Step 2: Resolve the sector filter BEFORE the RPC (moved up from the old
        # post-fetch step — the sector-before-RPC pattern from the retired
        # compliance loop). The shared picker
        # future was launched concurrently with the executors, so it has been
        # running in the shadow of the embed; we grant it a bounded grace here
        # and run unfiltered if it has not resolved. ``None`` → no filter. The
        # picker is NOT cancelled — a slower executor may still consume it.
        sectors = filter_sectors
        if filter_sectors_future is not None:
            sectors = await resolve_sector_filter(
                filter_sectors_future, label=query[:60],
            )

        # Step 3: search_topics RPC across all four source types.
        events.append({
            "type": "status",
            "text": "جاري البحث في قاعدة بيانات الأنظمة والتعاميم والخدمات...",
        })
        rows = await _search_topics_rpc(deps.supabase, embedding, sectors)

        # 0 rows WITH a sector filter → retry once unfiltered (+ Arabic notice).
        if not rows and sectors:
            logger.info(
                "search_topics: 0 rows with sectors %s — retrying unfiltered",
                sectors,
            )
            events.append({
                "type": "status",
                "text": (
                    "لم تُعطِ تصفية القطاعات نتائج — "
                    "جاري البحث بدون تصفية..."
                ),
            })
            rows = await _search_topics_rpc(deps.supabase, embedding, None)

        if not rows:
            events.append({
                "type": "status",
                "text": "لم يتم العثور على نتائج مطابقة.",
            })
            return [], 0

        # Step 4: Merge — ONE pool. Sort ALL rows by score DESC, cut top-15
        # (D1). The RPC already dedups per type by doc_id server-side (D8); the
        # ``seen source_id`` guard just belt-and-suspenders that per-call
        # uniqueness. ``score`` = 1 - cosine (same scale as the old best_sim);
        # no absolute gate.
        rows.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
        seen_source_ids: set[str] = set()
        merged: list[dict] = []
        for r in rows:
            sid = r.get("source_id")
            if not sid or sid in seen_source_ids:
                continue
            seen_source_ids.add(sid)
            merged.append(r)
            if len(merged) >= TOP_K:
                break

        # Step 5: Per-type content fetch — concurrent, one shared mechanism.
        chunk_ids = [
            r["source_id"] for r in merged
            if r.get("source_type") in _CHUNK_TYPES
        ]
        circular_ids = [
            r["source_id"] for r in merged
            if r.get("source_type") == "circular"
        ]
        service_ids = [
            r["source_id"] for r in merged
            if r.get("source_type") == "service"
        ]

        chunk_map, circular_map, service_map = await asyncio.gather(
            _fetch_by_ids(deps.supabase, "chunks_v2", CHUNK_SELECT_TOPICS, chunk_ids),
            _fetch_by_ids(deps.supabase, "circulars", CIRCULAR_SELECT, circular_ids),
            _fetch_by_ids(deps.supabase, "services", SERVICE_SELECT, service_ids),
        )

        # Step 6: Band + tag. Precise for the top-5 CHUNK rows only (D12); simple
        # for the rest of the chunks; flat for circular/service. ``_rrf`` = the
        # RPC score copied directly. Every row carries source_type + merged RPC
        # provenance forward (Wave 2a's type-aware adapter reads these).
        result_rows: list[dict] = []
        chunk_rank = 0
        for r in merged:
            source_type = r.get("source_type")
            sid = r.get("source_id")
            if source_type in _CHUNK_TYPES:
                row = chunk_map.get(sid)
                if row is None:
                    continue
                chunk_rank += 1
                row["_mode"] = "precise" if chunk_rank <= PRECISE_BAND else "simple"
            elif source_type == "circular":
                row = circular_map.get(sid)
                if row is None:
                    continue
                row["_mode"] = "flat"
            elif source_type == "service":
                row = service_map.get(sid)
                if row is None:
                    continue
                row["_mode"] = "flat"
            else:
                continue

            row["source_type"] = source_type
            row["_rrf"] = float(r.get("score") or 0.0)
            # Merged RPC provenance (sectors come from the RPC row, NOT re-fetched).
            row["topic_title"] = r.get("title") or ""
            row["doc_ref"] = r.get("doc_ref") or ""
            row["doc_id"] = r.get("doc_id") or ""
            if r.get("entity_ref"):
                row["entity_ref"] = r["entity_ref"]
            row["sectors"] = r.get("sectors") or []
            result_rows.append(row)

        if not result_rows:
            events.append({
                "type": "status",
                "text": "لم يتم العثور على نتائج مطابقة.",
            })
            return [], 0

        n_chunk = sum(1 for r in result_rows if r.get("source_type") in _CHUNK_TYPES)
        n_circular = sum(1 for r in result_rows if r.get("source_type") == "circular")
        n_service = sum(1 for r in result_rows if r.get("source_type") == "service")
        logger.info(
            "Topic search '%s': %d rows (%d chunk / %d circular / %d service)",
            query[:60], len(result_rows), n_chunk, n_circular, n_service,
        )
        events.append({
            "type": "status",
            "text": (
                f"تم استرجاع {len(result_rows)} نتيجة من "
                f"الأنظمة والتعاميم والخدمات."
            ),
        })

        # Step 7: Return — same contract as before (list[dict], count).
        return result_rows, len(result_rows)

    except Exception as e:
        logger.error(
            "Topic search failed for '%s': %s", query[:80], e,
            exc_info=True,
        )
        events.append({
            "type": "status",
            "text": "حدث خطأ أثناء البحث في الأنظمة والتعاميم والخدمات.",
        })
        return [], 0


# -- Supabase helpers (all wrapped in asyncio.to_thread) ----------------------


async def _search_topics_rpc(
    supabase: Any,
    embedding: list[float],
    sectors: list[str] | None,
) -> list[dict]:
    """Call the unified ``search_topics`` RPC across all four source types.

    ``p_per_type`` = :data:`PER_TYPE` (per-type quota); ``p_types`` is omitted so
    the RPC default (NULL = all four types) applies. ``p_sectors`` is passed ONLY
    when ``sectors`` is a non-empty list — an empty list must never reach the RPC
    (``sectors && '{}'`` is always false → zero rows). Rows come back ordered by
    ``score`` DESC, already deduped per type by ``doc_id`` server-side.
    """
    def _call() -> list[dict]:
        params: dict = {
            "p_query_embedding": embedding,
            "p_per_type": PER_TYPE,
        }
        if sectors:
            params["p_sectors"] = sectors
        result = supabase.rpc("search_topics", params).execute()
        return result.data or []

    try:
        return await asyncio.to_thread(_call)
    except Exception as e:
        logger.error("search_topics RPC failed: %s", e, exc_info=True)
        raise


async def _fetch_by_ids(
    supabase: Any,
    table: str,
    columns: str,
    ids: list[str],
) -> dict[str, dict]:
    """Batched ``in_("id", …)`` fetch of ``columns`` from ``table``.

    One shared mechanism for all three content tables — the per-type column
    lists are payload trims, not per-type logic. Returns a ``{id: row}`` map;
    ids absent from the table simply do not appear.
    """
    if not ids:
        return {}

    def _call(batch: list[str]) -> list[dict]:
        result = (
            supabase.table(table)
            .select(columns)
            .in_("id", batch)
            .execute()
        )
        return result.data or []

    out: dict[str, dict] = {}
    for i in range(0, len(ids), ID_BATCH):
        batch = ids[i:i + ID_BATCH]
        try:
            rows = await asyncio.to_thread(_call, batch)
        except Exception as e:
            logger.error("%s in_ fetch failed: %s", table, e, exc_info=True)
            raise
        for row in rows:
            rid = row.get("id")
            if rid:
                out[rid] = row
    return out
