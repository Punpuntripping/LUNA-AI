"""Read- and write-path service for ``workspace_item_references``.

Replaces the JSONB blob ``workspace_items.metadata.references`` (migration
049). The table stores per-WI ref state — ``(wi_id, item_id, ref_id,
domain, n, relevance, used, sub_queries)`` — and the full display payload
(``Reference`` pydantic shape) is reconstructed on read by joining to the
existing source tables via the URA-enrichment helpers and ``source_viewer``.

Migration 050: two-key design.

- ``item_id`` (UUID, nullable) — source row PK. ``chunks_v2.id``,
  ``cases.id``, or ``services.id``. The preferred join key for cross-WI
  queries ("which WIs cite this chunk?").
- ``ref_id`` (TEXT, always set) — the URA-emitted identifier
  (``reg:<uuid>`` | ``case:<case_ref>`` | ``compliance:<sha1[:16]>``).
  The durable fallback when item_id failed to resolve, and the
  forensic-traceability key into ``retrieval_artifacts``.

Public surface:
    fetch_item_references(supabase, wi_id, *, used_only=False) -> list[Reference]
    persist_item_references(supabase, wi_id, references, ura_results,
                            cited_numbers, ref_to_sub_queries) -> int

The read path explicitly reuses ``for_reference()`` /
``preprocessor._reference_from_ura()`` / ``preprocessor.build_snippet`` /
``source_viewer.build_source_view`` so the output is byte-for-byte identical
to what the publisher used to bake into JSONB.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

from supabase import Client as SupabaseClient

from agents.deep_search_v4.aggregator.models import Reference
from agents.deep_search_v4.aggregator.preprocessor import (
    _reference_from_ura,
    build_snippet,
    render_aggregator_content,
)
from agents.deep_search_v4.source_viewer import build_source_view
from agents.deep_search_v4.ura.enrich import (
    _enrich_cases,
    _enrich_regulations,
)
from agents.deep_search_v4.ura.schema import (
    CaseURAResult,
    ComplianceURAResult,
    RegURAResult,
    URAResultBase,
)

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_item_references",
    "persist_item_references",
]

# PostgREST `in_` batch size — matches enrich.py.
_ID_BATCH = 150

# Fallback stub label when the source row is gone / unresolvable.
_STUB_TITLE = "[المصدر غير متوفر]"


# ---------------------------------------------------------------------------
# READ PATH
# ---------------------------------------------------------------------------


async def fetch_item_references(
    supabase: SupabaseClient,
    wi_id: str,
    *,
    used_only: bool = False,
) -> list[Reference]:
    """Reconstruct ``list[Reference]`` for one workspace_item.

    Reads rows from ``workspace_item_references`` filtered by ``wi_id`` (and
    ``used`` when ``used_only=True``), groups by domain, batch-fetches the
    source rows, builds URA result shells, and runs them through the exact
    same projection pipeline the aggregator uses at publish time. Output
    matches the pre-migration ``metadata.references`` JSONB byte-for-byte.

    Returns:
        References ordered by ``n`` (ascending). Empty list if no rows.
    """
    rows = await asyncio.to_thread(
        _select_reference_rows, supabase, wi_id, used_only
    )
    if not rows:
        return []

    # Group rows by domain so each source-table fetch can be batched.
    by_domain: dict[str, list[dict]] = {"regulations": [], "compliance": [], "cases": []}
    for row in rows:
        domain = row.get("domain")
        if domain in by_domain:
            by_domain[domain].append(row)
        else:
            logger.warning("fetch_item_references: unknown domain %r — skipping row", domain)

    # Build a URA result shell per row, keyed by ``n`` so we can pair the
    # reconstructed Reference back to its row.
    shells_by_n: dict[int, URAResultBase] = {}

    if by_domain["regulations"]:
        reg_shells = await _build_reg_shells(supabase, by_domain["regulations"])
        shells_by_n.update(reg_shells)
    if by_domain["cases"]:
        case_shells = await _build_case_shells(supabase, by_domain["cases"])
        shells_by_n.update(case_shells)
    if by_domain["compliance"]:
        compliance_shells = await _build_compliance_shells(
            supabase, by_domain["compliance"]
        )
        shells_by_n.update(compliance_shells)

    # Walk rows in order, build Reference per shell, attach source_view in
    # parallel (mirrors aggregator preprocessor.attach_source_views).
    ordered_rows = sorted(rows, key=lambda r: int(r["n"]))
    references: list[Reference] = []
    pending_views: list[tuple[Reference, URAResultBase | None]] = []

    for row in ordered_rows:
        n = int(row["n"])
        shell = shells_by_n.get(n)
        if shell is None:
            # Source row missing / unresolvable -> emit a stub Reference so
            # the panel still has a card for [n]. Mirrors what the existing
            # ReferencePanel does when a Reference has empty fields (hides
            # the buttons gracefully).
            references.append(_stub_reference(row))
            continue

        ref = _reference_from_ura(n, shell)
        # Snippet derives from the aggregator-view content (same call the
        # aggregator preprocessor makes at publish time).
        ref.snippet = build_snippet(shell)
        references.append(ref)
        pending_views.append((ref, shell))

    # Resolve source_view payloads in parallel. Each lookup is wrapped so
    # one DB failure can't sink the whole panel.
    if pending_views:
        await _attach_source_views(supabase, pending_views)

    return references


def _select_reference_rows(
    supabase: SupabaseClient,
    wi_id: str,
    used_only: bool,
) -> list[dict]:
    """Sync Supabase read — runs under ``asyncio.to_thread``.

    Returns the full per-WI ref state. After migration 050, ``item_id`` is
    a UUID (nullable) and ``ref_id`` is the always-present URA-emitted text
    identifier. The build_* helpers prefer item_id when set and fall back
    to ref_id parsing for the source-table join.
    """
    try:
        q = (
            supabase.table("workspace_item_references")
            .select(
                "ref_pk, wi_id, item_id, ref_id, domain, n, relevance, used, sub_queries"
            )
            .eq("wi_id", wi_id)
            .order("n", desc=False)
        )
        if used_only:
            q = q.eq("used", True)
        resp = q.execute()
        return list(resp.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.exception("references_service: select rows failed for wi_id=%s: %s", wi_id, exc)
        return []


def _reg_chunk_id_from_row(row: dict) -> str:
    """Return the chunks_v2.id (uuid as text) for a regulations row.

    Prefers ``item_id`` (the migration-050 UUID column). Falls back to
    stripping the ``reg:`` prefix off ``ref_id`` so legacy rows whose
    item_id failed to resolve at backfill time still render.
    """
    item_id = row.get("item_id")
    if item_id:
        return str(item_id)
    ref_id = (row.get("ref_id") or "").strip()
    if ref_id.startswith("reg:"):
        return ref_id[4:]
    return ""


def _case_ref_from_row(row: dict) -> str:
    """Return the cases.case_ref (text) for a cases row.

    Always parsed from ``ref_id`` because ``_fetch_cases`` (from enrich.py)
    is keyed by case_ref, not by cases.id. item_id (UUID) is stored on the
    row for cross-WI / forensic queries but isn't used by the enrich path.
    """
    ref_id = (row.get("ref_id") or "").strip()
    if ref_id.startswith("case:"):
        return ref_id[5:]
    return ""


async def _build_reg_shells(
    supabase: SupabaseClient,
    rows: list[dict],
) -> dict[int, RegURAResult]:
    """Build RegURAResult shells for every regulations row and enrich in bulk.

    Reuses ``ura.enrich._enrich_regulations`` which already batches every
    fetch (chunks_v2, regulations_v2, cross_references_v2, articles_v2,
    appendices placeholder) and mutates the shells in place.
    """
    shells_by_n: dict[int, RegURAResult] = {}
    shells: list[RegURAResult] = []
    for row in rows:
        chunk_id = _reg_chunk_id_from_row(row)
        if not chunk_id:
            continue
        # Re-mint the URA ``ref_id`` so the enrichment code (which strips
        # ``reg:``) recovers the chunk_id correctly.
        shell = RegURAResult(
            ref_id=f"reg:{chunk_id}",
            source_type="reg_chunk",
            relevance=row.get("relevance", "medium"),
        )
        shells.append(shell)
        shells_by_n[int(row["n"])] = shell

    try:
        await _enrich_regulations(shells, supabase)
    except Exception as exc:  # noqa: BLE001
        logger.warning("references_service: reg enrichment failed: %s", exc)

    # Drop shells whose chunk lookup came back empty (chunk got re-chunked /
    # deleted) — they would render misleading empty cards. Map them to None
    # so the caller falls back to a stub.
    pruned: dict[int, RegURAResult] = {}
    for n, shell in shells_by_n.items():
        if (shell.chunk_content or "").strip() or (shell.reg_title or "").strip():
            pruned[n] = shell
    return pruned


async def _build_case_shells(
    supabase: SupabaseClient,
    rows: list[dict],
) -> dict[int, CaseURAResult]:
    """Build CaseURAResult shells and enrich (cases + entities).

    Uses ``ref_id`` to recover ``case_ref`` (the URA-level handle that
    ``enrich._fetch_cases`` queries by). ``item_id`` (cases.id UUID) is
    persisted on the row for cross-WI joins but isn't used here.
    """
    shells_by_n: dict[int, CaseURAResult] = {}
    shells: list[CaseURAResult] = []
    for row in rows:
        case_ref = _case_ref_from_row(row)
        if not case_ref:
            continue
        shell = CaseURAResult(
            ref_id=f"case:{case_ref}",
            source_type="case",
            relevance=row.get("relevance", "medium"),
        )
        shells.append(shell)
        shells_by_n[int(row["n"])] = shell

    try:
        await _enrich_cases(shells, supabase)
    except Exception as exc:  # noqa: BLE001
        logger.warning("references_service: case enrichment failed: %s", exc)

    # Cases that came back without details_url or entity_name are still
    # citable — case_number alone is enough for a card title. Keep them.
    return shells_by_n


async def _build_compliance_shells(
    supabase: SupabaseClient,
    rows: list[dict],
) -> dict[int, ComplianceURAResult]:
    """Build ComplianceURAResult shells from services table rows.

    Migration 050: prefers ``item_id`` (services.id UUID) for the join.
    Ref_id alone is insufficient for compliance because it carries only a
    16-char sha1 hash, not the service_ref. Rows whose item_id failed to
    resolve at write time fall through to a stub card via ``shells_by_n``
    omission.
    """
    rows_by_id: dict[str, list[dict]] = {}
    for row in rows:
        service_id = row.get("item_id")
        if not service_id:
            continue
        rows_by_id.setdefault(str(service_id), []).append(row)

    if not rows_by_id:
        return {}

    services = await asyncio.to_thread(
        _fetch_services_by_id, supabase, list(rows_by_id.keys())
    )

    shells_by_n: dict[int, ComplianceURAResult] = {}
    for service_id, related_rows in rows_by_id.items():
        svc = services.get(service_id)
        service_ref = (svc or {}).get("service_ref") or ""
        for row in related_rows:
            n = int(row["n"])
            shell = ComplianceURAResult(
                # Mint the URA-style ref_id from the recovered service_ref so
                # downstream code that re-parses it keeps working. Prefer the
                # row's own ref_id when service_ref isn't recoverable.
                ref_id=(
                    f"compliance:{_compliance_hash(service_ref)}"
                    if service_ref
                    else (row.get("ref_id") or "")
                ),
                source_type="gov_service",
                relevance=row.get("relevance", "medium"),
                service_ref=service_ref,
                service_name=(svc or {}).get("service_name_ar") or "",
                service_context=(svc or {}).get("service_context") or "",
                provider_name=(svc or {}).get("provider_name") or "",
                service_url=(svc or {}).get("service_url") or "",
                url=(svc or {}).get("url") or "",
            )
            shells_by_n[n] = shell

    return shells_by_n


def _fetch_services_by_id(
    supabase: SupabaseClient,
    service_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Batched ``services`` fetch keyed by services.id UUID."""
    out: dict[str, dict[str, Any]] = {}
    ids = sorted({sid for sid in service_ids if sid})
    for i in range(0, len(ids), _ID_BATCH):
        batch = ids[i:i + _ID_BATCH]
        try:
            resp = (
                supabase.table("services")
                .select(
                    "id, service_ref, service_name_ar, provider_name, "
                    "service_context, service_url, url"
                )
                .in_("id", batch)
                .execute()
            )
            for r in resp.data or []:
                rid = r.get("id")
                if rid:
                    out[str(rid)] = r
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "references_service: _fetch_services_by_id batch failed: %s", exc,
            )
    return out


