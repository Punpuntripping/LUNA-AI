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

**Failure is typed, not swallowed** (2026-08-22). The pipeline returns a
:class:`~.models.SearchOutcome`, not a bare ``(rows, count)`` tuple. It still
never raises — ``loop.py``'s ``asyncio.gather`` has no ``return_exceptions``,
so one dead socket must not take down the whole fan-out, and per-sub-query
isolation is the whole point of the fan-out. But a failed run now SAYS it
failed (``outcome.error``) instead of being byte-identical to "the corpus has
nothing". That confusion is what produced the 2026-08-22 incident: ten
``httpx.ReadTimeout``s became ten empty result sets, the URA came back empty,
and the lawyer was told «لم أعثر على نصوص نظامية» about well-covered material
while the phase span reported ``outcome: "ok"``.

The RPC also runs behind the shared DB concurrency gate
(``agents/deep_search_v4/shared/db_gate.py``) — the per-phase
``asyncio.Semaphore`` bounds pipeline-level work per executor, but three
executors fan out at once and none of them was the database's ceiling.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from agents.deep_search_v4.sector_picker.consume import resolve_sector_filter
from agents.deep_search_v4.shared.db_gate import SearchGateTimeout, search_gate

from .models import SearchOutcome
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
) -> SearchOutcome:
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
        A :class:`~.models.SearchOutcome`. ``rows`` holds the fetched content
        rows (``chunks_v2`` / ``circulars`` / ``services``) plus routing /
        provenance keys: ``source_type``, ``_mode``
        ("precise"/"simple"/"flat"), ``_rrf`` (= RPC score), and merged RPC
        fields (``topic_title``, ``doc_ref``, ``doc_id``, ``entity_ref``,
        ``sectors``). ``max_score`` / ``top_scores`` carry the retrieval score
        head; ``error`` is non-None only when the run died in transit.

        NEVER raises. A failed run yields ``rows=[]`` exactly as before — the
        caller's ``asyncio.gather`` stays safe and one sub-query's dead socket
        cannot cost the other nine their results. The difference from the old
        contract is that the caller can now TELL.
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
) -> SearchOutcome:
    """Inner implementation of ``search_regulations_pipeline`` (§2 steps 1-7)."""
    events = deps._events
    # Accumulated across the (up to two) RPC calls this sub-query makes — the
    # sector-filtered one plus, when that returns 0 rows, the unfiltered retry.
    gate_wait_ms = 0.0

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
        rows, wait_ms = await _search_topics_rpc(deps.supabase, embedding, sectors)
        gate_wait_ms += wait_ms

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
            rows, wait_ms = await _search_topics_rpc(deps.supabase, embedding, None)
            gate_wait_ms += wait_ms

        if not rows:
            events.append({
                "type": "status",
                "text": "لم يتم العثور على نتائج مطابقة.",
            })
            # A genuine zero from a healthy RPC: no rows, no score, no error.
            # This is the ONE shape that honestly means "the corpus has
            # nothing" — and it is rare, because search_topics is k-NN with no
            # threshold and normally returns 59-60 rows however weak they are.
            return SearchOutcome(
                rows=[], count=0, gate_wait_ms=gate_wait_ms,
            )

        # Step 4: Merge — ONE pool. Sort ALL rows by score DESC, cut top-15
        # (D1). The RPC already dedups per type by doc_id server-side (D8); the
        # ``seen source_id`` guard just belt-and-suspenders that per-call
        # uniqueness. ``score`` = 1 - cosine (same scale as the old best_sim);
        # no absolute gate.
        rows.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)

        # Capture the score head HERE — this is the last point at which the
        # RPC's ``score`` exists. Two things destroy it below: the TOP_K cut,
        # and Step 5's per-type content fetch, which replaces every row dict
        # with a fresh row selected from chunks_v2 / circulars / services —
        # none of whose column lists contains ``score`` (it is computed by the
        # RPC, not stored). Step 6 copies it onto ``_rrf``, but only for rows
        # that survived BOTH the cut and the content-row lookup, so reading it
        # back from the merged rows would silently under-report.
        #
        # ``score`` = 1 - cosine. Calibration against live prod (2026-08-22):
        # random unrelated topic pairs p50 0.244 / p95 0.331 / max 0.378;
        # genuinely on-topic material 0.55-0.79. Recorded only — nothing here
        # gates on it.
        all_scores = [float(r.get("score") or 0.0) for r in rows]
        max_score = all_scores[0] if all_scores else 0.0
        top_scores = all_scores[:5]

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
            # Not a genuine zero: the RPC DID return candidates (``max_score``
            # says how good they were) — every one of them lost its content row
            # in Step 5. Carrying the score head out of here is the only way
            # that stays visible downstream.
            return SearchOutcome(
                rows=[], count=0,
                max_score=max_score, top_scores=top_scores,
                gate_wait_ms=gate_wait_ms,
            )

        n_chunk = sum(1 for r in result_rows if r.get("source_type") in _CHUNK_TYPES)
        n_circular = sum(1 for r in result_rows if r.get("source_type") == "circular")
        n_service = sum(1 for r in result_rows if r.get("source_type") == "service")
        logger.info(
            "Topic search '%s': %d rows (%d chunk / %d circular / %d service)"
            " max_score=%.3f gate_wait=%.0fms",
            query[:60], len(result_rows), n_chunk, n_circular, n_service,
            max_score, gate_wait_ms,
        )
        events.append({
            "type": "status",
            "text": (
                f"تم استرجاع {len(result_rows)} نتيجة من "
                f"الأنظمة والتعاميم والخدمات."
            ),
        })

        # Step 7: Return — same rows as before, now with the score head and an
        # explicit ``error=None`` saying the run completed.
        return SearchOutcome(
            rows=result_rows, count=len(result_rows),
            max_score=max_score, top_scores=top_scores,
            gate_wait_ms=gate_wait_ms,
        )

    except SearchGateTimeout as e:
        # The gate could not seat this sub-query within GATE_WAIT_S. Spelled
        # out as its own clause — not folded into the generic handler below —
        # because it is the one failure this change INTRODUCES, and the
        # temptation to treat "I never got a slot" as "I looked and found
        # nothing" is exactly the laundering this whole change exists to kill.
        # It is a retrieval failure: empty rows, ``error`` set, counted in
        # ``failed_queries``, span goes ``degraded``. Same path as a
        # ReadTimeout, deliberately.
        #
        # ``gate_wait_ms`` UNDER-reports on this path and cannot do otherwise:
        # ``search_gate`` raises instead of yielding, so the ~GATE_WAIT_S the
        # caller actually spent queueing is never handed to us. Read the
        # ``SearchGateTimeout`` error name as "waited the full gate budget";
        # the gate logs the real figure itself.
        logger.error(
            "Topic search gated out for '%s': %s", query[:80], e,
        )
        events.append({
            "type": "status",
            "text": "حدث خطأ أثناء البحث في الأنظمة والتعاميم والخدمات.",
        })
        return SearchOutcome(
            rows=[], count=0,
            error=type(e).__name__,
            gate_wait_ms=gate_wait_ms,
        )

    except Exception as e:
        # Fail-soft, but TYPED (2026-08-22). Still no re-raise: ``loop.py``'s
        # ``asyncio.gather`` runs without ``return_exceptions=True``, so
        # raising here would let one dead socket cancel the entire fan-out —
        # strictly worse than losing one sub-query. What changes is that the
        # caller can now distinguish this from an honest empty: ``error``
        # carries the exception TYPE (``ReadTimeout``, ``SearchGateTimeout``,
        # …), which is the field the phase span's ``degraded`` outcome is
        # derived from. Before this, ten timed-out RPCs and ten genuinely empty
        # ones were the same ``([], 0)``.
        logger.error(
            "Topic search failed for '%s': %s: %s", query[:80],
            type(e).__name__, e,
            exc_info=True,
        )
        events.append({
            "type": "status",
            "text": "حدث خطأ أثناء البحث في الأنظمة والتعاميم والخدمات.",
        })
        return SearchOutcome(
            rows=[], count=0,
            error=type(e).__name__,
            gate_wait_ms=gate_wait_ms,
        )


