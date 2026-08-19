"""Read- and write-path service for ``workspace_item_references``.

Replaces the JSONB blob ``workspace_items.metadata.references`` (migration
049). The table stores per-WI ref state — ``(wi_id, item_id, ref_id,
domain, n, relevance, used, sub_queries)`` — and the full display payload
(``Reference`` pydantic shape) is reconstructed on read by joining to the
existing source tables via the URA-enrichment helpers and ``source_viewer``.

Migration 050: two-key design.

- ``item_id`` (UUID, nullable) — source row PK. ``chunks_v2.id``,
  ``cases.id``, ``services.id``, ``circulars.id``, ``articles_v2.id`` or
  ``regulations_v2.id``. The preferred join key for cross-WI queries ("which
  WIs cite this chunk?").
- ``ref_id`` (TEXT, always set) — the emitted identifier
  (``reg:<uuid>`` | ``case:<case_ref>`` | ``compliance:<sha1[:16]>`` |
  ``circular:<uuid>`` | ``article:<uuid>`` | ``regdoc:<uuid>``). The durable
  fallback when item_id failed to resolve, and the forensic-traceability key
  into ``retrieval_artifacts``.

Migration 136 (simple_search, plan §6.1a) added the last two: ``articles`` (ONE
مادة, ``article:<articles_v2.id>``) and ``regulation_docs`` (a WHOLE نظام,
``regdoc:<regulations_v2.id>``). Both carry their OWN prefix because ``reg:``
hard-assumes a ``chunks_v2.id`` — reusing it inserts cleanly and then renders a
dead stub, silently (§9 trap 4).

Public surface:
    fetch_item_references(supabase, wi_id, *, used_only=False,
                          with_source_views=False) -> list[Reference]
    fetch_item_references_payload(supabase, wi_id, *, used_only=False) -> list[dict]
    fetch_reference_row(supabase, wi_id, n) -> dict | None
    build_reference_source_view(supabase, row) -> SourceView | None
    persist_item_references(supabase, wi_id, references, ura_results,
                            cited_numbers, ref_to_sub_queries) -> int

The read path explicitly reuses ``for_reference()`` /
``preprocessor._reference_from_ura()`` / ``preprocessor.build_snippet`` /
``source_viewer.build_source_view`` so the output is byte-for-byte identical
to what the publisher used to bake into JSONB.

PHASE C — SOURCE BODIES LEFT THE LIST (access-tiers plan §6.1/§6.2)
-------------------------------------------------------------------
``fetch_item_references`` used to attach a fully-built ``source_view`` to every
reference, so one panel load shipped full case bodies, full chunk content and
UNCAPPED circular bodies (168 KB outliers) before the user clicked anything.
``[n]`` and «عرض المصدر» were pure client-side state changes, which made metering
structurally impossible: no server call happened at reveal time.

So ``with_source_views`` now defaults to **False**, and the full body is served
one-at-a-time by ``GET /api/v1/workspace/{item_id}/references/{n}/source``, which
runs ``resolve_access`` first. What stays in the list is the citation mesh —
``n``, ``title``, ``snippet``, ``ref_id``, ``domain``, links, ``cross_refs`` —
because §1.3 puts citation lists in the NEVER-gated class. Each entry also
carries ``has_source`` (see ``fetch_item_references_payload``) so the panel knows
whether to offer the «عرض المصدر» affordance without a probe request.

The default is the safe one on purpose: every caller that snapshots references
into a durable, anonymously-served artifact (``blog_posts.references_json``)
inherits the metered shape by DOING NOTHING. Opting back in is an explicit
keyword at one call site, which is exactly where such a decision should be
visible.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence, Union

from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
from agents.deep_search_v4.aggregator.models import Reference
from agents.deep_search_v4.aggregator.preprocessor import (
    _reference_from_ura,
    build_snippet,
    render_aggregator_content,
)
from agents.deep_search_v4.source_viewer import (
    SourceView,
    article_full_title,
    build_article_full_view,
    build_regulation_summary_view,
    build_source_view,
)
from agents.deep_search_v4.ura.enrich import (
    _enrich_cases,
    _enrich_regulations,
)
from agents.deep_search_v4.ura.schema import (
    CaseURAResult,
    CircularURAResult,
    ComplianceURAResult,
    RegURAResult,
    URAResultBase,
    cap_circular_content,
)

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_item_references",
    "fetch_item_references_payload",
    "fetch_reference_row",
    "build_reference_source_view",
    "persist_item_references",
]

# PostgREST `in_` batch size — matches enrich.py.
_ID_BATCH = 150

# Fallback stub label when the source row is gone / unresolvable.
_STUB_TITLE = "[المصدر غير متوفر]"

# Max concurrent build_source_view lookups per fetch_item_references call.
_SOURCE_VIEW_CONCURRENCY = 5

# How much of an article body the LIST-path shell keeps. The panel only ever
# renders a 500-char snippet off it (``build_snippet``), and Phase C forbids
# shipping bodies in the list at all — so the shell keeps a small working margin
# and the FULL body is re-read by ``source_viewer.build_article_full_view`` at
# reveal time. Measured 2026-08-15 over 51,792 مواد: p50 = 325 chars,
# p90 = 1,334, max = 244,419 — this cap exists for that tail.
_ARTICLE_SHELL_CONTENT_CHARS = 2_000

# ref_id prefixes, in one place so the read parsers, the write path and the
# resolver cannot drift. `reg:` is deliberately NOT reusable for either of these
# two: it hard-assumes a chunks_v2.id (plan §6.2 / §9 trap 4).
_ARTICLE_PREFIX = "article:"
_REGDOC_PREFIX = "regdoc:"


# ---------------------------------------------------------------------------
# simple_search shells (plan §6.1a)
# ---------------------------------------------------------------------------
#
# The four deep_search domains rebuild a typed URA result and project it through
# ``preprocessor._reference_from_ura``. The two simple_search domains have NO URA
# member — deep_search never produces an article or a whole-نظام result — and
# ``agents/deep_search_v4/ura/schema.py`` is not this layer's to extend. So they
# carry their own row-backed shells, deliberately shaped to be drop-in:
#
#   * ``.ref_id`` / ``.relevance``  — what every shell consumer reads generically.
#   * ``.content``                  — makes ``preprocessor.build_snippet`` work
#     unchanged: its legacy fall-through branch reads ``.content`` first, so both
#     domains get the same sentence-boundary-aware 500-char snippet every other
#     domain gets, with no per-domain snippet code.
#
# ``appears_in_sub_queries`` exists because callers iterate it defensively on any
# shell; a lookup has no sub-queries, so it is always empty.


@dataclass
class ArticleRefShell:
    """Read-path shell for ``domain='articles'`` — ONE مادة (``articles_v2``)."""

    ref_id: str
    relevance: str = "medium"
    article_id: str = ""
    article_number: str = ""
    content: str = ""
    """Body TRUNCATED to :data:`_ARTICLE_SHELL_CONTENT_CHARS` — snippet fuel
    only. The full body is re-read at reveal time; see the constant."""
    regulation_id: str = ""
    regulation_title: str = ""
    landing_url: str = ""
    doc_type: str = ""
    appears_in_sub_queries: list[int] = field(default_factory=list)


@dataclass
class RegulationDocRefShell:
    """Read-path shell for ``domain='regulation_docs'`` — a WHOLE نظام.

    ``domain='regulations'`` is a CHUNK of one. Keeping these apart is the entire
    point of the new domain (plan §6.2).
    """

    ref_id: str
    relevance: str = "medium"
    regulation_id: str = ""
    title: str = ""
    content: str = ""
    """``llm_summary`` (all 3,951 rows carry one; max 2,415 chars) falling back
    to ``summary``. Uncapped — it IS the abstract, never the statute."""
    landing_url: str = ""
    doc_type: str = ""
    appears_in_sub_queries: list[int] = field(default_factory=list)


# Every shape ``shells_by_n`` can hold. ``URAResultBase`` covers the four
# deep_search domains; the two dataclasses above cover simple_search.
RefShell = Union[URAResultBase, ArticleRefShell, RegulationDocRefShell]


# ---------------------------------------------------------------------------
# READ PATH
# ---------------------------------------------------------------------------


async def fetch_item_references(
    supabase: SupabaseClient,
    wi_id: str,
    *,
    used_only: bool = False,
    with_source_views: bool = False,
) -> list[Reference]:
    """Reconstruct ``list[Reference]`` for one workspace_item.

    Reads rows from ``workspace_item_references`` filtered by ``wi_id`` (and
    ``used`` when ``used_only=True``), groups by domain, batch-fetches the
    source rows, builds URA result shells, and runs them through the exact
    same projection pipeline the aggregator uses at publish time.

    Args:
        with_source_views: attach the full ``source_view`` body to every
            reference. **Defaults to False** (Phase C, §6.2): the full source is
            metered content now and is served one item at a time by
            ``GET /workspace/{item_id}/references/{n}/source`` after
            ``resolve_access``. Pass True only for a caller that genuinely needs
            every body in one payload AND is not a public/anonymous surface —
            there is currently no such caller.

    Returns:
        References ordered by ``n`` (ascending). Empty list if no rows.
    """
    references, _resolvable, _rows = await _load_references(
        supabase, wi_id, used_only=used_only, with_source_views=with_source_views
    )
    return references


async def fetch_item_references_payload(
    supabase: SupabaseClient,
    wi_id: str,
    *,
    used_only: bool = False,
) -> list[dict]:
    """The JSON-ready citation-list payload — no source bodies (§6.2 step 1).

    Same references as :func:`fetch_item_references`, dumped to plain dicts with
    one added key per entry:

        ``has_source``: bool — a full source view CAN be built for this ``n``.

    The frontend needs that bit to decide whether to render «عرض المصدر» at all,
    and it must not cost a probe request to learn it (a probe would either be a
    charge or a free oracle). It is computed from the enrichment that already
    happened: a reference whose source row could not be reconstructed renders as
    a stub card and has no body to reveal.

    ``source_view`` is still present in every entry, always ``null`` — the key is
    kept so an un-migrated client degrades to "no reveal button" instead of
    crashing on a missing property.

    Each entry also carries:

        ``library_url``: str | None — the cited item's page in OUR library
        («فتح الحكم / النظام / التعميم في ريحان»).

    That one is NAVIGATION, not content: it is a path to a page that enforces its
    own access tier, so it is resolved for free, for every card, on the LIST
    (which is why the button no longer requires spending a reveal first). It is
    ``None`` for any item with no published slug; the panel then renders the
    external link alone, never a hub fallback.

    A **compliance** reference resolves too, as of 2026-08-19, but only when the
    cited service has a published **service guide** — our own authored rewrite of
    the entity's official PDF user guide, at ``/compliance/{slug}``. ~169 of
    4,746 services have one, so ``None`` stays the common answer there and the
    card correctly shows the entity's own page alone. The guide's CONTENT is not
    involved on either side of this: the popup still shows a service as a title
    and a link with no procedure body, and nothing from ``guide_md`` enters agent
    context — the reader reaches the guide by leaving to its own page.

    ⚠ ``blog.py`` snapshots this payload into ``blog_posts.references_json``, so
    new posts inherit the key and posts published before it existed simply lack
    it — hence optional on the client, never required.
    """
    references, resolvable, rows = await _load_references(
        supabase, wi_id, used_only=used_only, with_source_views=False
    )

    # ONE batched resolution for the whole panel (≤4 round-trips), never
    # per-reference and never through ``resolve_access``. Fail-soft to {}.
    library_urls: dict[int, str] = {}
    try:
        from backend.app.services import library_items_service

        library_urls = await library_items_service.public_page_urls_for_reference_rows(
            supabase, rows
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "references_service: library url resolution failed for wi_id=%s: %s",
            wi_id, exc,
        )

    out: list[dict] = []
    for ref in references:
        entry = ref.model_dump(mode="json")
        entry["has_source"] = ref.n in resolvable
        entry["library_url"] = library_urls.get(ref.n)
        out.append(entry)
    return out


async def _load_references(
    supabase: SupabaseClient,
    wi_id: str,
    *,
    used_only: bool = False,
    with_source_views: bool = False,
) -> tuple[list[Reference], set[int], list[dict]]:
    """Shared read path. Returns ``(references, resolvable_ns, rows)``.

    ``resolvable_ns`` is what ``has_source`` is derived from: the set of ``n``
    values whose URA shell was successfully reconstructed, i.e. the references
    for which ``build_reference_source_view`` has something to build.

    ``rows`` are the raw ``workspace_item_references`` rows this read already
    paid for. They are handed back rather than re-queried because
    ``fetch_item_references_payload`` needs ``item_id`` / ``ref_id`` / ``domain``
    to batch-resolve ``library_url``, and a second SELECT for columns we are
    already holding would be a round-trip spent on nothing.
    """
    rows = await asyncio.to_thread(
        _select_reference_rows, supabase, wi_id, used_only
    )
    if not rows:
        return [], set(), []

    # Group rows by domain so each source-table fetch can be batched.
    #
    # ⚠ A domain missing from this dict is DROPPED ENTIRELY (§9 trap 5): the [n]
    # card never renders and the inline [n] marker in the body goes dead. That is
    # why a new domain lands here, in the CHECK constraint, and in
    # ``_stub_reference`` in the same change — never in one of the three.
    by_domain: dict[str, list[dict]] = {
        "regulations": [], "compliance": [], "cases": [], "circulars": [],
        "articles": [], "regulation_docs": [],
    }
    for row in rows:
        domain = row.get("domain")
        if domain in by_domain:
            by_domain[domain].append(row)
        else:
            logger.warning("fetch_item_references: unknown domain %r — skipping row", domain)

    # Build a shell per row, keyed by ``n`` so we can pair the reconstructed
    # Reference back to its row. Four domains rebuild a typed URA result; the two
    # simple_search domains carry their own row-backed shells (see ``RefShell``).
    shells_by_n: dict[int, RefShell] = {}

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
    if by_domain["circulars"]:
        circular_shells = await _build_circular_shells(
            supabase, by_domain["circulars"]
        )
        shells_by_n.update(circular_shells)
    if by_domain["articles"]:
        article_shells = await _build_article_shells(
            supabase, by_domain["articles"]
        )
        shells_by_n.update(article_shells)
    if by_domain["regulation_docs"]:
        regdoc_shells = await _build_regdoc_shells(
            supabase, by_domain["regulation_docs"]
        )
        shells_by_n.update(regdoc_shells)

    # Walk rows in order and build one Reference per shell.
    ordered_rows = sorted(rows, key=lambda r: int(r["n"]))
    references: list[Reference] = []
    pending_views: list[tuple[Reference, RefShell]] = []

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

        ref = _reference_from_shell(n, shell)
        # Snippet derives from the aggregator-view content (same call the
        # aggregator preprocessor makes at publish time) — except for
        # government services, which carry NO snippet at all.
        #
        # The two simple_search domains ride this SAME call: their shells expose
        # ``.content``, which is the first thing ``build_snippet``'s legacy
        # fall-through branch reads. An ``article_full`` snippet is therefore the
        # head of the مادة body (sentence-boundary-cut at 500 chars) and a
        # ``regulation_summary`` snippet is the head of the abstract — both
        # correct, with no per-domain snippet code to keep in sync.
        #
        # A service card is the service name and the issuing entity, nothing
        # else: ``services.service_name_ar`` is «{الجهة} - {اسم الخدمة}» on all
        # 4,717 rows, so both are already in the title. What the snippet added
        # was ``services.service_context`` — a blob written for the embedder,
        # not a reader: the description restated once per beneficiary, an
        # applicability sentence, then a «مرتبطة بـ: كلمة، كلمة، …» keyword tail
        # (4,707 of 4,717 rows). Clamped to two lines it read as the same
        # sentence twice, cut off mid-keyword-list.
        #
        # Blanked HERE, at the panel's read boundary, rather than in
        # ``build_snippet``: that function's output is still the aggregator
        # prompt's fallback ``<content>`` body, which must keep its text.
        ref.snippet = "" if ref.domain == "compliance" else build_snippet(shell)
        references.append(ref)
        pending_views.append((ref, shell))

    # PHASE C: the full bodies stay OUT of this response unless a caller
    # explicitly asks. `_attach_source_views` survives untouched as the
    # opt-in path and as the shared per-item builder.
    if pending_views and with_source_views:
        await _attach_source_views(supabase, pending_views)

    return references, {ref.n for ref, _ in pending_views}, rows


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
        # The primary row-select must NOT lie: a DB failure here previously
        # rendered as "no references" (empty panel). Re-raise as a 500 so the
        # client sees a retryable error, not fabricated emptiness. (Enrichment
        # failures downstream stay best-effort — degraded cards beat a failed
        # response.) Raised here in the service layer since the route handler
        # is out of scope.
        logger.exception("references_service: select rows failed for wi_id=%s: %s", wi_id, exc)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المراجع",
        ) from exc


# ---------------------------------------------------------------------------
# SINGLE-REFERENCE PATH (Phase C — the metered reveal)
# ---------------------------------------------------------------------------


async def fetch_reference_row(
    supabase: SupabaseClient,
    wi_id: str,
    n: int,
) -> dict | None:
    """One ``workspace_item_references`` row by ``(wi_id, n)``, or ``None``.

    Deliberately keyed on ``wi_id`` as well as ``n``: the reveal endpoint has
    already proven the caller owns ``wi_id``, so scoping the lookup to that WI is
    what makes ``n`` — a small, guessable integer — safe to accept from a client.

    Raises the same 500 envelope as the list read when the SELECT itself fails:
    "no such reference" and "the database is down" must not look alike here,
    because the caller turns the former into a 404 and would otherwise mask an
    outage as a missing citation.
    """
    rows = await asyncio.to_thread(_select_reference_row, supabase, wi_id, int(n))
    return rows[0] if rows else None


def _select_reference_row(
    supabase: SupabaseClient,
    wi_id: str,
    n: int,
) -> list[dict]:
    """Sync single-row read — runs under ``asyncio.to_thread``."""
    try:
        resp = (
            supabase.table("workspace_item_references")
            .select(
                "ref_pk, wi_id, item_id, ref_id, domain, n, relevance, used, sub_queries"
            )
            .eq("wi_id", wi_id)
            .eq("n", int(n))
            .limit(1)
            .execute()
        )
        return list(resp.data or [])
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "references_service: select row failed for wi_id=%s n=%s: %s", wi_id, n, exc
        )
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المرجع",
        ) from exc


async def build_reference_source_view(
    supabase: SupabaseClient,
    row: dict,
) -> SourceView | None:
    """Build the ONE ``SourceView`` for a single reference row, or ``None``.

    This is the body that used to ride along in the list response. It rebuilds
    the URA shell through the very same per-domain ``_build_*_shells`` helpers
    the list read uses (called with a one-row list) and then hands it to
    ``source_viewer.build_source_view`` — no second implementation of the
    projection exists, so the reveal payload cannot drift from what the panel
    used to show.

    ``None`` means the source row is gone / unreconstructable — the same
    condition that makes the list report ``has_source=False``. Callers must NOT
    treat it as a refusal (it is not an entitlement outcome).
    """
    domain = (row.get("domain") or "").strip()
    n = int(row.get("n") or 0)

    if domain == "regulations":
        shells: dict[int, RefShell] = await _build_reg_shells(supabase, [row])  # type: ignore[assignment]
    elif domain == "cases":
        shells = await _build_case_shells(supabase, [row])  # type: ignore[assignment]
    elif domain == "compliance":
        shells = await _build_compliance_shells(supabase, [row])  # type: ignore[assignment]
    elif domain == "circulars":
        shells = await _build_circular_shells(supabase, [row])  # type: ignore[assignment]
    elif domain == "articles":
        shells = await _build_article_shells(supabase, [row])  # type: ignore[assignment]
    elif domain == "regulation_docs":
        shells = await _build_regdoc_shells(supabase, [row])  # type: ignore[assignment]
    else:
        logger.warning("build_reference_source_view: unknown domain %r", domain)
        return None

    shell = shells.get(n)
    if shell is None:
        return None
    return await _safe_build_source_view(supabase, shell)


# ---------------------------------------------------------------------------
# Shell -> Reference projection
# ---------------------------------------------------------------------------


def _reference_from_shell(n: int, shell: RefShell) -> Reference:
    """Project ANY read-path shell onto a numbered ``Reference``.

    The four deep_search domains go through ``preprocessor._reference_from_ura``
    (the load-bearing ``ReferenceView -> Reference`` mapping, untouched). The two
    simple_search domains are projected here because they have no URA member to
    project FROM — see ``RefShell``.
    """
    if isinstance(shell, ArticleRefShell):
        return _reference_from_article_shell(n, shell)
    if isinstance(shell, RegulationDocRefShell):
        return _reference_from_regdoc_shell(n, shell)
    return _reference_from_ura(n, shell)


def _reference_from_article_shell(n: int, shell: ArticleRefShell) -> Reference:
    """``domain='articles'`` -> a card that reads «المادة 81 من نظام العمل».

    ``title`` is built by ``source_viewer.article_full_title`` — the SAME pure
    helper the popup header uses — so the card and the dialog it opens can never
    disagree about what the citation is called.

    ``regulation_title`` carries the parent نظام (the panel's "parent label" slot,
    exactly as the reg domain uses it), ``landing_url`` is the external exit and
    ``doc_type`` drives the type chip (لائحة / تنظيم / … rather than a blanket
    نظام). ``snippet`` is stamped by the caller.
    """
    return Reference(
        n=n,
        source_type="article_full",
        regulation_title=shell.regulation_title,
        article_num=shell.article_number or None,
        title=article_full_title(shell.article_number, shell.regulation_title),
        snippet="",
        relevance=shell.relevance,  # type: ignore[arg-type]
        ref_id=shell.ref_id,
        domain="articles",
        landing_url=shell.landing_url,
        doc_type=shell.doc_type,
    )


def _reference_from_regdoc_shell(n: int, shell: RegulationDocRefShell) -> Reference:
    """``domain='regulation_docs'`` -> a card for the WHOLE نظام.

    Both title slots carry the نظام's own name: unlike a chunk or a مادة, the
    document has no parent to name above it.
    """
    return Reference(
        n=n,
        source_type="regulation_summary",
        regulation_title=shell.title,
        title=shell.title,
        snippet="",
        relevance=shell.relevance,  # type: ignore[arg-type]
        ref_id=shell.ref_id,
        domain="regulation_docs",
        landing_url=shell.landing_url,
        doc_type=shell.doc_type,
    )


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

    ``with_summary=True`` is the whole reason that flag exists. A rebuilt shell
    starts with nothing but a ``ref_id`` — no ``case_content`` — so without the
    ``cases.summary`` column the card has no text to derive its title from and
    falls back to «حكم {court}». The LIVE search path leaves it False: the
    adapter already carried the summary in, and refetching ~3 KB × refs on every
    turn would buy nothing.
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
        await _enrich_cases(shells, supabase, with_summary=True)
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

    ⚠ ``item_id`` being the ONLY usable handle is also what
    ``library_items_service._guide_ids_for_services`` depends on to find the
    service's published guide — so a row that loses it loses both its card
    details and its «افتح الدليل الشامل للخدمة في ريحان» exit. Verified live
    2026-08-19: all 509 compliance rows carry one.
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
    """Mirror ``ura.reg_adapter._service_ref_id`` — sha1[:16].

    Only used to fabricate a plausible ``ref_id`` on the reconstructed
    ComplianceURAResult shell so downstream code that parses ``ref_id``
    keeps working. The real lookup key is ``service_ref``.
    """
    import hashlib

    if not service_ref:
        return ""
    return hashlib.sha1(service_ref.encode("utf-8")).hexdigest()[:16]


def _circular_id_from_row(row: dict) -> str:
    """Return circulars.id (uuid text) for a circulars row.

    Prefers ``item_id`` (persist mints it from the ``circular:<uuid>`` ref_id);
    falls back to parsing that ref_id so rows whose item_id wasn't set still
    render. Mirrors ``_reg_chunk_id_from_row``.
    """
    item_id = row.get("item_id")
    if item_id:
        return str(item_id)
    ref_id = (row.get("ref_id") or "").strip()
    if ref_id.startswith("circular:"):
        return ref_id[len("circular:"):]
    return ""


def _circular_entity_name(row: dict) -> str:
    """Issuing entity name from an embedded ``entities`` object (dict or list).

    The name rides in via the ``circulars_entity_id_fkey`` PostgREST embed (a
    to-one object; a list is tolerated defensively). Mirrors reg_search's
    ``_circular_entity_name``.
    """
    ent = row.get("entities")
    if isinstance(ent, dict):
        return (ent.get("entity_name") or "").strip()
    if isinstance(ent, list) and ent and isinstance(ent[0], dict):
        return (ent[0].get("entity_name") or "").strip()
    return ""


async def _build_circular_shells(
    supabase: SupabaseClient,
    rows: list[dict],
) -> dict[int, CircularURAResult]:
    """Build CircularURAResult shells from ``circulars`` table rows.

    ``item_id`` is ``circulars.id`` (persist mints it from the ``circular:<uuid>``
    ref_id); a NULL item_id falls back to parsing that ref_id (mirrors the
    regulations shell builder). One batched ``circulars`` fetch pulls circ_ref,
    title, content, source, and the embedded issuing entity name.

    D11 capped-vs-full split: the shell's ``content`` is the AGGREGATOR view —
    the full body capped at 4k with the truncation marker (``cap_circular_content``)
    — so the hover snippet and aggregator parity hold. The UNCAPPED user-facing
    body is rebuilt lazily by ``source_viewer._build_circular_view`` when a source
    view is requested, exactly like the case/service views fetch their full body
    fresh — the 168k outlier never rides in this references-list response.
    """
    rows_by_id: dict[str, list[dict]] = {}
    for row in rows:
        circ_id = _circular_id_from_row(row)
        if not circ_id:
            continue
        rows_by_id.setdefault(circ_id, []).append(row)

    if not rows_by_id:
        return {}

    circulars = await asyncio.to_thread(
        _fetch_circulars_by_id, supabase, list(rows_by_id.keys())
    )

    shells_by_n: dict[int, CircularURAResult] = {}
    for circ_id, related_rows in rows_by_id.items():
        circ = circulars.get(circ_id) or {}
        title = (circ.get("title") or "").strip()
        entity_name = _circular_entity_name(circ)
        # shell.content = 4k-capped aggregator view (snippet/aggregator parity).
        content = cap_circular_content(circ.get("content"))
        source_url = (circ.get("source") or "").strip()
        circ_ref = (circ.get("circ_ref") or "").strip()
        for row in related_rows:
            n = int(row["n"])
            shell = CircularURAResult(
                # Prefer the row's own ref_id; re-mint from the id otherwise so
                # source_viewer can re-parse ``circular:<uuid>`` for the full body.
                ref_id=(row.get("ref_id") or f"circular:{circ_id}"),
                source_type="circular",
                relevance=row.get("relevance", "medium"),
                circ_ref=circ_ref,
                title=title,
                entity_name=entity_name,
                content=content,
                source_url=source_url,
            )
            shells_by_n[n] = shell

    return shells_by_n


def _fetch_circulars_by_id(
    supabase: SupabaseClient,
    circular_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Batched ``circulars`` fetch keyed by circulars.id UUID.

    Embeds the issuing entity name via the ``circulars_entity_id_fkey`` FK (the
    same embed reg_search uses at retrieval time). ``content`` is the full body —
    the shell caps it to the 4k aggregator view; the uncapped user view is
    rebuilt in source_viewer.
    """
    out: dict[str, dict[str, Any]] = {}
    ids = sorted({cid for cid in circular_ids if cid})
    for i in range(0, len(ids), _ID_BATCH):
        batch = ids[i:i + _ID_BATCH]
        try:
            resp = (
                supabase.table("circulars")
                .select(
                    "id, circ_ref, title, content, source, "
                    "entities!circulars_entity_id_fkey(entity_name)"
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
                "references_service: _fetch_circulars_by_id batch failed: %s", exc,
            )
    return out


def _article_id_from_row(row: dict) -> str:
    """Return ``articles_v2.id`` (uuid text) for an ``articles`` row.

    Prefers ``item_id`` (persist mints it from the ``article:<uuid>`` ref_id);
    falls back to parsing that ref_id. Mirrors ``_circular_id_from_row``.
    """
    item_id = row.get("item_id")
    if item_id:
        return str(item_id)
    ref_id = (row.get("ref_id") or "").strip()
    if ref_id.startswith(_ARTICLE_PREFIX):
        return ref_id[len(_ARTICLE_PREFIX):]
    return ""


def _regdoc_id_from_row(row: dict) -> str:
    """Return ``regulations_v2.id`` (uuid text) for a ``regulation_docs`` row.

    Prefers ``item_id``; falls back to the ``regdoc:<uuid>`` ref_id. Mirrors
    ``_circular_id_from_row``.

    ⚠ NEVER accept a ``reg:`` prefix here. ``reg:`` carries a **chunks_v2.id**,
    and treating one as a regulations_v2.id is precisely the silent failure §6.2
    documents: the uuid validates, the row inserts, the read finds nothing, the
    shell is pruned, and the card renders as a dead stub with no errors anywhere.
    """
    item_id = row.get("item_id")
    if item_id:
        return str(item_id)
    ref_id = (row.get("ref_id") or "").strip()
    if ref_id.startswith(_REGDOC_PREFIX):
        return ref_id[len(_REGDOC_PREFIX):]
    return ""


async def _build_article_shells(
    supabase: SupabaseClient,
    rows: list[dict],
) -> dict[int, ArticleRefShell]:
    """Build :class:`ArticleRefShell` shells from ``articles_v2`` rows.

    Two batched round-trips, both fail-soft per batch: the مواد by id, then their
    parent أنظمة by ``regulation_id`` (``articles_v2`` is a VIEW, so there is no
    FK for a PostgREST embed to walk). Mirrors ``_build_circular_shells``.

    Like ``_build_reg_shells``, shells whose source row came back empty are
    PRUNED so the caller falls back to a stub card rather than rendering a
    misleading blank one.

    The shell body is truncated to :data:`_ARTICLE_SHELL_CONTENT_CHARS` — the
    panel only needs snippet fuel, and Phase C keeps bodies out of the list
    response entirely. ``source_viewer.build_article_full_view`` re-reads the
    FULL body when a reveal is actually requested, exactly as the circular /
    case views re-read theirs.
    """
    rows_by_id: dict[str, list[dict]] = {}
    for row in rows:
        article_id = _article_id_from_row(row)
        if not article_id:
            continue
        rows_by_id.setdefault(article_id, []).append(row)

    if not rows_by_id:
        return {}

    articles = await asyncio.to_thread(
        _fetch_articles_by_id, supabase, list(rows_by_id.keys())
    )

    reg_ids = sorted({
        str(a.get("regulation_id") or "")
        for a in articles.values()
        if a.get("regulation_id")
    })
    regulations = (
        await asyncio.to_thread(_fetch_regulations_by_id, supabase, reg_ids)
        if reg_ids
        else {}
    )

    shells_by_n: dict[int, ArticleRefShell] = {}
    for article_id, related_rows in rows_by_id.items():
        art = articles.get(article_id) or {}
        body = (art.get("content") or "").strip()
        number = str(art.get("article_number") or "").strip()
        reg_id = str(art.get("regulation_id") or "").strip()
        reg = regulations.get(reg_id) or {}
        reg_title = (reg.get("clean_title") or reg.get("title") or "").strip()

        # Prune: nothing to show, nothing to name → stub card (see docstring).
        if not body and not number and not reg_title:
            continue

        for row in related_rows:
            n = int(row["n"])
            shells_by_n[n] = ArticleRefShell(
                # Prefer the row's own ref_id; re-mint from the id otherwise so
                # downstream code can re-parse ``article:<uuid>``.
                ref_id=(row.get("ref_id") or f"{_ARTICLE_PREFIX}{article_id}"),
                relevance=row.get("relevance", "medium"),
                article_id=article_id,
                article_number=number,
                content=body[:_ARTICLE_SHELL_CONTENT_CHARS],
                regulation_id=reg_id,
                regulation_title=reg_title,
                landing_url=(reg.get("landing_url") or "").strip(),
                doc_type=(reg.get("doc_type_raw") or "").strip(),
            )

    return shells_by_n


async def _build_regdoc_shells(
    supabase: SupabaseClient,
    rows: list[dict],
) -> dict[int, RegulationDocRefShell]:
    """Build :class:`RegulationDocRefShell` shells from ``regulations_v2`` rows.

    ONE batched, fail-soft fetch — the ref IS the document, so there is no parent
    to resolve. Shells whose regulation row is gone are pruned to a stub.
    """
    rows_by_id: dict[str, list[dict]] = {}
    for row in rows:
        reg_id = _regdoc_id_from_row(row)
        if not reg_id:
            continue
        rows_by_id.setdefault(reg_id, []).append(row)

    if not rows_by_id:
        return {}

    regulations = await asyncio.to_thread(
        _fetch_regulations_by_id, supabase, list(rows_by_id.keys())
    )

    shells_by_n: dict[int, RegulationDocRefShell] = {}
    for reg_id, related_rows in rows_by_id.items():
        reg = regulations.get(reg_id) or {}
        title = (reg.get("clean_title") or reg.get("title") or "").strip()
        summary = ((reg.get("llm_summary") or reg.get("summary") or "") or "").strip()
        if not title and not summary:
            continue

        for row in related_rows:
            n = int(row["n"])
            shells_by_n[n] = RegulationDocRefShell(
                ref_id=(row.get("ref_id") or f"{_REGDOC_PREFIX}{reg_id}"),
                relevance=row.get("relevance", "medium"),
                regulation_id=reg_id,
                title=title,
                content=summary,
                landing_url=(reg.get("landing_url") or "").strip(),
                doc_type=(reg.get("doc_type_raw") or "").strip(),
            )

    return shells_by_n


def _fetch_articles_by_id(
    supabase: SupabaseClient,
    article_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Batched ``articles_v2`` fetch keyed by ``articles_v2.id``.

    Batched by :data:`_ID_BATCH` and fail-soft PER BATCH (a failing batch
    contributes nothing; the others still render) — the ``_fetch_circulars_by_id``
    envelope verbatim.
    """
    out: dict[str, dict[str, Any]] = {}
    ids = sorted({aid for aid in article_ids if aid})
    for i in range(0, len(ids), _ID_BATCH):
        batch = ids[i:i + _ID_BATCH]
        try:
            resp = (
                supabase.table("articles_v2")
                .select("id, regulation_id, article_number, content")
                .in_("id", batch)
                .execute()
            )
            for r in resp.data or []:
                rid = r.get("id")
                if rid:
                    out[str(rid)] = r
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "references_service: _fetch_articles_by_id batch failed: %s", exc,
            )
    return out


def _fetch_regulations_by_id(
    supabase: SupabaseClient,
    regulation_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Batched ``regulations_v2`` fetch keyed by ``regulations_v2.id``.

    Serves both simple_search shells: the whole-نظام card needs the summary, the
    مادة card needs its parent's title / link / doc_type. Fail-soft per batch.
    """
    out: dict[str, dict[str, Any]] = {}
    ids = sorted({rid for rid in regulation_ids if rid})
    for i in range(0, len(ids), _ID_BATCH):
        batch = ids[i:i + _ID_BATCH]
        try:
            resp = (
                supabase.table("regulations_v2")
                .select(
                    "id, title, clean_title, llm_summary, summary, "
                    "landing_url, doc_type_raw"
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
                "references_service: _fetch_regulations_by_id batch failed: %s", exc,
            )
    return out


async def _safe_build_source_view(
    supabase: SupabaseClient,
    shell: RefShell,
) -> SourceView | None:
    """Build the ONE ``SourceView`` for a shell, with the failure envelope.

    Shared by the bulk attach path and the per-item reveal endpoint so both
    behave identically when a source table hiccups: log, return ``None``, never
    raise into the caller's response.

    The two simple_search shells dispatch to their own id-keyed builders because
    ``build_source_view`` is a URA-type dispatch and neither has a URA member.
    Both re-read the source fresh, which is what keeps the revealed body full
    while the list stays a mesh.
    """
    try:
        if isinstance(shell, ArticleRefShell):
            return await build_article_full_view(
                supabase,
                shell.article_id,
                # Used only if the parent lookup misses on the reveal: better a
                # labelled article than a bare body.
                article_number=shell.article_number,
                regulation_title=shell.regulation_title,
                regulation_source_url=shell.landing_url,
            )
        if isinstance(shell, RegulationDocRefShell):
            return await build_regulation_summary_view(supabase, shell.regulation_id)
        return await build_source_view(supabase, shell)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "references_service: build_source_view(%s) failed: %s",
            getattr(shell, "ref_id", "?"),
            exc,
        )
        return None


async def _attach_source_views(
    supabase: SupabaseClient,
    pending: list[tuple[Reference, RefShell]],
) -> None:
    """Parallel ``build_source_view`` resolution; failures leave source_view=None.

    Phase C note: no longer on the default list path (§6.2) — it runs only when a
    caller passes ``with_source_views=True``. Kept intact because it is still the
    right shape for a bulk build, and because the per-item reveal shares its
    failure envelope via ``_safe_build_source_view``.

    The fan-out is bounded by a per-call semaphore so a panel with many refs
    can't open an unbounded number of concurrent source-table reads against a
    sync Supabase client. The semaphore is created here (not module-level) so
    it binds to the running event loop — important because this codebase mixes
    loops via ``asyncio.to_thread``.
    """
    if not pending:
        return

    sem = asyncio.Semaphore(_SOURCE_VIEW_CONCURRENCY)

    async def _one(shell: RefShell) -> Any:
        async with sem:
            return await _safe_build_source_view(supabase, shell)

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
    # domain -> the ``Reference.source_type`` Literal that belongs with it. The
    # two simple_search entries are ``article_full`` / ``regulation_summary``,
    # NEVER the legacy ``article`` / ``regulation`` values above them — a stub
    # that mislabels itself renders through the frontend's permissive legacy
    # union arm as bare markdown, with no error (§9 trap 3).
    _stub_source_type = {
        "regulations": "regulation",
        "cases": "case",
        "compliance": "gov_service",
        "circulars": "circular",
        "articles": "article_full",
        "regulation_docs": "regulation_summary",
    }.get(domain, "regulation")
    return Reference(
        n=int(row["n"]),
        source_type=_stub_source_type,
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
        elif ref.domain == "circulars":
            # ref_id = "circular:<uuid>" — the circulars.id rides in directly
            # (like regulations), so strip the prefix and validate as a uuid.
            candidate = (
                ref.ref_id[len("circular:"):]
                if ref.ref_id.startswith("circular:")
                else ref.ref_id
            )
            item_uuid = candidate if _looks_like_uuid(candidate) else None
        elif ref.domain == "articles":
            # ref_id = "article:<articles_v2.id>" — the PK rides in directly.
            candidate = (
                ref.ref_id[len(_ARTICLE_PREFIX):]
                if ref.ref_id.startswith(_ARTICLE_PREFIX)
                else ref.ref_id
            )
            item_uuid = candidate if _looks_like_uuid(candidate) else None
        elif ref.domain == "regulation_docs":
            # ref_id = "regdoc:<regulations_v2.id>". Distinct prefix on purpose:
            # a regulations_v2 uuid written under ``reg:`` passes every check
            # here and renders a dead stub on read (§6.2 / §9 trap 4).
            candidate = (
                ref.ref_id[len(_REGDOC_PREFIX):]
                if ref.ref_id.startswith(_REGDOC_PREFIX)
                else ref.ref_id
            )
            item_uuid = candidate if _looks_like_uuid(candidate) else None
        else:
            # EXPLICIT. This chain had no ``else`` until 2026-08-15, so a ref
            # carrying a domain nobody had wired up was written with a NULL
            # item_id and no trace of why — the row inserted, the read pruned it,
            # and the card rendered as a stub. It still writes (``ref_id`` alone
            # satisfies ``workspace_item_references_has_key``, and a degraded card
            # beats a dropped citation), but it says so.
            logger.warning(
                "persist_item_references: no item_id resolver for domain=%r "
                "(ref n=%d, ref_id=%s) — writing with item_id=NULL",
                ref.domain, ref.n, ref.ref_id,
            )

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
        return len(payloads)
    except Exception as exc:  # noqa: BLE001
        # Mirrors the publisher's forensic-write envelope — log and swallow
        # so a refs-write hiccup never crashes the user-visible publish.
        logger.exception(
            "persist_item_references: batch insert failed for wi_id=%s (%d refs) "
            "— retrying row-by-row: %s",
            wi_id, len(payloads), exc,
        )

    # Per-row fallback. The batch above is ONE atomic INSERT, so a single bad
    # row takes every other ref down with it and the artifact renders with no
    # المراجع section at all (ReferencePanel returns null on an empty list).
    # That is exactly how the ``domain='circulars'`` CHECK gap (migration 102)
    # silently emptied whole panels. Retrying one row at a time keeps the good
    # refs and localises the loss to the offending one, which is logged at
    # ERROR with enough identity to find it.
    written = 0
    for payload in payloads:
        try:
            supabase.table("workspace_item_references").insert(payload).execute()
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "persist_item_references: dropping ref n=%s domain=%s ref_id=%s "
                "for wi_id=%s: %s",
                payload.get("n"),
                payload.get("domain"),
                payload.get("ref_id"),
                wi_id,
                exc,
            )
    return written


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
