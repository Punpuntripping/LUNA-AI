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
                   (Appendix chunks ride this same path — real chunks_v2 rows.)
                   A FIFTH (chunk_tables_v2) runs ONLY under
                   ``with_tables=True``, which the live turn never passes —
                   see ``_enrich_regulations``.
    cases       -- 2 logical fetches (cases, entities), each batched by 150.
    compliance  -- 0 (the adapter already carries every field).
    circulars   -- 0 (the adapter carries the capped content + entity name).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

from agents.deep_search_v4.shared.case_summary import strip_pipeline_sections

# Repeal vocabulary -- shared with the reranker's candidate blocks so a
# repealed law is never described two different ways inside one turn.
from shared.library.reg_status import status_line

logger = logging.getLogger(__name__)


# PostgREST `in_` batch size.
_ID_BATCH = 150


def _batched(items: list[str], size: int) -> Iterator[list[str]]:
    """Yield ``items`` in chunks of ``size``."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


# -- Batched DB fetches (each runs inside asyncio.to_thread) ------------------


#: The chunk select list, live-turn shape. ``content`` is the AGENT view (the
#: prose conversion) and is what ``for_aggregator()`` projects — see D1/D2.
_CHUNK_COLUMNS = "id, regulation_id, title, summary, context, content, owns"

#: The reveal shape: ``content_display`` rides ALONGSIDE ``content``, never
#: instead of it. The fail-soft path in the viewer needs the prose, and nothing
#: may ever hand ``content_display`` to a prompt.
_CHUNK_COLUMNS_WITH_DISPLAY = _CHUNK_COLUMNS + ", content_display"


def _fetch_chunks(
    supabase, chunk_ids: list[str], *, with_display: bool = False
) -> dict[str, dict[str, Any]]:
    """`chunks_v2` rows keyed by id -- content/context/owns/regulation_id.

    ``with_display`` adds ONE column (``content_display``) and nothing else. It
    is False on the live search turn, so that turn's select list is byte-for-byte
    what it has always been. See :func:`_enrich_regulations`.
    """
    columns = _CHUNK_COLUMNS_WITH_DISPLAY if with_display else _CHUNK_COLUMNS
    out: dict[str, dict[str, Any]] = {}
    for batch in _batched(chunk_ids, _ID_BATCH):
        try:
            resp = (
                supabase.table("chunks_v2")
                .select(columns)
                .in_("id", batch)
                .execute()
            )
            for r in resp.data or []:
                out[r["id"]] = r
        except Exception as e:  # best-effort -- never crash the pipeline
            logger.warning("enrich_ura: _fetch_chunks batch failed: %s", e)
    return out


# -- chunk_tables_v2: the REVEAL-only read (plan D10) -------------------------
#
# PostgREST clamps any response to max-rows=1000, and one نظام in this corpus
# already carries 965 tables. A reveal batches whole chunks, so the clamp is
# reachable — and past it the missing rows do not error, they simply do not
# arrive, and every unmatched token becomes a DELETED table (the renderer drops
# what it cannot resolve). Page it. Mirrors
# ``library_service._chunk_tables_for_regulation``, which solved the same
# problem on the library side.
_TABLES_PAGE = 1000

#: Absolute ceiling across ONE enrichment call. A runaway ingestion must not
#: turn a source reveal into an unbounded scan; a reveal is a handful of chunks,
#: so this is orders of magnitude of headroom.
_TABLES_MAX_ROWS = 10_000


def _fetch_chunk_tables(
    supabase, chunk_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Raw ``chunk_tables_v2`` rows for these chunks, keyed by ``chunk_id``.

    ONE batched read per ``_ID_BATCH`` of chunk ids, PAGED at 1000 rows inside
    each batch. Selects ``table_ref, chunk_id, table_html, table_md`` and
    nothing else: ``table_html`` alone is 29.0 MB corpus-wide and the provenance
    columns (``page``, ``resolution``, ``source_file``, ``line_start``…) are of
    no use to a renderer.

    Ordered by ``table_ref`` so the paging window is stable — an unordered
    PostgREST range is not a guaranteed partition, and a row that lands in no
    page is a table silently deleted from a statute.

    Best-effort like every other fetch here: a failure logs and returns whatever
    arrived. The consumer's fail-soft direction is what makes that safe — a
    chunk whose tables did not arrive renders its PROSE (``chunk_content``),
    which is exactly today's output, never ``content_display`` minus its tables.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for batch in _batched(chunk_ids, _ID_BATCH):
        start = 0
        while True:
            try:
                resp = (
                    supabase.table("chunk_tables_v2")
                    .select("table_ref, chunk_id, table_html, table_md")
                    .in_("chunk_id", batch)
                    .order("table_ref")
                    .range(start, start + _TABLES_PAGE - 1)
                    .execute()
                )
            except Exception as e:
                logger.warning("enrich_ura: _fetch_chunk_tables batch failed: %s", e)
                break
            page = resp.data or []
            for r in page:
                chunk_id = r.get("chunk_id")
                if chunk_id is None:
                    continue
                out.setdefault(str(chunk_id), []).append(r)
            if len(page) < _TABLES_PAGE:
                break
            start += _TABLES_PAGE
            if start >= _TABLES_MAX_ROWS:
                logger.warning(
                    "enrich_ura: chunk_tables read hit the %d-row ceiling — "
                    "some tables will render as prose",
                    _TABLES_MAX_ROWS,
                )
                break
    return out


def _fetch_regulations(supabase, regulation_ids: list[str]) -> dict[str, dict[str, Any]]:
    """`regulations_v2` rows keyed by id -- title/scope/url/doc_type/STATUS.

    ``status_class`` / ``status_raw`` carry REPEAL. They are fetched here, in
    the one batched hop the URA stage already makes per regulation, because
    this is the last point at which a kept chunk can still learn that its
    parent law was repealed -- everything downstream (the aggregator prompt
    above all) reads only what this fills in.
    """
    ids = sorted({rid for rid in regulation_ids if rid})
    out: dict[str, dict[str, Any]] = {}
    for batch in _batched(ids, _ID_BATCH):
        try:
            resp = (
                supabase.table("regulations_v2")
                .select(
                    "id, clean_title, title, scope, landing_url, pdf_url, "
                    "doc_type_raw, status_class, status_raw"
                )
                .in_("id", batch)
                .execute()
            )
            for r in resp.data or []:
                out[r["id"]] = r
        except Exception as e:
            logger.warning("enrich_ura: _fetch_regulations batch failed: %s", e)
    return out


# ``regulations_v2.doc_type_raw`` is the ingestion-time Arabic document type
# (لائحة / تنظيم / دليل / مواصفة قياسية / …, 21 values live). The corpus uses
# "غير محدد" as its not-determined sentinel; it is normalised away here so the
# UI falls back to its generic نظام chip instead of labelling a card "غير محدد".
_DOC_TYPE_UNSPECIFIED = "غير محدد"


def _doc_type_label(raw: str | None) -> str:
    """Displayable ``doc_type_raw``; ``""`` when absent or unspecified."""
    value = (raw or "").strip()
    return "" if value == _DOC_TYPE_UNSPECIFIED else value


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


_CASE_COLS = (
    "case_ref, details_url, entity_id, referenced_regulations, "
    # ~200 chars combined, fetched on EVERY path: they are the inputs
    # ``judgment_subject()`` needs to label a reference card with what the
    # ruling is about (and `court` was silently empty on the panel-rebuild
    # path, which is what made its fallback render as a bare «حكم»).
    "short_summary, court"
)

# ``cases.summary`` is ~3 KB/row. The live search path already holds it (the
# adapter carried it in as ``case_content``), so fetching it there would be pure
# waste on every turn; only the panel-rebuild path, whose shells start with
# nothing but a ``ref_id``, actually needs the column.
_CASE_COLS_WITH_SUMMARY = _CASE_COLS + ", summary"


def _fetch_cases(
    supabase, case_refs: list[str], *, with_summary: bool = False
) -> dict[str, dict[str, Any]]:
    """`cases` rows keyed by ``case_ref`` -- details_url/entity_id/citations.

    ``with_summary`` additionally selects the heavy ``summary`` column; see
    :data:`_CASE_COLS_WITH_SUMMARY` for why it is opt-in.
    """
    cols = _CASE_COLS_WITH_SUMMARY if with_summary else _CASE_COLS
    out: dict[str, dict[str, Any]] = {}
    for batch in _batched(case_refs, _ID_BATCH):
        try:
            resp = (
                supabase.table("cases")
                .select(cols)
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


async def _enrich_regulations(
    reg_results: list, supabase, *, with_tables: bool = False
) -> None:
    """Fill the heavy reg fields and resolve cross-refs (4 batched fetches).

    Mutates each ``RegURAResult`` in ``reg_results`` in place. The empty-body
    filter is applied by the caller (``enrich_ura``) after this returns.

    Args:
        with_tables: also read ``chunks_v2.content_display`` and the chunks'
            ``chunk_tables_v2`` rows, filling ``chunk_display`` /
            ``chunk_tables`` (a FIFTH batched fetch). **False on the live search
            path**, and that default is the point — see below. True from
            ``references_service._build_reg_shells`` on the source REVEAL, which
            runs on a user's click.

    ⚠ THIS FUNCTION HAS TWO CALLERS AND ONLY ONE OF THEM IS A USER CLICK.
    ``enrich_ura`` runs it on every deep_search turn; ``references_service``
    runs it when someone opens «عرض المصدر» on a single citation. The corpus
    holds **29.0 MB** of table markup and only **7.7%** of regulation citations
    ever point at a chunk that has any, so pulling it on the hot path would cost
    every search for a body almost nobody opens — and it would bloat the
    persisted retrieval artifact, which is the more expensive half. With
    ``with_tables=False`` this function issues not one extra query and selects
    not one extra column: the live turn is bit-for-bit what it was.

    Same shape, same reason, as ``_enrich_cases(..., with_summary=True)``.
    Plan: ``.claude/plans/chunk_table_rendering.md`` D10 / §4.2.
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
    chunks = await asyncio.to_thread(
        _fetch_chunks, supabase, chunk_ids, with_display=with_tables
    )

    # 1b. chunk_tables_v2 — REVEAL ONLY. Skipped entirely (no query, no import
    #     of 29 MB of markup into a persisted artifact) on the live turn.
    chunk_tables: dict[str, list[dict[str, Any]]] = {}
    if with_tables:
        chunk_tables = await asyncio.to_thread(
            _fetch_chunk_tables, supabase, chunk_ids
        )

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
            if with_tables:
                # The display fork (D1/D2). ``chunk_content`` above keeps the
                # PROSE — untouched, still what for_aggregator() projects — and
                # the user view travels beside it.
                #
                # Both are filled TOGETHER or not at all: a display body whose
                # tables did not arrive would render its tokens as nothing,
                # which does not degrade the نظام, it DELETES tables from it.
                # A chunk with no rows in chunk_tables_v2 therefore keeps
                # ``chunk_display=""``, and the viewer falls back to the prose.
                rows = chunk_tables.get(str(chunk.get("id") or "")) or []
                if rows:
                    res.chunk_display = chunk.get("content_display") or ""
                    res.chunk_tables = rows
                else:
                    res.chunk_display = ""
                    res.chunk_tables = []
        else:
            # Chunk missing -> leave defaults; empty-filter will drop it.
            res.chunk_content = ""
            res.chunk_context = ""
            res.owns = {}
            if with_tables:
                res.chunk_display = ""
                res.chunk_tables = []
            reg = {}

        res.reg_title = reg.get("clean_title") or reg.get("title") or ""
        res.reg_scope = reg.get("scope") or ""
        res.landing_url = reg.get("landing_url") or ""
        res.pdf_url = reg.get("pdf_url") or ""
        res.doc_type = _doc_type_label(reg.get("doc_type_raw"))
        # Repeal. "" for every regulation the corpus does not record as
        # repealed -- including the ``reg == {}`` case, where the chunk could
        # not be fetched at all (such a result carries no content and is
        # dropped by the empty-content filter below). A DB miss must never
        # surface as a claim about a law's validity, in either direction.
        res.reg_status = status_line(reg.get("status_class"), reg.get("status_raw"))

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


