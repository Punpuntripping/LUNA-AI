"""Stage 3 of the unfolding pipeline: URA result -> click-ready ``SourceView``.

Pipeline position::

    reranker  ->  aggregator/URA  ->  source_viewer (this module)
                                              |
                                              v
                                  popup payload for the artifact UI

Given any ``URAResult`` (the discriminated union from
``agents.deep_search_v4.ura.schema``) plus a Supabase client, ``build_source_view``
performs the **minimum** Supabase lookups required to fill in the fields that
the URA result does not already carry, and returns a discriminated-union
``SourceView`` model that the frontend can render directly into the source
popup.

View variants the user can click (URA v3.0 -- the reg domain is chunk-shaped,
the article/section split is gone):

- ``ChunkSourceView``      -- a regulation chunk: ``chunk_content`` + the parent
  regulation's landing link. NOT ``chunk_context`` (see ``_build_reg_view``).
- ``CaseSourceView``       -- a court ruling; the ``summary`` digest (NOT the
  raw ruling text -- that lives on ``/judgments/{slug}``), one ``details_url``
  and a human-readable composite title.
- ``ServiceSourceView``    -- a government service; the name and ONE exit
  (``services.service_url``). No body: the popup is a link, not a guide.
- ``CircularSourceView``   -- a ministerial circular (تعميم); the FULL uncapped
  ``circulars.content`` body + issuing entity name + ``circulars.source`` link.

``ArticleSourceView`` / ``SectionSourceView`` / ``RegulationSourceView`` are
retained in the ``SourceView`` union ONLY so pre-v3.0 persisted ``source_view``
payloads still validate on reload -- ``build_source_view`` no longer produces
them.

The ref_id formats produced by the three executor adapters are::

    reg_adapter.py (chunk)       ->  ``reg:<uuid>``        (the chunks_v2.id;
                                     enrich.py strips the ``reg:`` prefix)
    case_adapter.py              ->  ``case:<uuid>``       (the cases.id)
    reg_compliance (service)     ->  ``compliance:<sha1>`` (16-char hash; the
                                     real lookup key is ``ComplianceURAResult.service_ref``,
                                     not the ref_id itself)
    reg_compliance (circular)    ->  ``circular:<uuid>``   (the circulars.id;
                                     _build_circular_view strips the prefix)

Example::

    from agents.deep_search_v4.source_viewer import build_source_view

    view = await build_source_view(supabase, ura_result)
    if view.source_type == "case":
        return {"url": view.details_url, "title": view.title}

Note: Supabase calls are made via ``asyncio.to_thread`` because the project
uses the **sync** supabase-py client inside async route handlers (established
pattern -- see ``agents/deep_search_v3/case_search/unfold.py:340`` and
``agents/deep_search_v3/reg_search/reranker.py:319``).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from agents.deep_search_v4.shared.case_summary import strip_pipeline_sections
from agents.deep_search_v4.ura.schema import (
    CaseURAResult,
    CircularURAResult,
    ComplianceURAResult,
    RegURAResult,
    URAResult,
)

logger = logging.getLogger(__name__)

# Type alias kept loose -- the project uses ``supabase.client.Client`` (sync).
SupabaseClient = Any


# ---------------------------------------------------------------------------
# Pydantic SourceView discriminated union
# ---------------------------------------------------------------------------


class ChunkSourceView(BaseModel):
    """Click-ready payload for a regulation **chunk** (URA v3.0).

    The reg domain is chunk-shaped now -- the article/section split is gone.
    The URA result (post-``enrich.py``) already carries every field, so this
    view is built with no Supabase round-trip.
    """

    source_type: Literal["chunk"] = "chunk"
    title: str = ""
    """Parent regulation title (``RegURAResult.reg_title``)."""
    content: str = ""
    """The chunk's own text (``RegURAResult.chunk_content``) — nothing else.
    ``chunk_context`` was dropped from this body in 2026-08-08; see
    ``_build_reg_view``. Payloads persisted before then still carry the
    concatenated form and render as-is."""
    regulation_title: str = ""
    regulation_source_url: str = ""
    """Parent regulation's ``landing_url`` -- the ONE external click target.

    No PDF companion: the source popup offers exactly two ways out (the official
    link, and the item's page in our own library), so a raw file link was a third
    exit that led out of the product for content we already publish ourselves.
    """


class ArticleSourceView(BaseModel):
    """Click-ready payload for a regulation **article**.

    Legacy (pre-URA-v3.0) -- retained for persisted-artifact reload compat
    only. ``build_source_view`` no longer produces it.
    """

    source_type: Literal["article"] = "article"
    title: str
    article_num: str | None = None
    content: str = ""
    """Full article content (``articles.content``)."""
    regulation_title: str = ""
    regulation_source_url: str = ""
    """Parent regulation's ``source_url`` -- main click target when present."""


class SectionSourceView(BaseModel):
    """Click-ready payload for a regulation **section**.

    Legacy (pre-URA-v3.0) -- retained for persisted-artifact reload compat
    only. ``build_source_view`` no longer produces it.
    """

    source_type: Literal["section"] = "section"
    title: str
    content: str = ""
    """Section content -- ``section_summary`` + ``section_context`` concatenated
    (with a blank line separator) when both are present."""
    regulation_title: str = ""
    regulation_source_url: str = ""


class RegulationSourceView(BaseModel):
    """Click-ready payload for a **regulation** itself.

    Legacy (pre-URA-v3.0) -- retained for persisted-artifact reload compat
    only. ``build_source_view`` no longer produces it.
    """

    source_type: Literal["regulation"] = "regulation"
    title: str = ""
    source_url: str = ""


class CaseSourceView(BaseModel):
    """Click-ready payload for a **court case**.

    THE POPUP SHOWS THE ملخص, NOT THE RULING (2026-08-03). A citation preview is
    a "is this the ruling I need?" surface, and 8.5k chars of raw judgment text
    answers that worse than the structured digest does. The full text has its own
    home — the «فتح الحكم في ريحان» exit lands on ``/judgments/{slug}``, and the
    reveal already spent the unlock for that same ``judgment`` content_id, so the
    reader pays nothing extra to go read it there.
    """

    source_type: Literal["case"] = "case"
    title: str
    """Composite label: ``court | case_number | date_hijri`` (parts dropped if
    empty / pipe-separated)."""
    summary: str = ""
    """Structured digest (``cases.summary`` — ## الملخص / الوقائع / المطالبات /
    …) — what the popup renders. Falls back to ``cases.short_summary`` (the same
    ## الملخص section alone, which is also the /judgments lead) when the long
    digest is missing. Markdown."""
    content: str = ""
    """Raw ruling text (``cases.content``) — the LAST-RESORT body, populated
    ONLY when the ruling has no summary at all (18 rows of 30.5k). Never sent
    alongside ``summary``: it would be several KB the popup does not render."""
    details_url: str = ""


class ServiceSourceView(BaseModel):
    """Click-ready payload for a **government service** — title and link, nothing else.

    BODYLESS SINCE 2026-08-03. The popup used to render four Arabic-labelled
    blocks (intro, الخطوات, المتطلبات, المستندات المطلوبة) mirroring the retired
    ``/compliance/{slug}`` page. Both are gone: a procedure's steps and documents
    go stale the moment the issuing entity edits them, and restating them under
    ريحان's own chrome makes us the apparent authority on a process we do not own.
    The service's own page is the authority, so the citation now does one thing —
    name the service and open it.

    The extra fields are NOT kept for back-compat. ``source_view`` payloads
    persisted before this change validate fine (unknown keys are ignored, and
    every removed field was optional), and any that still carry the old body just
    stop rendering it.
    """

    source_type: Literal["gov_service"] = "gov_service"
    title: str
    """Service name in Arabic (``services.service_name_ar``)."""
    service_url: str = ""
    """``services.service_url`` — THE exit, and the only one. ``services.url``
    (the المنصة الوطنية portal link) was dropped with the body: a portal home page
    is not the cited source, and offering two exits made the reader choose between
    a document and a directory."""


class CircularSourceView(BaseModel):
    """Click-ready payload for a **ministerial circular** (تعميم).

    New under the unified ``search_topics`` corpus (D5/D11). The user view is the
    FULL circular body, uncapped — the URA shell only carries the 4k-capped
    aggregator view, so ``_build_circular_view`` fetches ``circulars`` fresh for
    the uncapped ``content`` (the 168k-char outlier lives here; the panel scrolls
    it, we never truncate). Mirrors the service view's flat, one-fetch shape.
    """

    source_type: Literal["circular"] = "circular"
    circ_ref: str = ""
    """``circulars.circ_ref`` — the human-readable circular reference code."""
    title: str = ""
    """Circular title (``circulars.title``)."""
    entity_name: str = ""
    """Issuing entity Arabic name (embedded ``entities.entity_name``)."""
    content: str = ""
    """FULL circular body (``circulars.content``) — uncapped, rendered as
    markdown in the popup. May be very long (max ~168k chars) → the panel
    scrolls."""
    url: str = ""
    """``circulars.source`` — the official source link (may be "")."""


SourceView = Annotated[
    Union[
        ChunkSourceView,
        ArticleSourceView,     # legacy -- reload compat only
        SectionSourceView,     # legacy -- reload compat only
        RegulationSourceView,  # legacy -- reload compat only
        CaseSourceView,
        ServiceSourceView,
        CircularSourceView,
    ],
    Field(discriminator="source_type"),
]


# ---------------------------------------------------------------------------
# ref_id parsing
# ---------------------------------------------------------------------------


def _parse_reg_ref_id(ref_id: str) -> tuple[str, str]:
    """Parse a regulation ref_id into ``(sub_kind, db_id)``.

    The reg_search adapter mints ``reg:<uuid>`` for everything (the
    sub-kind -- article / section / regulation -- lives on
    ``RegURAResult.source_type``). To stay robust, we also accept the
    extended ``reg:<kind>:<uuid>`` form in case the adapter changes later.

    Returns:
        ``(sub_kind, db_id)``. ``sub_kind`` is ``""`` when only the bare
        ``reg:<uuid>`` form is provided -- callers should then fall back to
        the URA result's ``source_type`` field. Returns ``("", "")`` when
        the ref_id is malformed or empty.
    """
    if not ref_id:
        return ("", "")
    parts = ref_id.split(":", 2)
    if len(parts) < 2 or parts[0] != "reg":
        return ("", "")
    if len(parts) == 2:
        return ("", parts[1])
    # reg:<kind>:<uuid>
    return (parts[1], parts[2])


def _parse_simple_ref_id(prefix: str, ref_id: str) -> str:
    """Extract the id suffix from ``<prefix>:<id>`` ref_ids (case / compliance)."""
    if not ref_id:
        return ""
    head, _, tail = ref_id.partition(":")
    if head != prefix:
        return ""
    return tail


# ---------------------------------------------------------------------------
# Supabase fetch helpers (sync client driven via asyncio.to_thread)
# ---------------------------------------------------------------------------


async def _fetch_case(supabase: SupabaseClient, case_ref: str) -> dict | None:
    """Look up a case by its human-readable ``case_ref`` (text), not its UUID.

    URA ``ref_id`` for cases encodes ``case:<case_ref>`` (see
    ``case_search/unfold_ura.py::_build_reranked_case_result`` where ``db_id``
    is set to ``full_row['case_ref']``). Filtering ``cases.id`` (uuid) with a
    ``case_ref`` value returns PostgREST 400. Always filter by ``case_ref``.

    Selects ``summary`` / ``short_summary`` — what the popup actually renders —
    plus ``content`` as the last-resort body for the ~18 rulings that carry
    neither summary. The URA result may or may not carry a body of its own (it
    does when produced by the case_search adapter; it doesn't when
    ``references_service`` rebuilds the shell from the relational refs table).
    """
    def _call() -> dict | None:
        try:
            resp = (
                supabase.table("cases")
                .select(
                    "id, court, court_level, city, case_number, "
                    "judgment_number, date_hijri, details_url, "
                    "summary, short_summary, content"
                )
                .eq("case_ref", case_ref)
                .maybe_single()
                .execute()
            )
            return resp.data if resp else None
        except Exception as e:
            logger.debug("source_viewer: fetch case %s failed: %s", case_ref, e)
            return None

    return await asyncio.to_thread(_call)


async def _fetch_service_by_ref(
    supabase: SupabaseClient, service_ref: str
) -> dict | None:
    """Fetch a service row by ``service_ref``.

    Two columns, because the popup renders two things. The body columns
    (``intro_*``, ``steps``, ``requirements``, ``required_documents``) and the
    ``url`` portal link are deliberately NOT projected — see ServiceSourceView
    for why they left the payload. They are several KB per reveal that nothing
    on screen would draw.
    """
    def _call() -> dict | None:
        try:
            resp = (
                supabase.table("services")
                .select("service_ref, service_name_ar, service_url")
                .eq("service_ref", service_ref)
                .maybe_single()
                .execute()
            )
            return resp.data if resp else None
        except Exception as e:
            logger.debug(
                "source_viewer: fetch service %s failed: %s", service_ref, e
            )
            return None

    return await asyncio.to_thread(_call)


async def _fetch_circular_by_id(
    supabase: SupabaseClient, circular_id: str
) -> dict | None:
    """Fetch a circular row by ``circulars.id`` (uuid).

    Pulls the FULL ``content`` (uncapped — user view per D11), the source link,
    circ_ref, title, and the embedded issuing entity name. The 168k-char outlier
    is read here on demand only; the references list never carries it.
    """
    def _call() -> dict | None:
        try:
            resp = (
                supabase.table("circulars")
                .select(
                    "id, circ_ref, title, content, source, "
                    "entities!circulars_entity_id_fkey(entity_name)"
                )
                .eq("id", circular_id)
                .maybe_single()
                .execute()
            )
            return resp.data if resp else None
        except Exception as e:
            logger.debug(
                "source_viewer: fetch circular %s failed: %s", circular_id, e
            )
            return None

    return await asyncio.to_thread(_call)


# ---------------------------------------------------------------------------
# Per-domain builders
# ---------------------------------------------------------------------------


def _strip_line_indent(text: str) -> str:
    """Strip leading whitespace from every line.

    chunks_v2 rows are PDF-extracted Arabic legal prose; the extractor often
    preserves stray indentation that has no semantic meaning in our text but
    triggers CommonMark's "indented code block" rule (4+ leading spaces) in
    react-markdown. The popup then renders a list of articles inside a
    ``<pre><code>`` box instead of as prose / bullets.

    Stripping per-line leading whitespace is safe here because the corpus has
    no nested-list semantics — bullets are flat ``- ...`` lines that markdown
    parses correctly when they start at column 0.
    """
    if not text:
        return text
    return "\n".join(line.lstrip() for line in text.splitlines())


async def _build_reg_view(
    supabase: SupabaseClient, ura: RegURAResult
) -> ChunkSourceView:
    """Build a ``ChunkSourceView`` from a ``RegURAResult`` (URA v3.0).

    The reg domain is chunk-shaped now. ``ura/enrich.py`` has already filled
    every field this view needs (``chunk_content``, ``reg_title``,
    ``landing_url``), so no Supabase round-trip is required -- ``supabase`` is
    accepted for signature symmetry only. ``RegURAResult.pdf_url`` is
    deliberately NOT projected: the popup no longer offers a PDF exit.

    ``chunk_context`` IS NOT SHOWN (2026-08-08). The popup used to append it
    under the مقطع — a pipeline-written paragraph placing the chunk in the
    statute («يقع هذا المقطع ضمن الباب الخامس …»). It is derived commentary, not
    the cited text, and it reads as part of the نظام because it sits inside the
    same body with no separator: a reader quoting the popup ends up quoting us.
    The field survives on ``RegURAResult`` (stored, and still in the forensic
    dumps) — it just no longer reaches a user surface.
    """
    _ = supabase  # unused -- reg views are fully URA-sourced post-enrich

    return ChunkSourceView(
        title=ura.reg_title or "",
        content=_strip_line_indent((ura.chunk_content or "").strip()),
        regulation_title=ura.reg_title or "",
        regulation_source_url=ura.landing_url or "",
    )


async def _build_case_view(
    supabase: SupabaseClient, ura: CaseURAResult
) -> CaseSourceView:
    """Resolve a ``CaseURAResult`` -> ``CaseSourceView``.

    URA does not carry ``details_url``, so we always fetch from ``cases``.
    """
    case_ref = _parse_simple_ref_id("case", ura.ref_id)
    row: dict = {}
    if case_ref:
        row = (await _fetch_case(supabase, case_ref)) or {}

    # Composite title preferring DB row but falling back to URA fields.
    title_parts = [
        row.get("court") or ura.court or "",
        row.get("case_number") or ura.case_number or "",
        row.get("date_hijri") or ura.date_hijri or "",
    ]
    composite = " | ".join(p for p in (s.strip() for s in title_parts) if p)
    title = composite or ura.title or "قضية"

    # What the reader gets: the ملخص. ``ura.case_content`` is itself
    # ``cases.summary`` clipped to 6k by the case_search adapter (see
    # CaseURAResult), so it belongs in this chain, not in the raw-body one — it
    # covers the case where the row fetch missed on a live URA.
    # strip_pipeline_sections: ~16.5k `cases.summary` rows end in the pipeline's
    # resolver-telemetry appendix (internal reg/chunk ids + match scores) and 252
    # in the classifier's crash dump («## classification_error» +
    # `ConnectError: … getaddrinfo failed`). This popup is user-facing, so
    # neither may render — and the strip also covers stored URAs whose
    # `case_content` was persisted before the publish-time strip existed.
    summary = strip_pipeline_sections(
        (row.get("summary") or row.get("short_summary") or ura.case_content or "").strip()
    )
    # The raw ruling rides along ONLY when there is no summary to show. Shipping
    # both would send several unrendered KB per reveal, and the ruling text is
    # exactly the PDPL-sensitive payload the /judgments wing is noindexed over.
    case_body = "" if summary else (row.get("content") or "").strip()

    return CaseSourceView(
        title=title,
        summary=summary,
        content=case_body,
        details_url=row.get("details_url", "") or "",
    )


async def _build_service_view(
    supabase: SupabaseClient, ura: ComplianceURAResult
) -> ServiceSourceView:
    """Resolve a ``ComplianceURAResult`` -> ``ServiceSourceView``.

    Still fetches ``services`` by ``service_ref``: the row is the authority on
    the canonical name and link, and the URA result carries them only when it
    came straight from the compliance adapter (``references_service`` rebuilds
    a shell without them). Falls back to the URA on a lookup miss.

    ``ura.url`` — the المنصة الوطنية portal link — is no longer a fallback for
    ``service_url``. A service with no ``service_url`` now renders exit-less
    rather than sending the reader to a portal home page that never mentions it.
    """
    row: dict = {}
    if ura.service_ref:
        row = await _fetch_service_by_ref(supabase, ura.service_ref) or {}

    return ServiceSourceView(
        title=row.get("service_name_ar") or ura.service_name or "",
        service_url=(row.get("service_url") or ura.service_url or "").strip(),
    )


def _circular_entity_name(row: Any) -> str:
    """Issuing entity name from an embedded ``entities`` object (dict or list).

    Rides in via the ``circulars_entity_id_fkey`` PostgREST embed (a to-one
    object; a list is tolerated defensively). Mirrors reg_search's helper.
    """
    ent = (row or {}).get("entities") if isinstance(row, dict) else None
    if isinstance(ent, dict):
        return (ent.get("entity_name") or "").strip()
    if isinstance(ent, list) and ent and isinstance(ent[0], dict):
        return (ent[0].get("entity_name") or "").strip()
    return ""


async def _build_circular_view(
    supabase: SupabaseClient, ura: CircularURAResult
) -> CircularSourceView:
    """Resolve a ``CircularURAResult`` -> ``CircularSourceView`` (D11).

    The URA shell carries only the 4k-capped aggregator content, so we always
    fetch ``circulars`` by id to pull the UNCAPPED full body for the user popup
    (the 168k outlier lives here — the panel scrolls it, we never truncate).
    Falls back to the shell's own fields when the lookup misses (the capped body
    is the last resort so the popup is never empty). Lazy by construction: this
    fetch only runs when a source view is requested, exactly like case/service.
    """
    circ_id = _parse_simple_ref_id("circular", ura.ref_id)
    row: dict = {}
    if circ_id:
        row = (await _fetch_circular_by_id(supabase, circ_id)) or {}

    return CircularSourceView(
        circ_ref=(row.get("circ_ref") or ura.circ_ref or "").strip(),
        title=(row.get("title") or ura.title or "").strip(),
        entity_name=(_circular_entity_name(row) or ura.entity_name or "").strip(),
        # FULL body (uncapped). Capped shell content is the last-resort fallback.
        content=(row.get("content") or ura.content or "").strip(),
        url=(row.get("source") or ura.source_url or "").strip(),
    )


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def build_source_view(
    supabase: SupabaseClient,
    ura_result: URAResult,
) -> SourceView:
    """Resolve a URA result into a click-ready ``SourceView`` via Supabase lookups.

    Args:
        supabase: Sync supabase-py client (driven via ``asyncio.to_thread``).
        ura_result: One of ``RegURAResult`` | ``ComplianceURAResult`` | ``CaseURAResult``.

    Returns:
        A discriminated-union ``SourceView`` instance, ready to JSON-serialize
        and ship to the frontend artifact popup.

    Raises:
        TypeError: When ``ura_result`` is not one of the three URA result types.
    """
    if isinstance(ura_result, RegURAResult):
        return await _build_reg_view(supabase, ura_result)
    if isinstance(ura_result, CaseURAResult):
        return await _build_case_view(supabase, ura_result)
    if isinstance(ura_result, ComplianceURAResult):
        return await _build_service_view(supabase, ura_result)
    if isinstance(ura_result, CircularURAResult):
        return await _build_circular_view(supabase, ura_result)
    raise TypeError(
        f"build_source_view: unsupported URA result type {type(ura_result).__name__}"
    )


__all__ = [
    "ChunkSourceView",
    "ArticleSourceView",
    "SectionSourceView",
    "RegulationSourceView",
    "CaseSourceView",
    "ServiceSourceView",
    "CircularSourceView",
    "SourceView",
    "build_source_view",
]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    """Smoke-test dispatch with a stub Supabase client (no network).

    Run with::

        python -m agents.deep_search_v4.source_viewer
    """
    import asyncio as _asyncio

    class _StubResp:
        def __init__(self, data):
            self.data = data

    class _StubChain:
        """Records the table being queried and returns canned rows."""

        def __init__(self, fixtures: dict[str, dict]):
            self._fixtures = fixtures
            self._table = ""

        def table(self, name):
            self._table = name
            return self

        def select(self, *_a, **_kw):
            return self

        def eq(self, *_a, **_kw):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return _StubResp(self._fixtures.get(self._table))

    fixtures = {
        "cases": {
            "id": "case-1",
            "court": "محكمة الاستئناف",
            "case_number": "1234",
            "date_hijri": "1445/06/01",
            "details_url": "https://sjp.gov.sa/case/1",
            "summary": "## الملخص\nنزاع تجاري حول رسوم الخدمة.",
            "short_summary": "نزاع تجاري حول رسوم الخدمة.",
            "content": "النص الكامل للحكم.",
        },
        "services": {
            "service_ref": "svc-abc",
            "service_name_ar": "خدمة كذا",
            "url": "https://my.gov.sa/national",
            "service_url": "https://entity.gov.sa/svc",
        },
        "circulars": {
            "id": "circ-1",
            "circ_ref": "ت/123",
            "title": "تعميم بشأن كذا",
            "content": "النص الكامل للتعميم بدون اقتطاع.",
            "source": "https://gov.sa/circular/123",
            "entities": {"entity_name": "وزارة التجارة"},
        },
    }
    stub = _StubChain(fixtures)

    async def _run():
        # 1) reg chunk (URA v3.0 -- fully URA-sourced, no DB call)
        chunk = RegURAResult(
            ref_id="reg:550e8400-e29b-41d4-a716-446655440000",
            source_type="reg_chunk",
            relevance="high",
            reg_title="نظام الأحوال الشخصية",
            chunk_content="نص المقطع الكامل",
            chunk_context="سياق المقطع",
            landing_url="https://laws.boe.gov.sa/...",
            pdf_url="https://files/x.pdf",
        )
        v = await build_source_view(stub, chunk)
        assert isinstance(v, ChunkSourceView), v
        assert v.regulation_source_url.startswith("https://")
        assert "نص المقطع الكامل" in v.content and "سياق المقطع" in v.content
        # The URA still CARRIES pdf_url (it is corpus metadata); the view must
        # not project it — the popup has no PDF exit any more.
        assert not hasattr(v, "regulation_pdf_link")

        # 2) case
        case = CaseURAResult(
            ref_id="case:case-1",
            source_type="case",
            relevance="high",
            title="قضية",
        )
        v = await build_source_view(stub, case)
        assert isinstance(v, CaseSourceView), v
        assert "محكمة الاستئناف" in v.title
        assert v.details_url.endswith("/1")
        # The popup gets the ملخص; the raw ruling stays behind /judgments.
        assert v.summary.startswith("## الملخص")
        assert v.content == "", v.content

        # 3) gov_service
        svc = ComplianceURAResult(
            ref_id="compliance:abcdef0123456789",
            source_type="gov_service",
            relevance="medium",
            service_name="خدمة كذا",
            service_ref="svc-abc",
            service_url="https://entity.gov.sa/svc",
        )
        v = await build_source_view(stub, svc)
        assert isinstance(v, ServiceSourceView), v
        assert v.title == "خدمة كذا"
        assert v.service_url == "https://entity.gov.sa/svc"
        # Title + link and nothing else: no body, and no portal fallback exit.
        assert not hasattr(v, "steps")
        assert not hasattr(v, "national_platform_url")

        # 4) circular — FULL uncapped content + entity + source link
        circ = CircularURAResult(
            ref_id="circular:circ-1",
            source_type="circular",
            relevance="high",
            title="عنوان احتياطي",
            content="محتوى مقتطع احتياطي",
        )
        v = await build_source_view(stub, circ)
        assert isinstance(v, CircularSourceView), v
        assert v.source_type == "circular"
        assert v.content == "النص الكامل للتعميم بدون اقتطاع."
        assert v.entity_name == "وزارة التجارة"
        assert v.circ_ref == "ت/123"
        assert v.url == "https://gov.sa/circular/123"

        # ref_id parser edge cases
        assert _parse_reg_ref_id("") == ("", "")
        assert _parse_reg_ref_id("reg:abc") == ("", "abc")
        assert _parse_reg_ref_id("reg:section:sec-1") == ("section", "sec-1")
        assert _parse_simple_ref_id("case", "case:xyz") == "xyz"
        assert _parse_simple_ref_id("case", "reg:xyz") == ""

        print("source_viewer self-test: OK (4 variants + ref_id parsers)")

    _asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    _self_test()
