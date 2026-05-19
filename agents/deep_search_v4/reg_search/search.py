"""Search pipeline for the reg_search package (v2 chunk corpus).

Semantic-only chunk retrieval over the v2 corpus:
- ``search_regulations_pipeline`` — embed -> ``search_chunk_titles`` RPC ->
  dedup title rows by chunk_id -> select top-15 by best_sim -> fetch
  ``chunks_v2`` rows -> optional sector filter -> rank-band into _mode -> return.

Replaces the legacy 3-tier (articles/sections/regulations) hybrid + Jina
rerank + markdown-assembly pipeline. There is no BM25 hybrid lane and no
absolute score gate — the corpus is a single retrieval unit and selection is
a top-15 relative cut (see ``planning/REG_SEARCH_V2_REFRAME.md`` §5 and
``SUPABASE_V2_STATUS.md`` §9/§10).

The Supabase client is sync; this module is async — every Supabase call is
wrapped in ``asyncio.to_thread`` so it never blocks the event loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .unfold_reranker import CHUNK_SELECT

if TYPE_CHECKING:
    from .models import RegSearchDeps

logger = logging.getLogger(__name__)

# Semantic search knobs — locked at 150/150 (REG_SEARCH_V2_REFRAME §5 / §7.2).
# The RPC default ef_search is 80 and silently truncates the candidate pool,
# so BOTH match_count AND ef_search are passed as explicit named args.
MATCH_COUNT = 150
EF_SEARCH = 150

# Top-N chunks kept after dedup + best_sim ranking (REG_SEARCH_V2_REFRAME §5.4).
TOP_K = 15

# Rank-band boundary: ranks 1-5 -> "precise", 6-15 -> "simple".
PRECISE_BAND = 5

# Batch size for the `in_` chunk fetch.
ID_BATCH = 180


# -- Regulations pipeline -----------------------------------------------------


async def search_regulations_pipeline(
    query: str,
    deps: RegSearchDeps,
    filter_sectors: list[str] | None = None,
    precomputed_embedding: list[float] | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[list[dict], int]:
    """Search the v2 chunk corpus for one sub-query.

    Args:
        query: Arabic search query.
        deps: RegSearchDeps with ``supabase`` and ``embedding_fn``.
        filter_sectors: Optional list of sector names to narrow scope. If the
            filter empties the result set, it is dropped and the unfiltered
            set is used instead.
        precomputed_embedding: Pre-computed 1024-dim embedding. Skips the embed
            step when provided (the loop.py batch path); ``deps.embedding_fn``
            is the single-query fallback.
        semaphore: Optional concurrency limiter for parallel pipeline calls.

    Returns:
        ``(chunk_rows, result_count)``. Each ``chunk_row`` is a ``chunks_v2``
        row (``CHUNK_SELECT`` columns) plus two extra keys: ``_mode``
        ("precise" for rank-band 1-5, "simple" for 6-15) and ``_rrf``
        (float, = best_sim).
    """
    if semaphore:
        async with semaphore:
            return await _search_regulations_pipeline_inner(
                query, deps, filter_sectors, precomputed_embedding,
            )
    return await _search_regulations_pipeline_inner(
        query, deps, filter_sectors, precomputed_embedding,
    )


async def _search_regulations_pipeline_inner(
    query: str,
    deps: RegSearchDeps,
    filter_sectors: list[str] | None,
    precomputed_embedding: list[float] | None,
) -> tuple[list[dict], int]:
    """Inner implementation of ``search_regulations_pipeline`` (§5 steps 1-8)."""
    events = deps._events

    try:
        events.append({
            "type": "status",
            "text": f"جاري البحث في الأنظمة واللوائح: {query[:80]}...",
        })

        # Step 1: Embed query (skip if pre-computed).
        embedding = precomputed_embedding or await deps.embedding_fn(query)

        # Step 2: Semantic search — search_chunk_titles RPC.
        events.append({
            "type": "status",
            "text": "جاري البحث في قاعدة بيانات الأنظمة...",
        })
        title_rows = await _search_chunk_titles(deps.supabase, embedding)

        # Step 3: Dedup title rows by chunk_id, keep best_sim = 1 - min(distance).
        best_sim: dict[str, float] = {}
        for row in title_rows:
            cid = row.get("chunk_id")
            if not cid:
                continue
            sim = 1.0 - float(row.get("distance", 1.0))
            if cid not in best_sim or sim > best_sim[cid]:
                best_sim[cid] = sim

        if not best_sim:
            events.append({
                "type": "status",
                "text": "لم يتم العثور على أنظمة مطابقة.",
            })
            return [], 0

        # Step 4: Select top-15 by best_sim (no absolute gate — SUPABASE §10).
        ranked = sorted(best_sim.items(), key=lambda kv: kv[1], reverse=True)
        top = ranked[:TOP_K]
        top_ids = [cid for cid, _ in top]

        # Step 5: Fetch chunks_v2 rows (CHUNK_SELECT) for the selected ids.
        chunk_map = await _fetch_chunks(deps.supabase, top_ids)

        # Step 6: Optional sector filter — intersect parent regulation sectors[].
        # Applied before the rank-band cut; if it empties the set, retry without.
        if filter_sectors:
            allowed = await _filter_by_sectors(
                deps.supabase, chunk_map, filter_sectors,
            )
            if allowed:
                if len(allowed) < len(chunk_map):
                    events.append({
                        "type": "status",
                        "text": (
                            f"تصفية القطاعات: {len(allowed)} من "
                            f"{len(chunk_map)} نتيجة ضمن النطاق."
                        ),
                    })
                chunk_map = allowed
            else:
                logger.warning(
                    "Sector filter %s emptied the result set — "
                    "retrying without filter", filter_sectors,
                )
                events.append({
                    "type": "status",
                    "text": (
                        "لم تُعطِ تصفية القطاعات نتائج — "
                        "جاري البحث بدون تصفية..."
                    ),
                })

        # Step 7: Rank-band the surviving chunks (in best_sim order), tag
        # _mode + _rrf. Ranks 1-5 -> "precise", 6-15 -> "simple".
        chunk_rows: list[dict] = []
        rank = 0
        for cid, sim in top:
            row = chunk_map.get(cid)
            if row is None:
                continue
            rank += 1
            row["_mode"] = "precise" if rank <= PRECISE_BAND else "simple"
            row["_rrf"] = sim
            chunk_rows.append(row)

        if not chunk_rows:
            events.append({
                "type": "status",
                "text": "لم يتم العثور على أنظمة مطابقة.",
            })
            return [], 0

        logger.info(
            "Reg search '%s': %d chunks (%d precise / %d simple)",
            query[:60], len(chunk_rows),
            sum(1 for r in chunk_rows if r["_mode"] == "precise"),
            sum(1 for r in chunk_rows if r["_mode"] == "simple"),
        )
        events.append({
            "type": "status",
            "text": f"تم استرجاع {len(chunk_rows)} نتيجة من الأنظمة واللوائح.",
        })

        # Step 8: Return.
        return chunk_rows, len(chunk_rows)

    except Exception as e:
        logger.error(
            "Regulation search failed for '%s': %s", query[:80], e,
            exc_info=True,
        )
        events.append({
            "type": "status",
            "text": "حدث خطأ أثناء البحث في الأنظمة.",
        })
        return [], 0


# -- Supabase helpers (all wrapped in asyncio.to_thread) ----------------------


async def _search_chunk_titles(
    supabase: Any, embedding: list[float]
) -> list[dict]:
    """Call the ``search_chunk_titles`` RPC — semantic-only title search.

    Both ``match_count`` and ``ef_search`` are passed explicitly: the RPC's
    default ``ef_search`` is 80 and silently truncates the pool.
    """
    def _call() -> list[dict]:
        result = (
            supabase.rpc(
                "search_chunk_titles",
                {
                    "query_embedding": embedding,
                    "match_count": MATCH_COUNT,
                    "ef_search": EF_SEARCH,
                },
            )
            .execute()
        )
        return result.data or []

    try:
        return await asyncio.to_thread(_call)
    except Exception as e:
        logger.error("search_chunk_titles RPC failed: %s", e, exc_info=True)
        raise


async def _fetch_chunks(
    supabase: Any, chunk_ids: list[str]
) -> dict[str, dict]:
    """Batched ``in_`` fetch of ``chunks_v2`` rows (CHUNK_SELECT columns).

    Returns a ``{chunk_id: row}`` map. Ids absent from the corpus simply do
    not appear in the map.
    """
    if not chunk_ids:
        return {}

    def _call(batch: list[str]) -> list[dict]:
        result = (
            supabase.table("chunks_v2")
            .select(CHUNK_SELECT)
            .in_("id", batch)
            .execute()
        )
        return result.data or []

    out: dict[str, dict] = {}
    for i in range(0, len(chunk_ids), ID_BATCH):
        batch = chunk_ids[i:i + ID_BATCH]
        try:
            rows = await asyncio.to_thread(_call, batch)
        except Exception as e:
            logger.error("chunks_v2 in_ fetch failed: %s", e, exc_info=True)
            raise
        for row in rows:
            rid = row.get("id")
            if rid:
                out[rid] = row
    return out


async def _filter_by_sectors(
    supabase: Any,
    chunk_map: dict[str, dict],
    filter_sectors: list[str],
) -> dict[str, dict]:
    """Keep only chunks whose parent regulation's ``sectors[]`` intersects.

    One batched ``in_`` lookup on ``regulations_v2`` for the distinct parent
    regulation ids of ``chunk_map``. A regulation with no/empty ``sectors``
    is treated as out of scope (dropped).
    """
    if not chunk_map:
        return {}

    reg_ids = sorted({
        row.get("regulation_id")
        for row in chunk_map.values()
        if row.get("regulation_id")
    })
    if not reg_ids:
        return {}

    def _call(batch: list[str]) -> list[dict]:
        result = (
            supabase.table("regulations_v2")
            .select("id, sectors")
            .in_("id", batch)
            .execute()
        )
        return result.data or []

    reg_sectors: dict[str, set[str]] = {}
    for i in range(0, len(reg_ids), ID_BATCH):
        batch = reg_ids[i:i + ID_BATCH]
        try:
            rows = await asyncio.to_thread(_call, batch)
        except Exception as e:
            logger.error(
                "regulations_v2 sector lookup failed: %s", e, exc_info=True,
            )
            raise
        for row in rows:
            rid = row.get("id")
            if rid:
                reg_sectors[rid] = set(row.get("sectors") or [])

    wanted = set(filter_sectors)
    return {
        cid: row
        for cid, row in chunk_map.items()
        if reg_sectors.get(row.get("regulation_id"), set()) & wanted
    }
