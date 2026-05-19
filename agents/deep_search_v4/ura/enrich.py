"""Post-merge URA enrichment stage (URA Two-View Reframe -- §P2).

The merger builds **lightweight URA result shells** (base plumbing + whatever
the reranker already knew); the heavy fields -- full chunk body, resolved
cross-references, landing URLs, regulation/entity names -- are filled here, in
one batched pass over the merge survivors.

``enrich_ura`` mutates the result instances **in place** (the URA models are
plain ``BaseModel`` by contract -- see ``schema.py``) and rebuilds
``ura.high_results`` / ``ura.medium_results`` to drop empty-body reg results.

Design constraints:
- The ``supabase`` argument is the **sync service-role** client. The anon key
  hits RLS and silently returns empty ``in_(...)`` results -- do not swap it.
- Every Supabase call runs under ``asyncio.to_thread`` (sync client, async
  context) and is wrapped in try/except -- enrichment is best-effort and must
  never crash the pipeline; a failed fetch just leaves fields at their default.
- ``in_(...)`` lookups are batched at ``_ID_BATCH = 150`` (PostgREST limit).

Public surface:
    enrich_ura(ura, supabase) -> None   -- mutates ``ura`` in place.

Per-domain query count:
    regulations -- 4 logical fetches (chunks_v2, regulations_v2,
                   cross_references_v2, articles_v2), each batched by 150.
    cases       -- 2 logical fetches (cases, entities), each batched by 150.
    compliance  -- 0 (the compliance adapter already carries every field).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)


# PostgREST `in_` batch size.
_ID_BATCH = 150


def _batched(items: list[str], size: int) -> Iterator[list[str]]:
    """Yield ``items`` in chunks of ``size``."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


# -- Batched DB fetches (each runs inside asyncio.to_thread) ------------------