def _compliance_hash(service_ref: str) -> str:
    """Mirror ``ura.compliance_adapter._compliance_ref_id`` — sha1[:16].

    Only used to fabricate a plausible ``ref_id`` on the reconstructed
    ComplianceURAResult shell so downstream code that parses ``ref_id``
    keeps working. The real lookup key is ``service_ref``.
    """
    import hashlib

    if not service_ref:
        return ""
    return hashlib.sha1(service_ref.encode("utf-8")).hexdigest()[:16]


async def _attach_source_views(
    supabase: SupabaseClient,
    pending: list[tuple[Reference, URAResultBase]],
) -> None:
    """Parallel ``build_source_view`` resolution; failures leave source_view=None."""
    if not pending:
        return

    async def _one(shell: URAResultBase) -> Any:
        try:
            return await build_source_view(supabase, shell)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "references_service: build_source_view(%s) failed: %s",
                getattr(shell, "ref_id", "?"),
                exc,
            )
            return None

    views = await asyncio.gather(*(_one(shell) for _, shell in pending))
    for (ref, _), view in zip(pending, views):
        if view is not None:
            ref.source_view = view


def _stub_reference(row: dict) -> Reference:
    """Build a minimal ``Reference`` when the source row cannot be resolved.

    The frontend ``ReferencePanel`` gracefully hides buttons whose URLs are
    empty and the "عرض المصدر" button when ``source_view is None``, so a
    stub still renders as a card with just the [n] badge + title.

    Carries the row's ``ref_id`` so the stub is still forensically
    traceable (e.g. into retrieval_artifacts) even though the source row
    didn't resolve.
    """
    domain = row.get("domain") or "regulations"
    return Reference(
        n=int(row["n"]),
        source_type="regulation" if domain == "regulations" else (
            "case" if domain == "cases" else "gov_service"
        ),
        regulation_title=_STUB_TITLE,
        title=_STUB_TITLE,
        snippet="",
        relevance=row.get("relevance", "medium"),
        ref_id=row.get("ref_id", "") or "",
        domain=domain,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# WRITE PATH
# ---------------------------------------------------------------------------


def persist_item_references(
    supabase: SupabaseClient,
    wi_id: str,
    references: list[Reference],
    ura_results: Sequence[URAResultBase] | None,
    cited_numbers: Sequence[int] | None,
    ref_to_sub_queries: dict[int, list[int]] | None,
) -> int:
    """Insert one ``workspace_item_references`` row per ``Reference``.

    Called by ``agents.agent_search.publisher`` right after the workspace
    item insert succeeds.

    Migration 050: writes BOTH columns per row:
      * ``item_id`` (UUID) — source row PK. For regulations this is
        ``chunks_v2.id`` (parsed from ref_id). For cases/services this is
        ``cases.id`` / ``services.id``, resolved via batched lookups
        against ``case_ref`` / ``service_ref``. NULL when the source row
        can't be located.
      * ``ref_id`` (TEXT) — the URA-emitted identifier
        (``reg:<uuid>`` | ``case:<case_ref>`` | ``compliance:<hash>``).
        Always populated. The durable fallback when item_id can't resolve,
        plus the forensic-join key into retrieval_artifacts.

    Args:
        wi_id: The newly-created workspace_items.item_id.
        references: Final (post-filter) list from ``AggregatorOutput``.
        ura_results: Optional parallel list of URA result objects — supplies
            the ``service_ref`` for compliance refs (the ``Reference.ref_id``
            only carries a hash). When None, compliance refs fall back to
            row.ref_id alone, with item_id left NULL.
        cited_numbers: From the postvalidator (``extract_cited_numbers``).
            Drives the ``used`` column.
        ref_to_sub_queries: From ``preprocess_references`` — maps
            ``Reference.n -> [sub_query_index, ...]``.

    Returns:
        Number of rows inserted.
    """
    if not references:
        return 0

    cited_set = set(cited_numbers or [])
    sq_map = ref_to_sub_queries or {}

    # Compliance refs: extract service_ref from the URA results so we can
    # batch-look-up services.id at write time. Also build a parallel map
    # ura_ref_id -> URA result so we can compute the aggregator-view word
    # count for every ref from the same text the LLM grounded against.
    service_ref_by_ura_ref_id: dict[str, str] = {}
    ura_by_ref_id: dict[str, URAResultBase] = {}
    if ura_results is not None:
        for ura_result in ura_results:
            ura_ref_id = getattr(ura_result, "ref_id", "") or ""
            if not ura_ref_id:
                continue
            ura_by_ref_id[ura_ref_id] = ura_result
            if isinstance(ura_result, ComplianceURAResult):
                sref = (ura_result.service_ref or "").strip()
                if sref:
                    service_ref_by_ura_ref_id[ura_ref_id] = sref

    # Phase 1: collect lookup batches for cases (by case_ref) and services
    # (by service_ref) so we can resolve their UUID PKs in two round-trips
    # rather than one per ref.
    case_refs_needed: set[str] = set()
    service_refs_needed: set[str] = set()
    for ref in references:
        if ref.domain == "cases" and ref.ref_id.startswith("case:"):
            case_refs_needed.add(ref.ref_id[5:])
        elif ref.domain == "compliance":
            sref = service_ref_by_ura_ref_id.get(ref.ref_id, "")
            if sref:
                service_refs_needed.add(sref)

    case_id_by_ref = (
        _fetch_case_ids(supabase, list(case_refs_needed)) if case_refs_needed else {}
    )
    service_id_by_ref = (
        _fetch_service_ids(supabase, list(service_refs_needed))
        if service_refs_needed
        else {}
    )

    payloads: list[dict] = []
    for ref in references:
        if not ref.ref_id:
            logger.warning(
                "persist_item_references: skipping ref n=%d — empty ref_id",
                ref.n,
            )
            continue

        item_uuid: str | None = None
        if ref.domain == "regulations":
            # ref_id = "reg:<uuid>" — strip prefix, validate as uuid.
            candidate = (
                ref.ref_id[4:] if ref.ref_id.startswith("reg:") else ref.ref_id
            )
            item_uuid = candidate if _looks_like_uuid(candidate) else None
        elif ref.domain == "cases":
            case_ref = (
                ref.ref_id[5:] if ref.ref_id.startswith("case:") else ""
            )
            item_uuid = case_id_by_ref.get(case_ref)
        elif ref.domain == "compliance":
            sref = service_ref_by_ura_ref_id.get(ref.ref_id, "")
            item_uuid = service_id_by_ref.get(sref) if sref else None

        # Migration 051: per-ref word count of the aggregator-view content
        # (exactly what the LLM grounded against). Derived from the URA
        # result when present; falls back to 0 when no URA was supplied
        # (replay tests, legacy callers).
        word_count = 0
        ura_for_ref = ura_by_ref_id.get(ref.ref_id)
        if ura_for_ref is not None:
            try:
                rendered = render_aggregator_content(ura_for_ref.for_aggregator(ref.n))
                word_count = _count_words(rendered)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "persist_item_references: word-count render failed for n=%d: %s",
                    ref.n, exc,
                )

        payloads.append({
            "wi_id": wi_id,
            "item_id": item_uuid,           # may be None — readers fall back to ref_id
            "ref_id": ref.ref_id,
            "domain": ref.domain,
            "n": ref.n,
            "relevance": ref.relevance,
            "used": ref.n in cited_set,
            "sub_queries": list(sq_map.get(ref.n, [])),
            "content_word_count": word_count,
        })

    if not payloads:
        return 0

    try:
        supabase.table("workspace_item_references").insert(payloads).execute()
    except Exception as exc:  # noqa: BLE001
        # Mirrors the publisher's forensic-write envelope — log and swallow
        # so a refs-write hiccup never crashes the user-visible publish.
        logger.exception(
            "persist_item_references: insert failed for wi_id=%s: %s",
            wi_id, exc,
        )
        return 0

    return len(payloads)