# Mirror of ``case_search/unfold_ura.MAX_REFERENCED_REGULATIONS``: the live
# search path clips a case's citations to 8 before they ever reach a URA, so the
# rebuild path below must clip to the same number or a resumed artifact would
# show more إحالات than the run that produced it (one case carries 275).
_MAX_CASE_REFS_REBUILD = 8


async def _enrich_cases(
    case_results: list, supabase, *, with_summary: bool = False
) -> None:
    """Fill case reference-view fields (2 batched fetches).

    Resolves ``details_url`` + ``entity_id`` from ``cases``, then the Arabic
    ``entity_name`` from ``entities``.

    ``short_summary`` and ``court`` are filled on EVERY path. They are ~200
    chars and they are what ``shared.seo.judgment_naming.judgment_subject()``
    reads to title a judgment reference card — the same function that cut the
    10,000 published ``/judgments`` slugs, so a card and the page its button
    opens say the identical sentence. ``court`` in particular used to be filled
    by the adapter on the live path and left EMPTY on the panel-rebuild path,
    which is why the naming fallback rendered as a bare «حكم» there.

    Args:
        with_summary: also fetch ``cases.summary`` (~3 KB/row) and use it to
            fill an EMPTY ``case_content``. **False on the live search path**,
            where the adapter already carried the summary in — refetching it
            would add ~3 KB × refs to every turn for a value we hold. True from
            ``references_service``, whose shells carry nothing but a ``ref_id``.

    ``referenced_regulations`` is filled ONLY when the shell arrives empty. On
    the live search path the adapter already carried it (clipped to 8) and
    overwriting would silently re-shape the aggregator payload; but
    ``references_service`` rebuilds case shells from ``workspace_item_references``
    with nothing but a ``ref_id``, and those shells used to reach the panel with
    no إحالات at all. This is the fill for that second path.
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

    cases = await asyncio.to_thread(
        _fetch_cases, supabase, case_refs, with_summary=with_summary
    )

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
        # Title inputs — always. ``court`` keeps whatever the adapter set when
        # the row's own column is empty (never downgrade a known court to "").
        res.short_summary = (case.get("short_summary") or "").strip()
        res.court = (case.get("court") or "").strip() or res.court
        if with_summary and not (res.case_content or "").strip():
            res.case_content = strip_pipeline_sections(
                (case.get("summary") or "").strip()
            )
        if not res.referenced_regulations:
            refs = case.get("referenced_regulations") or []
            if isinstance(refs, list):
                res.referenced_regulations = list(refs[:_MAX_CASE_REFS_REBUILD])


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
            # Includes appendix chunks (corpus="appendix"): they are real
            # chunks_v2 rows, so reg enrichment fetches them by id normally and
            # the ``corpus`` marker is left untouched.
            reg_results.append(res)
        elif domain == "cases":
            case_results.append(res)
        # compliance / circulars -> no-op. The type-aware reg_adapter already
        # carries every field (service_context / structured payload, circular
        # content). Never route these through chunks_v2 — their ids are
        # services.id / circulars.id, not chunk ids.

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