# -- Supabase helpers (all wrapped in asyncio.to_thread) ----------------------


async def _search_topics_rpc(
    supabase: Any,
    embedding: list[float],
    sectors: list[str] | None,
) -> tuple[list[dict], float]:
    """Call the unified ``search_topics`` RPC across all four source types.

    ``p_per_type`` = :data:`PER_TYPE` (per-type quota); ``p_types`` is omitted so
    the RPC default (NULL = all four types) applies. ``p_sectors`` is passed ONLY
    when ``sectors`` is a non-empty list — an empty list must never reach the RPC
    (``sectors && '{}'`` is always false → zero rows). Rows come back ordered by
    ``score`` DESC, already deduped per type by ``doc_id`` server-side.

    Runs inside the shared DB concurrency gate (2026-08-22). The per-phase
    ``asyncio.Semaphore(state.concurrency)`` in ``loop.py`` stays — it caps
    pipeline-level work for THIS executor — but it was never the database's
    ceiling: three executors fan out concurrently, each with its own semaphore,
    and the RPC is the expensive end (an HNSW scan across four source types).
    The gate is the one place that knows the global in-flight count. On
    2026-08-22 every one of ten concurrent ``search_topics`` calls came back
    ``httpx.ReadTimeout``.

    Returns:
        ``(rows, gate_wait_ms)`` — the RPC rows plus how long this call sat
        queued on the gate before it was let through.

    Raises:
        Whatever the transport or the gate raises (``httpx.ReadTimeout``,
        ``SearchGateTimeout``, …). The pipeline above is the single place that
        converts a raise into a typed ``SearchOutcome``; keeping this helper
        honest means the conversion happens exactly once.
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
        async with search_gate() as gate_wait_ms:
            return (await asyncio.to_thread(_call), gate_wait_ms)
    except SearchGateTimeout:
        # No traceback: the RPC never ran, and ``db_gate`` has already logged
        # the wait and the limit. A stack trace here would only suggest the
        # database misbehaved when in fact we chose not to ask it.
        raise
    except Exception as e:
        logger.error(
            "search_topics RPC failed: %s: %s", type(e).__name__, e,
            exc_info=True,
        )
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