def _fetch_chunks(supabase, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    """`chunks_v2` rows keyed by id -- content/context/owns/regulation_id."""
    out: dict[str, dict[str, Any]] = {}
    for batch in _batched(chunk_ids, _ID_BATCH):
        try:
            resp = (
                supabase.table("chunks_v2")
                .select("id, regulation_id, title, summary, context, content, owns")
                .in_("id", batch)
                .execute()
            )
            for r in resp.data or []:
                out[r["id"]] = r
        except Exception as e:  # best-effort -- never crash the pipeline
            logger.warning("enrich_ura: _fetch_chunks batch failed: %s", e)
    return out


def _fetch_regulations(supabase, regulation_ids: list[str]) -> dict[str, dict[str, Any]]:
    """`regulations_v2` rows keyed by id -- title/scope/landing_url/pdf_url."""
    ids = sorted({rid for rid in regulation_ids if rid})
    out: dict[str, dict[str, Any]] = {}
    for batch in _batched(ids, _ID_BATCH):
        try:
            resp = (
                supabase.table("regulations_v2")
                .select("id, clean_title, title, scope, landing_url, pdf_url")
                .in_("id", batch)
                .execute()
            )
            for r in resp.data or []:
                out[r["id"]] = r
        except Exception as e:
            logger.warning("enrich_ura: _fetch_regulations batch failed: %s", e)
    return out


def _fetch_cross_refs(supabase, chunk_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """`cross_references_v2` rows for reg chunks, grouped by ``source_id``.

    Filters ``source_type = 'reg_chunk'`` and ``source_id IN (chunk ids)`` --
    a reg chunk's ``source_id`` equals ``chunks_v2.id``.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for batch in _batched(chunk_ids, _ID_BATCH):
        try:
            resp = (
                supabase.table("cross_references_v2")
                .select(
                    "source_id, relation, target_type, target_number, "
                    "target_id, target_ref, target_reg_title"
                )
                .eq("source_type", "reg_chunk")
                .in_("source_id", batch)
                .execute()
            )
            for r in resp.data or []:
                out.setdefault(r["source_id"], []).append(r)
        except Exception as e:
            logger.warning("enrich_ura: _fetch_cross_refs batch failed: %s", e)
    return out


def _fetch_articles(supabase, article_ids: list[str]) -> dict[str, str]:
    """`articles_v2` content keyed by id -- the ``madda`` cross-ref body."""
    ids = sorted({aid for aid in article_ids if aid})
    out: dict[str, str] = {}
    for batch in _batched(ids, _ID_BATCH):
        try:
            resp = (
                supabase.table("articles_v2")
                .select("id, content")
                .in_("id", batch)
                .execute()
            )
            for r in resp.data or []:
                out[r["id"]] = r.get("content") or ""
        except Exception as e:
            logger.warning("enrich_ura: _fetch_articles batch failed: %s", e)
    return out


def _fetch_appendices(supabase, appendix_ids: list[str]) -> dict[str, str]:
    """Stub handler for ``appendix`` cross-refs.

    ``appendices_v2`` does not exist yet (URA reframe §7.2 -- separate
    migration). Until it lands the handler resolves nothing; appendix
    cross-refs fall through to the title-only fallback (body stays ``""``).
    """
    return {}


def _fetch_cases(supabase, case_refs: list[str]) -> dict[str, dict[str, Any]]:
    """`cases` rows keyed by ``case_ref`` -- details_url/entity_id."""
    out: dict[str, dict[str, Any]] = {}
    for batch in _batched(case_refs, _ID_BATCH):
        try:
            resp = (
                supabase.table("cases")
                .select("case_ref, details_url, entity_id")
                .in_("case_ref", batch)
                .execute()
            )
            for r in resp.data or []:
                out[r["case_ref"]] = r
        except Exception as e:
            logger.warning("enrich_ura: _fetch_cases batch failed: %s", e)
    return out


def _fetch_entities(supabase, entity_ids: list[str]) -> dict[str, str]:
    """`entities` -- ``entity_name`` (Arabic) keyed by id."""
    ids = sorted({eid for eid in entity_ids if eid})
    out: dict[str, str] = {}
    for batch in _batched(ids, _ID_BATCH):
        try:
            resp = (
                supabase.table("entities")
                .select("id, entity_name")
                .in_("id", batch)
                .execute()
            )
            for r in resp.data or []:
                out[r["id"]] = r.get("entity_name") or ""
        except Exception as e:
            logger.warning("enrich_ura: _fetch_entities batch failed: %s", e)
    return out


# -- Per-domain enrichment ----------------------------------------------------


async def _enrich_regulations(reg_results: list, supabase) -> None:
    """Fill the heavy reg fields and resolve cross-refs (4 batched fetches).

    Mutates each ``RegURAResult`` in ``reg_results`` in place. The empty-body
    filter is applied by the caller (``enrich_ura``) after this returns.
    """
    if not reg_results:
        return

    # ref_id is "reg:<uuid>" -- strip the prefix to recover chunks_v2.id.
    chunk_id_by_result: dict[int, str] = {}
    for res in reg_results:
        ref_id = res.ref_id or ""
        chunk_id = ref_id[4:] if ref_id.startswith("reg:") else ref_id
        if chunk_id:
            chunk_id_by_result[id(res)] = chunk_id

    chunk_ids = sorted(set(chunk_id_by_result.values()))
    if not chunk_ids:
        return

    # 1. chunks_v2 by id.
    chunks = await asyncio.to_thread(_fetch_chunks, supabase, chunk_ids)

    # 2. regulations_v2 by the chunks' regulation_id.
    regulation_ids = [c.get("regulation_id") for c in chunks.values()]
    regs = await asyncio.to_thread(_fetch_regulations, supabase, regulation_ids)

    # 3. cross_references_v2 WHERE source_type='reg_chunk' AND source_id IN (..).
    cross_refs = await asyncio.to_thread(_fetch_cross_refs, supabase, chunk_ids)

    # 4. Resolve cross-ref bodies -- union of all target_ids across all chunks,
    #    dispatched on target_type. Single batched fetch per resolution table.
    madda_ids: set[str] = set()
    appendix_ids: set[str] = set()
    for rows in cross_refs.values():
        for r in rows:
            tid = r.get("target_id")
            if not tid:
                continue
            ttype = r.get("target_type") or ""
            if ttype == "madda":
                madda_ids.add(tid)
            elif ttype == "appendix":
                appendix_ids.add(tid)
            # unknown target_type -> no body resolution (title-only fallback)

    article_bodies = await asyncio.to_thread(
        _fetch_articles, supabase, sorted(madda_ids)
    )
    appendix_bodies = await asyncio.to_thread(
        _fetch_appendices, supabase, sorted(appendix_ids)
    )

    # Local import -- keep module import-time light and avoid cycle risk.
    from agents.deep_search_v4.ura.schema import CrossRef

    def _resolve_body(target_type: str, target_id: str | None) -> str:
        if not target_id:
            return ""
        if target_type == "madda":
            return article_bodies.get(target_id, "")
        if target_type == "appendix":
            return appendix_bodies.get(target_id, "")
        return ""  # unknown / future type -> title-only fallback

    # -- Mutate each reg result in place --------------------------------------
    for res in reg_results:
        chunk_id = chunk_id_by_result.get(id(res))
        chunk = chunks.get(chunk_id) if chunk_id else None

        if chunk:
            res.chunk_content = chunk.get("content") or ""
            res.chunk_context = chunk.get("context") or ""
            owns = chunk.get("owns")
            res.owns = owns if isinstance(owns, dict) else {}
            reg = regs.get(chunk.get("regulation_id")) or {}
        else:
            # Chunk missing -> leave defaults; empty-filter will drop it.
            res.chunk_content = ""
            res.chunk_context = ""
            res.owns = {}
            reg = {}

        res.reg_title = reg.get("clean_title") or reg.get("title") or ""
        res.reg_scope = reg.get("scope") or ""
        res.landing_url = reg.get("landing_url") or ""
        res.pdf_url = reg.get("pdf_url") or ""

        # Build deduped cross-refs for this chunk (dedup by target_id).
        rows = cross_refs.get(chunk_id, []) if chunk_id else []
        seen_targets: set[str] = set()
        refs: list[CrossRef] = []
        for r in rows:
            target_id = r.get("target_id")
            if target_id is not None:
                if target_id in seen_targets:
                    continue
                seen_targets.add(target_id)
            target_type = r.get("target_type") or ""
            refs.append(
                CrossRef(
                    target_type=target_type,
                    target_reg_title=r.get("target_reg_title") or "",
                    target_number=r.get("target_number"),
                    relation=r.get("relation") or "",
                    content=_resolve_body(target_type, target_id),
                )
            )
        # Assign a fresh list -- never append onto the default. Caps apply at
        # projection time (for_aggregator / for_reference), not here.
        res.cross_refs = refs


async def _enrich_cases(case_results: list, supabase) -> None:
    """Fill case reference-view fields (2 batched fetches).

    Resolves ``details_url`` + ``entity_id`` from ``cases``, then the Arabic
    ``entity_name`` from ``entities``. Case content / ``referenced_regulations``
    already arrive via the adapter -- not refetched here.
    """
    if not case_results:
        return

    # ref_id is "case:<case_ref>" -- strip the prefix to recover case_ref.
    case_ref_by_result: dict[int, str] = {}
    for res in case_results:
        ref_id = res.ref_id or ""
        case_ref = ref_id[5:] if ref_id.startswith("case:") else ref_id
        if case_ref:
            case_ref_by_result[id(res)] = case_ref

    case_refs = sorted(set(case_ref_by_result.values()))
    if not case_refs:
        return

    cases = await asyncio.to_thread(_fetch_cases, supabase, case_refs)

    entity_ids = [c.get("entity_id") for c in cases.values()]
    entities = await asyncio.to_thread(_fetch_entities, supabase, entity_ids)

    for res in case_results:
        case_ref = case_ref_by_result.get(id(res))
        case = cases.get(case_ref) if case_ref else None
        if not case:
            continue
        res.details_url = case.get("details_url")
        entity_id = case.get("entity_id")
        res.entity_id = entity_id
        if entity_id:
            res.entity_name = entities.get(entity_id, "")


# -- Public entry point -------------------------------------------------------


async def enrich_ura(ura, supabase) -> None:
    """Enrich a merged URA in place with heavy fields fetched from Supabase.

    Runs after ``merger.build_ura_from_phases`` -- the merger ships lightweight
    shells, this stage batch-fetches the full bodies, cross-references, landing
    URLs and entity names for every merge survivor.

    Args:
        ura: A ``UnifiedRetrievalArtifact``. ``high_results`` / ``medium_results``
            hold the kept, tiered results. Mutated in place; reg results with an
            empty ``chunk_content`` are dropped from those two lists.
        supabase: The **sync service-role** Supabase client. Do not pass the
            anon client -- RLS would silently empty every ``in_(...)`` query.

    Returns:
        None -- ``ura`` is mutated in place. Best-effort: a failed DB fetch
        leaves the affected fields at their defaults rather than raising.
    """
    if ura is None:
        return

    kept = list(ura.high_results) + list(ura.medium_results)
    if not kept:
        return

    # Split kept results by domain.
    reg_results: list = []
    case_results: list = []
    for res in kept:
        domain = getattr(res, "domain", None)
        if domain == "regulations":
            reg_results.append(res)
        elif domain == "cases":
            case_results.append(res)
        # compliance -> no-op (the adapter already carries every field)

    # Enrich each domain (best-effort -- a failure in one must not block the
    # other, and must not crash the pipeline).
    try:
        await _enrich_regulations(reg_results, supabase)
    except Exception as e:
        logger.warning("enrich_ura: regulation enrichment failed: %s", e)
    try:
        await _enrich_cases(case_results, supabase)
    except Exception as e:
        logger.warning("enrich_ura: case enrichment failed: %s", e)

    # -- Empty-filter: drop reg results with a blank chunk_content ------------
    # Rebuild high_results / medium_results in place.
    def _drop_empty_regs(results: list) -> list:
        kept_out: list = []
        for res in results:
            if getattr(res, "domain", None) == "regulations":
                content = getattr(res, "chunk_content", "") or ""
                if not content.strip():
                    logger.warning(
                        "enrich_ura: dropping empty-body reg result %s",
                        getattr(res, "ref_id", "?"),
                    )
                    continue
            kept_out.append(res)
        return kept_out

    ura.high_results = _drop_empty_regs(ura.high_results)
    ura.medium_results = _drop_empty_regs(ura.medium_results)


__all__ = ["enrich_ura"]
