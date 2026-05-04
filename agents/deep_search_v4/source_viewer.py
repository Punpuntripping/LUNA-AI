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

Five view variants are produced, one per source type the user can click:

- ``ArticleSourceView``    -- a single article, full content + parent reg link.
- ``SectionSourceView``    -- a section, summary + context + parent reg link.
- ``RegulationSourceView`` -- the regulation itself; only the source URL /
  PDF link matter.
- ``CaseSourceView``       -- a court ruling; one ``details_url`` plus a
  human-readable composite title.
- ``ServiceSourceView``    -- a government service; both the national-platform
  URL (``services.url``) and the service URL (``services.service_url``).

The ref_id formats produced by the three executor adapters are::

    reg_search/adapter.py        ->  ``reg:<uuid>``        (the article OR
                                     section OR regulation row id; type is
                                     disambiguated via ``RegURAResult.source_type``)
    case_search/adapter.py       ->  ``case:<uuid>``       (the cases.id)
    compliance_search/adapter.py ->  ``compliance:<sha1>`` (16-char hash; the
                                     real lookup key is ``ComplianceURAResult.service_ref``,
                                     not the ref_id itself)

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

from agents.deep_search_v4.ura.schema import (
    CaseURAResult,
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


class ArticleSourceView(BaseModel):
    """Click-ready payload for a regulation **article**."""

    source_type: Literal["article"] = "article"
    title: str
    article_num: str | None = None
    content: str = ""
    """Full article content (``articles.content``)."""
    regulation_title: str = ""
    regulation_source_url: str = ""
    """Parent regulation's ``source_url`` -- main click target when present."""
    regulation_pdf_link: dict | None = None
    """Fallback link object when ``source_url`` is empty.

    Shape mirrors what ``regulations.pdf_link`` stores -- typically
    ``{"url": "...", "filename": "...", ...}``.
    """


class SectionSourceView(BaseModel):
    """Click-ready payload for a regulation **section**."""

    source_type: Literal["section"] = "section"
    title: str
    content: str = ""
    """Section content -- ``section_summary`` + ``section_context`` concatenated
    (with a blank line separator) when both are present."""
    regulation_title: str = ""
    regulation_source_url: str = ""
    regulation_pdf_link: dict | None = None


class RegulationSourceView(BaseModel):
    """Click-ready payload for a **regulation** itself.

    Only ``source_url`` (or ``pdf_link`` as fallback) is meaningful at the UI
    layer; ``title`` is rendered as a label.
    """

    source_type: Literal["regulation"] = "regulation"
    title: str = ""
    source_url: str = ""
    pdf_link: dict | None = None


class CaseSourceView(BaseModel):
    """Click-ready payload for a **court case**."""

    source_type: Literal["case"] = "case"
    title: str
    """Composite label: ``court | case_number | date_hijri`` (parts dropped if
    empty / pipe-separated)."""
    details_url: str = ""


class ServiceSourceView(BaseModel):
    """Click-ready payload for a **government service**."""

    source_type: Literal["gov_service"] = "gov_service"
    title: str
    """Service name in Arabic (``services.service_name_ar``)."""
    national_platform_url: str = ""
    """``services.url`` -- shown as "المنصة الوطنية" in the UI."""
    service_url: str = ""
    """``services.service_url`` -- shown as "رابط الخدمة" in the UI."""


SourceView = Annotated[
    Union[
        ArticleSourceView,
        SectionSourceView,
        RegulationSourceView,
        CaseSourceView,
        ServiceSourceView,
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


async def _fetch_article(supabase: SupabaseClient, article_id: str) -> dict | None:
    def _call() -> dict | None:
        try:
            resp = (
                supabase.table("articles")
                .select("id, title, content, article_num, regulation_id")
                .eq("id", article_id)
                .maybe_single()
                .execute()
            )
            return resp.data if resp else None
        except Exception as e:
            logger.debug("source_viewer: fetch article %s failed: %s", article_id, e)
            return None

    return await asyncio.to_thread(_call)


async def _fetch_section(supabase: SupabaseClient, section_id: str) -> dict | None:
    def _call() -> dict | None:
        try:
            resp = (
                supabase.table("sections")
                .select(
                    "id, title, section_summary, section_context, regulation_id"
                )
                .eq("id", section_id)
                .maybe_single()
                .execute()
            )
            return resp.data if resp else None
        except Exception as e:
            logger.debug("source_viewer: fetch section %s failed: %s", section_id, e)
            return None

    return await asyncio.to_thread(_call)


async def _fetch_regulation(supabase: SupabaseClient, regulation_id: str) -> dict | None:
    def _call() -> dict | None:
        try:
            resp = (
                supabase.table("regulations")
                .select("id, title, source_url, pdf_link")
                .eq("id", regulation_id)
                .maybe_single()
                .execute()
            )
            return resp.data if resp else None
        except Exception as e:
            logger.debug(
                "source_viewer: fetch regulation %s failed: %s", regulation_id, e
            )
            return None

    return await asyncio.to_thread(_call)


async def _fetch_case(supabase: SupabaseClient, case_ref: str) -> dict | None:
    """Look up a case by its human-readable ``case_ref`` (text), not its UUID.

    URA ``ref_id`` for cases encodes ``case:<case_ref>`` (see
    ``case_search/unfold_ura.py::_build_reranked_case_result`` where ``db_id``
    is set to ``full_row['case_ref']``). Filtering ``cases.id`` (uuid) with a
    ``case_ref`` value returns PostgREST 400. Always filter by ``case_ref``.
    """
    def _call() -> dict | None:
        try:
            resp = (
                supabase.table("cases")
                .select(
                    "id, court, court_level, city, case_number, "
                    "judgment_number, date_hijri, details_url"
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
    def _call() -> dict | None:
        try:
            resp = (
                supabase.table("services")
                .select("service_ref, service_name_ar, url, service_url")
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


# ---------------------------------------------------------------------------
# Per-domain builders
# ---------------------------------------------------------------------------


def _normalize_pdf_link(raw: Any) -> dict | None:
    """Return a dict-shaped pdf_link or ``None``.

    ``regulations.pdf_link`` is jsonb. Defensively coerce string variants to
    a one-key dict so the frontend always sees the same shape.
    """
    if isinstance(raw, dict) and raw:
        return raw
    if isinstance(raw, str) and raw.strip():
        return {"url": raw.strip()}
    return None


async def _build_reg_view(
    supabase: SupabaseClient, ura: RegURAResult
) -> ArticleSourceView | SectionSourceView | RegulationSourceView:
    """Dispatch a ``RegURAResult`` to the right SourceView variant.

    Sub-kind resolution order:
      1. ``reg:<kind>:<uuid>`` extended ref_id (if the adapter ever emits it).
      2. ``RegURAResult.source_type`` (current adapter only sets ``article``
         or ``section``).
      3. Fall back to ``"article"``.
    """
    sub_kind, db_id = _parse_reg_ref_id(ura.ref_id)
    if not sub_kind:
        sub_kind = (ura.source_type or "").strip().lower() or "article"

    # ---- regulation ----------------------------------------------------
    if sub_kind == "regulation":
        reg_row: dict = {}
        if db_id:
            reg_row = (await _fetch_regulation(supabase, db_id)) or {}
        return RegulationSourceView(
            title=reg_row.get("title") or ura.regulation_title or ura.title,
            source_url=reg_row.get("source_url", "") or "",
            pdf_link=_normalize_pdf_link(reg_row.get("pdf_link")),
        )

    # ---- section -------------------------------------------------------
    if sub_kind == "section":
        sec_row: dict = {}
        if db_id:
            sec_row = (await _fetch_section(supabase, db_id)) or {}

        # Compose full content -- summary + context, blank line separated.
        summary = (sec_row.get("section_summary") or ura.section_summary or "").strip()
        context = (sec_row.get("section_context") or ura.article_context or "").strip()
        if summary and context:
            content = f"{summary}\n\n{context}"
        else:
            content = summary or context or (ura.content or "")

        # Parent regulation lookup for source_url + pdf_link.
        reg_row = {}
        reg_id = sec_row.get("regulation_id")
        if reg_id:
            reg_row = (await _fetch_regulation(supabase, reg_id)) or {}

        return SectionSourceView(
            title=sec_row.get("title") or ura.title,
            content=content,
            regulation_title=reg_row.get("title") or ura.regulation_title or "",
            regulation_source_url=reg_row.get("source_url", "") or "",
            regulation_pdf_link=_normalize_pdf_link(reg_row.get("pdf_link")),
        )

    # ---- article (default) --------------------------------------------
    art_row: dict = {}
    if db_id:
        art_row = (await _fetch_article(supabase, db_id)) or {}

    reg_row = {}
    reg_id = art_row.get("regulation_id")
    if reg_id:
        reg_row = (await _fetch_regulation(supabase, reg_id)) or {}

    article_num = art_row.get("article_num")
    if article_num is not None:
        article_num = str(article_num)
    else:
        article_num = ura.article_num

    return ArticleSourceView(
        title=art_row.get("title") or ura.title,
        article_num=article_num,
        content=art_row.get("content") or ura.content or "",
        regulation_title=reg_row.get("title") or ura.regulation_title or "",
        regulation_source_url=reg_row.get("source_url", "") or "",
        regulation_pdf_link=_normalize_pdf_link(reg_row.get("pdf_link")),
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

    return CaseSourceView(
        title=title,
        details_url=row.get("details_url", "") or "",
    )


async def _build_service_view(
    supabase: SupabaseClient, ura: ComplianceURAResult
) -> ServiceSourceView:
    """Resolve a ``ComplianceURAResult`` -> ``ServiceSourceView``.

    The URA already carries ``service_url``; only ``services.url`` (the
    national platform link) needs a Supabase lookup -- and we look up by
    ``service_ref``, not by the hashed ref_id.
    """
    national_platform_url = ""
    if ura.service_ref:
        row = await _fetch_service_by_ref(supabase, ura.service_ref) or {}
        national_platform_url = row.get("url", "") or ""

    return ServiceSourceView(
        title=ura.title,
        national_platform_url=national_platform_url,
        service_url=ura.service_url or "",
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
    raise TypeError(
        f"build_source_view: unsupported URA result type {type(ura_result).__name__}"
    )


__all__ = [
    "ArticleSourceView",
    "SectionSourceView",
    "RegulationSourceView",
    "CaseSourceView",
    "ServiceSourceView",
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
        "articles": {
            "id": "art-1",
            "title": "مادة 51",
            "content": "نص المادة الكامل",
            "article_num": "51",
            "regulation_id": "reg-1",
        },
        "sections": {
            "id": "sec-1",
            "title": "الباب الأول",
            "section_summary": "ملخص الفصل",
            "section_context": "سياق الفصل",
            "regulation_id": "reg-1",
        },
        "regulations": {
            "id": "reg-1",
            "title": "نظام الأحوال الشخصية",
            "source_url": "https://laws.boe.gov.sa/...",
            "pdf_link": {"url": "https://files/x.pdf"},
        },
        "cases": {
            "id": "case-1",
            "court": "محكمة الاستئناف",
            "case_number": "1234",
            "date_hijri": "1445/06/01",
            "details_url": "https://sjp.gov.sa/case/1",
        },
        "services": {
            "service_ref": "svc-abc",
            "service_name_ar": "خدمة كذا",
            "url": "https://my.gov.sa/national",
            "service_url": "https://entity.gov.sa/svc",
        },
    }
    stub = _StubChain(fixtures)

    async def _run():
        # 1) article (bare reg:<uuid> ref_id, sub_kind from source_type)
        art = RegURAResult(
            ref_id="reg:art-1",
            source_type="article",
            title="مادة 51",
            content="snippet",
            relevance="high",
            regulation_title="نظام الأحوال الشخصية",
            article_num="51",
        )
        v = await build_source_view(stub, art)
        assert isinstance(v, ArticleSourceView), v
        assert v.article_num == "51"
        assert v.regulation_source_url.startswith("https://")
        assert v.content == "نص المادة الكامل"

        # 2) section (extended reg:section:<uuid> ref_id form)
        sec = RegURAResult(
            ref_id="reg:section:sec-1",
            source_type="section",
            title="الباب الأول",
            content="snippet",
            relevance="medium",
            regulation_title="نظام الأحوال الشخصية",
        )
        v = await build_source_view(stub, sec)
        assert isinstance(v, SectionSourceView), v
        assert "ملخص الفصل" in v.content and "سياق الفصل" in v.content

        # 3) regulation (extended ref_id)
        reg = RegURAResult(
            ref_id="reg:regulation:reg-1",
            source_type="regulation",
            title="نظام الأحوال الشخصية",
            content="",
            relevance="high",
            regulation_title="نظام الأحوال الشخصية",
        )
        v = await build_source_view(stub, reg)
        assert isinstance(v, RegulationSourceView), v
        assert v.source_url.startswith("https://")

        # 4) case
        case = CaseURAResult(
            ref_id="case:case-1",
            source_type="case",
            title="قضية",
            content="snippet",
            relevance="high",
        )
        v = await build_source_view(stub, case)
        assert isinstance(v, CaseSourceView), v
        assert "محكمة الاستئناف" in v.title
        assert v.details_url.endswith("/1")

        # 5) gov_service
        svc = ComplianceURAResult(
            ref_id="compliance:abcdef0123456789",
            source_type="gov_service",
            title="خدمة كذا",
            content="...",
            relevance="medium",
            service_ref="svc-abc",
            service_url="https://entity.gov.sa/svc",
        )
        v = await build_source_view(stub, svc)
        assert isinstance(v, ServiceSourceView), v
        assert v.national_platform_url == "https://my.gov.sa/national"
        assert v.service_url == "https://entity.gov.sa/svc"

        # ref_id parser edge cases
        assert _parse_reg_ref_id("") == ("", "")
        assert _parse_reg_ref_id("reg:abc") == ("", "abc")
        assert _parse_reg_ref_id("reg:section:sec-1") == ("section", "sec-1")
        assert _parse_simple_ref_id("case", "case:xyz") == "xyz"
        assert _parse_simple_ref_id("case", "reg:xyz") == ""

        print("source_viewer self-test: OK (5 variants + ref_id parsers)")

    _asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    _self_test()