_UUID_RE = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_like_uuid(s: str) -> bool:
    import re

    return bool(s) and bool(re.match(_UUID_RE, s))


def _count_words(text: str) -> int:
    """Whitespace-split word count — mirrors the SQL ``compute_word_count``
    function from migration 048. Language-agnostic (Arabic / English /
    mixed). Empty / whitespace-only text returns 0.
    """
    if not text:
        return 0
    stripped = text.strip()
    if not stripped:
        return 0
    return len(stripped.split())


def _fetch_case_ids(
    supabase: SupabaseClient,
    case_refs: Sequence[str],
) -> dict[str, str]:
    """``case_ref -> cases.id`` map. Batched."""
    out: dict[str, str] = {}
    refs = sorted({r for r in case_refs if r})
    for i in range(0, len(refs), _ID_BATCH):
        batch = refs[i:i + _ID_BATCH]
        try:
            resp = (
                supabase.table("cases")
                .select("id, case_ref")
                .in_("case_ref", batch)
                .execute()
            )
            for r in resp.data or []:
                ref = r.get("case_ref")
                rid = r.get("id")
                if ref and rid:
                    out[ref] = str(rid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("references_service: _fetch_case_ids batch failed: %s", exc)
    return out


def _fetch_service_ids(
    supabase: SupabaseClient,
    service_refs: Sequence[str],
) -> dict[str, str]:
    """``service_ref -> services.id`` map. Batched."""
    out: dict[str, str] = {}
    refs = sorted({r for r in service_refs if r})
    for i in range(0, len(refs), _ID_BATCH):
        batch = refs[i:i + _ID_BATCH]
        try:
            resp = (
                supabase.table("services")
                .select("id, service_ref")
                .in_("service_ref", batch)
                .execute()
            )
            for r in resp.data or []:
                ref = r.get("service_ref")
                rid = r.get("id")
                if ref and rid:
                    out[ref] = str(rid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("references_service: _fetch_service_ids batch failed: %s", exc)
    return out
