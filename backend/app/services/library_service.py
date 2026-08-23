"""Public SEO library business logic (Phase 0 sitemap feed + Phase 1 gating).

The public library exposes the corpus as a programmatic reference site. This
module holds the query helpers behind the anon backend endpoints in
``backend/app/api/public_library.py``.

Phase 0 shipped the **sitemap feed** — a paged ``{loc, lastmod}`` list per
section that the frontend's XML sitemap routes consume.

Phase 1 adds the **gating engine** — the single decision point that says whether
a given library item is ``'open'`` (full bytes ship to anon) or ``'gated'``
(truncated). The gate resolver, section-default cache, sidecar reader, and the
pure truncation / circular-threshold / hub-depth helpers all live here so that
truncation happens in exactly ONE place.

GATING SECURITY INVARIANT — READ BEFORE TOUCHING ANY GATE FUNCTION:
    Gated bytes must NEVER leave the server for an anonymous client. Truncation
    is the ONLY gate. There is NO CSS blur, NO client-side hide, NO
    ``display:none`` fallback, NO nosnippet trick — the hidden text is simply not
    present in the payload the anon endpoint returns. The same truncated bytes go
    to Googlebot and to humans (no UA sniffing = no cloaking). Anything that would
    put full gated text in an anon response is a leak, not a styling choice.

Gate state is stored in the ``seo_item_meta`` SIDECAR table (migration 095),
NOT on the corpus tables. The corpus v2 "tables" (``regulations_v2`` etc.) are
VIEWS over the pipeline-owned schema ``regulation_v2`` and get re-ingested — SEO
columns would be clobbered, so all per-item SEO state lives in the sidecar keyed
``(content_type, content_id)``.

All functions are SYNCHRONOUS and are invoked from the route handlers via
``run_db`` / ``asyncio.to_thread`` (same convention as ``blog_service``). The
Supabase client handed in is the service-role client (RLS bypassed) — so every
public-visibility filter is applied EXPLICITLY here; nothing is delegated to
RLS. Unlike ``blog_service.get_public_post`` there are NO per-read side effects
(no view-counter writes): a sitemap crawl must never mutate rows.

All user-facing error messages are Arabic.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
import urllib.parse
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from supabase import Client as SupabaseClient

# ``cases.summary`` — the «ملخص ريحان» served on the judgment page — is
# pipeline-owned and ~16.5k rows carry a trailing internal appendix (resolver ids,
# a classification crash dump). EVERY consumer must strip it at render; see the
# module docstring of ``case_summary``. This is that rule applied to the library.
from agents.deep_search_v4.shared.case_summary import strip_pipeline_sections
from backend.app.errors import LunaHTTPException, ErrorCode
from backend.app.services import search_service
from shared.config import get_settings
from shared.library.courts import COURT_ORDER, COURT_VARIANTS, slug_for_court
from shared.library.entities import ENTITY_ORDER
from shared.library.entities import slug_for_name as slug_for_provider
from shared.library.sectors import SECTOR_SLUGS, slug_for_sector
from shared.library.case_sources import entity_name as _judgment_entity_name
from shared.library.case_sources import judgment_provenance
from shared.seo.judgment_naming import (
    court_level_label,
    hijri_year,
    judgment_display_title,
    judgment_subject,
)

logger = logging.getLogger(__name__)

# One sitemap page holds up to this many URLs. MUST NOT exceed 1,000: Supabase
# PostgREST clamps any response to max-rows=1000, so a larger page size silently
# returns 1,000 rows while total_pages is computed from the larger divisor —
# every page past the clamp gets DROPPED from the sitemap (caught 2026-07-23:
# regulations served 1,000 of 3,373). The frontend XML route iterates all pages.
SITEMAP_PAGE_SIZE = 1000

# Hardcoded public marketing / legal routes that are always in the sitemap.
# lastmod is intentionally None — these are hand-maintained pages with no
# per-row timestamp; the XML layer simply omits <lastmod> for them.
_STATIC_PATHS = ("/", "/pricing", "/terms", "/privacy", "/audiences", "/blog")

# --- Gating engine tunables (Phase 1) -------------------------------------
# Default free-text budget for a gated body before truncation kicks in. مادة /
# form / judgment bodies pass their own free_chars where the template differs.
GATE_FREE_CHARS_DEFAULT = 400
# Free-text budget for a gated مادة body (Phase 3). A مادة is short + high-value,
# so the gated preview is a touch more generous than the section default.
ARTICLE_FREE_CHARS = 500
# Free-text budget for the شرح TEASER on a مادة page (gate #3 — the AI شرح is the
# scarce value-add and is ALWAYS gated, even on an open-tier مادة whose نص is
# free). Only this many chars of the cached sharh_md ever reach an anon payload;
# the full شرح is served only to ENTITLED callers via /library/full/article.
#
# ⚠ "AUTHED" WAS NEVER ENOUGH, and this comment said "authed" until 2026-08-07.
# Saying it cost the entire شرح corpus: ``resolve_access`` returned free the
# moment the gate read ``'open'``, and step (b) of ``resolve_gate`` makes a مادة
# inherit its parent نظام's tier — under which ALL 229 of 229 مواد that carry a
# شرح sit. So every registered account read 100% of the شرح free and unmetered
# (security review 2026-08-07, H-5). The two halves of gate #3 now read:
#   * anon    → this teaser and nothing else (``_sharh_teaser``).
#   * authed  → ``get_full_article(include_sharh=...)``, whose flag is
#               ``AccessDecision.is_entitled`` and NOTHING else. An open-tier نص
#               stays free; the شرح layered on top of it is bought, always.
SHARH_TEASER_CHARS = 170
# A gated circular whose body is <= this many chars renders fully (open): a
# 4-line تعميم that is 90% placeholder bars looks broken and adds no gate value.
CIRCULAR_FREE_LENGTH = 800
# Hub browse depth BY TIER (access-tiers plan §4.5 / D12). Page N+1 returns the
# CTA wall. Server-enforced in the hub list endpoints ("browse the full library"
# is an account feature); deep directory pages have ~zero SEO value anyway, so
# discovery rides the sitemap + internal mesh rather than pagination.
#
# ⚠ ANON WAS 3 UNTIL 2026-07-27 and is now 1 — a deliberate TIGHTENING (plan
# §4.5: "note this tightens anon from today's effective 3 pages to 1 — accepted").
# Anything that used to read ANON_HUB_MAX_PAGE as "the authenticated cap too" is
# now wrong: a signed-in free account gets FREE_HUB_MAX_PAGE, paid is unbounded.
ANON_HUB_MAX_PAGE = 1
FREE_HUB_MAX_PAGE = 3
# One placeholder bar stands in for ~this many hidden characters; the visible
# bar count is capped so a 40k-char gated doc doesn't render a wall of bars.
_PLACEHOLDER_CHARS_PER_LINE = 90
_MAX_PLACEHOLDER_LINES = 30

# The recognised library content types (mirrors the seo_item_meta CHECK
# constraint in migration 095). 'service' is the only fail-OPEN type.
#
# ``compliance`` is the /compliance wing's own sidecar type (service GUIDES —
# see the wing's block comment below), keyed by ``service_guides.id``. It is a
# DIFFERENT key space from the legacy ``service`` rows, which are keyed by
# ``services.id`` and are stale leftovers of the retired wing: never read, never
# written, never reused.
_CONTENT_TYPES = (
    "regulation", "article", "judgment", "circular", "service", "form", "compliance",
)

# In-process TTL cache for seo_gate_defaults. The policy table has one row per
# content_type and changes rarely (operator edit), so a ~5-minute cache spares
# every gate resolution a round-trip. Module-level; safe under the sync
# service-function model (functions run in worker threads via run_db, and a torn
# read here at worst returns a slightly-stale-but-consistent dict).
_GATE_DEFAULTS_TTL_SECONDS = 300.0
_gate_defaults_cache: dict[str, Any] = {"value": None, "expires_at": 0.0}

# --- Stage-1 "sample mode" pagination (circulars + services only) ---------
# During the stage-1 rollout only a small SAMPLE of a wing is slugged
# (``seo_item_meta.slug NOT NULL``) — e.g. 100 of 1,843 circulars. A hub that
# paginates the FILTERED CORPUS and drops unslugged rows via ``_slug_map`` is
# correct once every corpus row is slugged, but during the sample it returns
# EMPTY pages: the corpus's first pages hold none of the 100 published rows
# (caught 2026-07-23: /regulations?page=1 → 0 items, total_pages=375).
#
# ``_published_ids`` detects the sample: when a wing's published-id count is
# ``<= SAMPLE_MODE_MAX_IDS`` the hub lister paginates over the PUBLISHED ids
# (fetched from the sidecar) instead of the corpus, so every page is full and
# ``total_pages`` is EXACT. Above the ceiling the wing is in full-corpus steady
# state and the lister keeps its legacy corpus-pagination path untouched.
#
# ⚠ THIS CEILING NO LONGER APPLIES TO /regulations OR /judgments — AT ALL.
# Both wings now have a published-only RANKED VIEW (`library_regulations_ranked`,
# migration 116; `library_judgments_ranked`, migration 123) and a published-only
# counts RPC (`library_sector_counts_published`, migration 124). For them
# "published" is a property of the RELATION rather than a post-filter, so there
# is no sample to detect and no ceiling to cross: their listers, their page
# counts and their sector counts all read the view / the published RPC at any
# corpus size. ``_RANKED_HUB_VIEWS`` is the switch, and
# ``_published_sample_counts`` short-circuits on it.
#
# WHAT IS LEFT: circulars (100 published) and compliance (169 service guides,
# the whole wing), which have no ranked view. They are the only callers of
# ``_published_ids`` now. Compliance is a 169-row corpus by construction — one
# guide per guided service — so it sits far below the ceiling and stays there;
# if it ever grows past it, give it a ranked view, do not raise the constant.
#
# The ceiling was raised 300 → 1000 on 2026-08-06 when /regulations went to 462
# published rows, as a stopgap for exactly the failure the views fix: crossing it
# flipped a wing's counts onto the CORPUS path, where a sector reporting 695 rows
# of which 0 are servable gets prerendered as a static, indexable, EMPTY page (the
# soft-404-at-scale failure documented at CROSS-WING COUNTS below). Publishing
# ~10,000 judgments and 1,188 regulations blows straight through 1000, which is
# why the stopgap was replaced rather than raised again. Do NOT wire a third wing
# onto this constant to buy time — give it a ranked view instead.
SAMPLE_MODE_MAX_IDS = 1000

# In-process TTL cache for the per-content_type published-id list — one entry per
# content_type, each ``{"value": list|None, "expires_at": float}`` (same shape as
# ``_gate_defaults_cache``). ~60s: short enough that a fresh publish surfaces
# within a minute, long enough to spare every hub page the multi-round-trip
# sidecar scan. A cached ``value`` of ``None`` is the legitimate steady-state
# answer (NOT "uncached") — freshness is decided by ``expires_at`` alone.
_PUBLISHED_IDS_TTL_SECONDS = 60.0
_published_ids_cache: dict[str, dict[str, Any]] = {}

__all__ = [
    "SITEMAP_PAGE_SIZE",
    "sitemap_blog_urls",
    "sitemap_static_urls",
    # Phase 1 — gating engine
    "GATE_FREE_CHARS_DEFAULT",
    "CIRCULAR_FREE_LENGTH",
    "ANON_HUB_MAX_PAGE",
    "FREE_HUB_MAX_PAGE",
    "get_gate_defaults",
    "get_item_meta",
    "resolve_gate",
    "truncate_for_gate",
    "effective_circular_gate",
    # The exposure budget — gate on a fraction of the document, not per section
    "GateBudget",
    "MIN_WITHHELD_RATIO",
    "MIN_WITHHELD_CHARS",
    "free_budget",
    "gate_decision",
    "spend_budget_across_sections",
    "hub_page_allowed",
    # Phase 2 — content endpoints (/regulations)
    "HUB_PAGE_SIZE",
    "REG_STATUS_MAP",
    "map_reg_status",
    "DOC_TYPE_BUCKET_LABELS",
    "map_doc_type_bucket",
    "list_regulations_hub",
    "regulations_hub_total_pages",
    "get_regulation_doc",
    # /compliance — the service-guides wing (ungated, SEO)
    "COMPLIANCE_WING_READY",
    "list_compliance_hub",
    "compliance_hub_total_pages",
    "compliance_entity_counts",
    "get_compliance_guide",
    # Phase 3 — مادة (article) pages
    "ARTICLE_FREE_CHARS",
    "SHARH_TEASER_CHARS",
    "get_regulation_article",
    "sitemap_article_urls",
    # Authed full-content (the signup promise)
    "get_full_regulation",
    "get_full_article",
    "get_full_circular",
    "get_full_form",
    # Phase 5 — /circulars
    "list_circulars_hub",
    "circulars_hub_total_pages",
    "get_circular_doc",
    # Phase 5 — /judgments
    "JUDGMENT_CITED_FREE_LIMIT",
    "JUDGMENT_BUDGET",
    "list_judgments_hub",
    "judgments_hub_total_pages",
    "court_counts",
    "get_judgment_doc",
    "get_full_judgment",
    # Phase 3 — /forms (نماذج)
    "FORM_BODY_FREE_CHARS",
    "FORM_CATEGORIES",
    "list_forms_hub",
    "forms_hub_total_pages",
    "get_form_detail",
    "open_form_in_writer",
    "sitemap_forms_urls",
    # library_sectors.md Phase 1 — the sector axis (§5 · §7.2 · §7.3)
    "SECTOR_COUNT_SECTIONS",
    "library_corpus_counts",
    "sector_counts",
    # read_next_related_items.md §5 — «اقرأ تاليًا» + «الأنظمة المذكورة»
    "RELATED_NEXT_LIMIT",
    "REGULATION_CITED_LIMIT",
    "get_related_next",
]


def _join(base_url: str, path: str) -> str:
    """Join a trailing-slash-free base with an absolute path.

    ``base_url`` already has no trailing slash (the config validator strips it),
    so ``f"{base}{path}"`` yields e.g. ``https://rayhanai.com/pricing`` and, for
    the root path ``/``, ``https://rayhanai.com/``.
    """
    return f"{base_url}{path}"


def sitemap_static_urls(base_url: str) -> list[dict[str, Any]]:
    """The hardcoded static-page URL set for the ``static`` sitemap section.

    Returns ``[{"loc": <absolute url>, "lastmod": None}, ...]``. No DB access.
    """
    return [{"loc": _join(base_url, path), "lastmod": None} for path in _STATIC_PATHS]


def sitemap_blog_urls(
    supabase: SupabaseClient,
    base_url: str,
    page: int = 1,
    page_size: int = SITEMAP_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch one page of public blog-post URLs for the ``blog`` sitemap section.

    Selects published, PUBLIC, non-deleted ``blog_posts`` (the same gallery
    visibility rule the anon ``/public/blogs`` gallery uses), newest first, and
    projects each into ``{"loc": "{base}/blog/{token}", "lastmod": <iso>}`` where
    ``lastmod`` = ``updated_at`` (falling back to ``created_at``).

    Returns ``(urls, total_pages)``. ``page`` is 1-based and clamped to ``>= 1``;
    a page past the end yields an empty ``urls`` list with the real
    ``total_pages`` (never < 1). Read-only — no ``view_count`` bump.
    """
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or SITEMAP_PAGE_SIZE))
    offset = (page - 1) * page_size

    try:
        result = (
            supabase.table("blog_posts")
            .select("token, updated_at, created_at", count="exact")
            .eq("is_public", True)
            .eq("is_published", True)
            .is_("deleted_at", "null")
            .order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error building blog sitemap feed: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب خريطة الموقع",
        )

    total = int(result.count or 0)
    total_pages = max(1, math.ceil(total / page_size)) if total else 1

    rows = result.data or []
    urls: list[dict[str, Any]] = []
    for row in rows:
        token = row.get("token")
        if not token:
            continue
        lastmod: Optional[str] = row.get("updated_at") or row.get("created_at")
        urls.append({"loc": _join(base_url, f"/blog/{token}"), "lastmod": lastmod})

    return urls, total_pages


def sitemap_library_urls(
    supabase: SupabaseClient,
    base_url: str,
    content_type: str,
    path_prefix: str,
    page: int = 1,
    page_size: int = SITEMAP_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int]:
    """Sitemap feed for a library wing, driven by the ``seo_item_meta`` sidecar.

    Emits one URL per sidecar row of ``content_type`` that has a slug AND is
    flagged ``indexable``. ``loc`` = ``{base}/{path_prefix}/{percent-encoded
    slug}`` (Arabic slugs are percent-encoded for maximally-compatible ``<loc>``
    values), ``lastmod`` = the sidecar ``updated_at``. Same ``(urls,
    total_pages)`` contract as ``sitemap_blog_urls``. Read-only, no side effects.

    ⚠ TWO PREDICATES, TWO QUESTIONS (migration 130). ``slug IS NOT NULL`` means
    the wing can SERVE the page; ``indexable`` means a crawler may HAVE it. They
    were the same thing until judgments needed to differ — all 10,000 published
    rulings stay servable because the court sections paginate over them, while
    only the PDPL-cleared 3,000 belong in a sitemap.

    The ``indexable`` filter is applied to EVERY wing, not special-cased to
    judgments. It is a no-op for the others (migration 130 defaults the column
    true and only judgments were flipped), and applying it universally means the
    next wing that wants a curated index is a data change rather than a code
    change — and, more to the point, that nobody has to remember to add the
    filter when it becomes load-bearing somewhere new.
    """
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or SITEMAP_PAGE_SIZE))
    offset = (page - 1) * page_size

    try:
        result = (
            supabase.table("seo_item_meta")
            .select("slug, updated_at", count="exact")
            .eq("content_type", content_type)
            .not_.is_("slug", "null")
            .eq("indexable", True)
            .order("updated_at", desc=True)
            .order("content_id", desc=False)
            .range(offset, offset + page_size - 1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error building %s sitemap feed: %s", content_type, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب خريطة الموقع",
        )

    total = int(result.count or 0)
    total_pages = max(1, math.ceil(total / page_size)) if total else 1

    urls: list[dict[str, Any]] = []
    for row in result.data or []:
        slug = row.get("slug")
        if not slug:
            continue
        encoded = urllib.parse.quote(slug, safe="")
        urls.append(
            {
                "loc": _join(base_url, f"/{path_prefix}/{encoded}"),
                "lastmod": row.get("updated_at"),
            }
        )
    return urls, total_pages


# ==========================================================================
# PHASE 1 — GATING ENGINE
#
# resolve_gate() is the ONE decision point: it returns 'open' or 'gated' for a
# library item, and the anon endpoints truncate the body accordingly via
# truncate_for_gate(). No other module decides gating. Gated bytes are removed
# from the payload server-side — never hidden with CSS/client tricks.
# ==========================================================================


def get_gate_defaults(supabase: SupabaseClient) -> dict[str, str]:
    """Section-level default gating policy as ``{content_type: 'open'|'gated'}``.

    Reads ``public.seo_gate_defaults`` (seeded in migration 095) through a
    module-level ~5-minute TTL cache — the table has one row per content_type
    and changes only on a rare operator edit, so caching spares every gate
    resolution a DB round-trip.

    Fail-soft: if the query errors, returns the last cached value when one
    exists, otherwise an empty dict. An empty dict makes ``resolve_gate`` fall
    through to its ultimate fail-closed default ('gated', except 'service'), so a
    DB blip never accidentally OPENS a gated item.
    """
    now = time.monotonic()
    cached_value = _gate_defaults_cache.get("value")
    if cached_value is not None and now < _gate_defaults_cache.get("expires_at", 0.0):
        return cached_value

    try:
        result = (
            supabase.table("seo_gate_defaults")
            .select("content_type, default_gate")
            .execute()
        )
        rows = result.data or []
        defaults = {
            r["content_type"]: r["default_gate"]
            for r in rows
            if r.get("content_type") and r.get("default_gate")
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not load seo_gate_defaults (using stale/empty): %s", e)
        # Prefer a stale-but-consistent policy over failing the whole page; an
        # empty dict still resolves safely (fail-closed) downstream.
        return cached_value if cached_value is not None else {}

    _gate_defaults_cache["value"] = defaults
    _gate_defaults_cache["expires_at"] = now + _GATE_DEFAULTS_TTL_SECONDS
    return defaults


def get_item_meta(
    supabase: SupabaseClient, content_type: str, content_id: str
) -> Optional[dict[str, Any]]:
    """Fetch one ``seo_item_meta`` sidecar row, or ``None`` if absent.

    Keyed on the composite PK ``(content_type, content_id)`` (migration 095).
    ``content_id`` is TEXT in the schema; callers pass whatever key shape the
    corpus uses (uuid, reg_ref, derived article key). Returns the row dict
    (``slug``, ``seo_tier``, ``gate_override``, ``updated_at``) or ``None``.

    Read-only, no side effects. A query error is swallowed to ``None`` so a
    sidecar blip degrades to "no override" (→ section default) rather than a 500.
    """
    if not content_type or content_id is None:
        return None
    try:
        result = (
            supabase.table("seo_item_meta")
            .select("content_type, content_id, slug, seo_tier, gate_override, updated_at")
            .eq("content_type", content_type)
            .eq("content_id", str(content_id))
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Could not load seo_item_meta (%s/%s): %s", content_type, content_id, e
        )
        return None
    rows = result.data or []
    return rows[0] if rows else None


def resolve_gate(
    supabase: SupabaseClient,
    content_type: str,
    content_id: str,
    *,
    parent_regulation_id: Optional[str] = None,
) -> str:
    """Resolve an item's gate to ``'open'`` or ``'gated'`` — the ONE gate decision.

    This is the single source of truth for gating; truncation, the GateBanner,
    the PDF proxy, and paywall JSON-LD all key off the value it returns. Gated
    means the body is truncated server-side before it reaches an anon client —
    NEVER hidden with CSS/JS.

    Resolution order (documented in migration 095's header):
      (a) the item's own ``seo_item_meta.gate_override`` if set;
      (b) for ``content_type='article'``: the PARENT regulation's sidecar row —
          its ``gate_override`` if set, else its ``seo_tier`` if set (an article
          of an open-tier regulation renders its public-domain نص free);
      (c) for ``content_type='regulation'``: its own ``seo_tier`` if set;
      (d) ``seo_gate_defaults[content_type]`` (the section policy);
      (e) ultimate fallback ``'gated'`` (fail-closed) — EXCEPT
          ``content_type='service'`` which fails OPEN. Services are
          policy-never-gated: a service citation is revealed free in chat, and
          that reveal is what calls through here.
          ⚠ ``'service'`` IS NOT THE /compliance WING. That wing serves
          ``content_type='compliance'`` — service GUIDES — which never reach this
          function at all: the guides are ungated by design, so nothing resolves
          a gate for them (``get_compliance_guide`` makes no call here).

    ``parent_regulation_id`` is the ``content_id`` of the article's parent
    regulation in ``seo_item_meta`` (content_type ``'regulation'``); when it is
    ``None`` step (b) is skipped and the article falls through to the section
    default. Unknown/invalid gate values in the sidecar are ignored (treated as
    unset) so a bad row can never open a gated item.
    """
    meta = get_item_meta(supabase, content_type, content_id)

    # (a) the item's own hard override wins over everything.
    if meta and meta.get("gate_override") in ("open", "gated"):
        return meta["gate_override"]

    # (b) article inherits from its parent regulation's sidecar.
    if content_type == "article" and parent_regulation_id:
        parent = get_item_meta(supabase, "regulation", parent_regulation_id)
        if parent:
            if parent.get("gate_override") in ("open", "gated"):
                return parent["gate_override"]
            if parent.get("seo_tier") in ("open", "gated"):
                return parent["seo_tier"]

    # (c) regulation uses its own popularity tier.
    if content_type == "regulation" and meta and meta.get("seo_tier") in ("open", "gated"):
        return meta["seo_tier"]

    # (d) section-level default policy.
    default_gate = get_gate_defaults(supabase).get(content_type)
    if default_gate in ("open", "gated"):
        return default_gate

    # (e) ultimate fallback: fail-closed, except a service which fails open.
    return "open" if content_type == "service" else "gated"


def truncate_for_gate(
    text: str, gate: str, *, free_chars: int = GATE_FREE_CHARS_DEFAULT
) -> dict[str, Any]:
    """Cut ``text`` down to the free preview for a gate — the ONLY gate mechanism.

    Pure function, no DB. The hidden remainder is DROPPED from the returned
    payload: gated bytes must NEVER leave the server for an anon client. There is
    no CSS blur / client-side hide — the caller ships only ``visible_text``.

    Returns ``{"visible_text", "is_truncated", "hidden_placeholder_lines"}``:
      - ``gate='open'``  → full text, ``is_truncated=False``, 0 placeholder lines.
      - ``gate='gated'`` → if ``len(text) <= free_chars`` the whole text is
        returned untruncated; otherwise the text is cut at the LAST whitespace
        at/ before ``free_chars`` (never mid-word — falls back to a hard cut only
        when the first ``free_chars`` contain no whitespace at all), trailing
        whitespace stripped. ``hidden_placeholder_lines`` ≈
        ``ceil(hidden_chars / 90)``, capped at 30, for rendering placeholder bars.
    """
    text = text or ""
    free_chars = max(0, int(free_chars))

    if gate != "gated" or len(text) <= free_chars:
        return {
            "visible_text": text,
            "is_truncated": False,
            "hidden_placeholder_lines": 0,
        }

    # Cut at the last whitespace at or before free_chars — never split a word.
    cut_at = next(
        (i for i in range(free_chars - 1, -1, -1) if text[i].isspace()),
        None,
    )
    if cut_at is not None and cut_at > 0:
        visible_text = text[:cut_at].rstrip()
    else:
        # No whitespace in the window (one very long token): hard cut is the only
        # option; honoring "never mid-word" is impossible here.
        visible_text = text[:free_chars].rstrip()

    hidden_chars = len(text) - len(visible_text)
    hidden_placeholder_lines = min(
        _MAX_PLACEHOLDER_LINES,
        math.ceil(hidden_chars / _PLACEHOLDER_CHARS_PER_LINE),
    )
    return {
        "visible_text": visible_text,
        "is_truncated": True,
        "hidden_placeholder_lines": hidden_placeholder_lines,
    }


# --- The exposure budget: gate on a FRACTION of the document ---------------
#
# ⚠ `truncate_for_gate` above is an ABSOLUTE PER-SECTION budget, and that is the
# root cause of the exposure this layer exists to fix. Measured on prod
# 2026-08-10: judgments served 42.0% of the ruling body free (846 of 10,000 at
# ≥90%), circulars 45.6%, and the 877 أنظمة with ≤3 chunks 61.2% — because a
# per-section budget MULTIPLIES by section count and never asks what fraction of
# THIS document it is giving away. Plan: `.claude/plans/gate_exposure_budget.md`.
#
# The replacement is one budget computed ONCE from the document's own length and
# then SPENT across its sections in reading order. Callers must not re-derive it.


@dataclass(frozen=True)
class GateBudget:
    """A wing's exposure dial. ``ratio`` is the policy; the bounds keep it sane.

    ``floor`` exists for SEO — thin content ranks badly, and an over-tight gate
    costs the traffic these pages are published for. ``ceiling`` exists for the
    opposite reason: a 45k-char نظام must not leak 7k just because it is long.
    """

    ratio: float
    floor: int
    ceiling: int


# «gated» has to MEAN something. A document that cannot clear both of these after
# truncation is not being gated — it is being decorated with a paywall — so
# `gate_decision` either cuts it deeper or marks it honestly open.
MIN_WITHHELD_RATIO = 0.5
MIN_WITHHELD_CHARS = 800


def free_budget(total_chars: int, budget: GateBudget) -> int:
    """The document-wide free allowance in characters. Pure, no DB."""
    return min(
        max(round(budget.ratio * max(0, int(total_chars))), budget.floor),
        budget.ceiling,
    )


def gate_decision(
    total_chars: int, gate: str, budget: GateBudget
) -> tuple[str, int]:
    """``(effective_gate, free_chars)`` for a whole document.

    Generalises ``effective_circular_gate``'s hand-tuned ≤800 downgrade into the
    rule every wing shares. An ``'open'`` gate passes through untouched (open
    means open — the caller ships everything). For a ``'gated'`` document:

      1. Take the ratio budget.
      2. If honouring it would withhold less than ``MIN_WITHHELD_RATIO`` of the
         document or less than ``MIN_WITHHELD_CHARS``, cut DEEPER — down to the
         most that can be served while still clearing both floors.
      3. If cutting that deep would leave less than ``budget.floor`` (the
         document is simply too short to gate honestly), return ``'open'`` and
         the whole thing. No CTA, no placeholder bars, full crawl value.

    Step 3 is a deliberate, visible trade: some short items become genuinely
    free. They already were — the only change is that the page stops claiming
    otherwise.
    """
    total = max(0, int(total_chars))
    if gate != "gated" or total == 0:
        return ("open" if gate != "gated" else "gated", total)

    target = free_budget(total, budget)
    # The deepest we may serve while still withholding a real remainder.
    max_servable = min(
        total - MIN_WITHHELD_CHARS,
        int(total * (1.0 - MIN_WITHHELD_RATIO)),
    )
    if max_servable >= target:
        return ("gated", target)
    if max_servable >= budget.floor:
        return ("gated", max_servable)
    return ("open", total)


def spend_budget_across_sections(
    texts: list[str], gate: str, free_chars: int
) -> list[dict[str, Any]]:
    """Spend ONE document budget across sections in reading order.

    Returns one ``truncate_for_gate``-shaped dict per input text. The budget is
    front-loaded deliberately: the opening of a document is its narrative setup —
    the part carrying the search terms that tell a reader whether this is about
    their problem — and the reasoning that follows is what an unlock buys. Once
    the allowance is exhausted, later sections truncate to nothing and render as
    placeholder bars, which is the correct signal that there IS more.

    This replaces "budget × N sections", under which free bytes grew linearly
    with how finely a document happened to be subdivided.
    """
    if gate != "gated":
        return [
            {
                "visible_text": t,
                "is_truncated": False,
                "hidden_placeholder_lines": 0,
            }
            for t in texts
        ]

    remaining = max(0, int(free_chars))
    out: list[dict[str, Any]] = []
    for text in texts:
        cut = truncate_for_gate(text or "", "gated", free_chars=remaining)
        # A truncated section EXHAUSTS the allowance rather than carrying its
        # remainder forward. `truncate_for_gate` cuts at the last whitespace
        # inside the window, so a truncation typically leaves a few unspent
        # chars — carried forward those become a «قصي» stub at the head of the
        # next section: not a preview, just corrupted text under a heading.
        remaining = 0 if cut["is_truncated"] else remaining - len(cut["visible_text"])
        out.append(cut)
    return out


def effective_circular_gate(gate: str, body_len: int) -> str:
    """Downgrade a gated circular to ``'open'`` when its body is short.

    A ``'gated'`` circular whose body is ``<= CIRCULAR_FREE_LENGTH`` (800) chars
    renders fully open — a short تعميم rendered ~90% placeholder bars looks broken
    and gates nothing of value. Any other case returns ``gate`` unchanged (an
    already-``'open'`` circular stays open; a long gated one stays gated).
    Pure function, no DB.
    """
    if gate == "gated" and int(body_len) <= CIRCULAR_FREE_LENGTH:
        return "open"
    return gate


def hub_page_allowed(page: int, tier: str) -> bool:
    """Whether a hub list ``page`` is viewable at ``tier`` — the browse-depth cap.

    ``tier`` ∈ ``'anon'`` | ``'free'`` | ``'paid'`` (D12):

    ==========  ========================================
    tier        max page
    ==========  ========================================
    ``anon``    ``ANON_HUB_MAX_PAGE`` (1)
    ``free``    ``FREE_HUB_MAX_PAGE`` (3)
    ``paid``    unbounded (basic/pro/max/marketing/dev)
    ==========  ========================================

    Page cap+1 returns the «سجّل مجاناً» CTA wall from the list endpoint — a 200
    with ``cap_reached=true`` and no items, the SAME body for humans and for
    Googlebot (no cloaking), never a 4xx. Pure function, no DB.

    ⚠ The SIGNATURE CHANGED on 2026-07-27: it used to take ``is_authed: bool``,
    which every call site passed as a hardcoded ``False`` (PART 9 trap 3 — the
    cap was dead for authenticated users). An unknown tier string is treated as
    ``'anon'``: fail-closed, because the only thing a bad tier can do here is
    hand out more depth than the caller paid for.
    """
    t = (tier or "").strip().lower()
    if t == "paid":
        return True
    if t == "free":
        return int(page) <= FREE_HUB_MAX_PAGE
    return int(page) <= ANON_HUB_MAX_PAGE


# ==========================================================================
# LAYER B — ENTITLEMENT (access-tiers plan, PART 2 + §1.2 + D4/D5/D11)
#
# Layer A (``resolve_gate`` above) answers "is this ITEM gated?" — a property of
# the item, cacheable, tier-free. Layer B answers "may THIS USER unlock THIS
# gated item RIGHT NOW?" and is per-user, so:
#
#   * ``resolve_gate`` MUST NOT gain a user/tier parameter. ``_gate_defaults_cache``
#     and ``_published_ids_cache`` are global and time-keyed; a per-user dimension
#     there poisons them across users (PART 9 trap 1). Layer B exists exactly so
#     that never has to happen.
#   * NOTHING in this block is memoized or cached, and none of it may run in a
#     server component / ISR render. It runs only on endpoints that set
#     ``Cache-Control: private, no-store``.
#   * Layer C (the ledger, ``library_unlocks``) is MONEY: inserted once via
#     ON CONFLICT DO NOTHING, never updated. Behavioural counters live on
#     ``library_items`` (Phase B2) so no page view ever writes to the cost ledger.
#
# The §1.2 access predicate, in one line:
#     access = row exists AND (current plan is paid OR row.period_key = current)
# A paid user reaches every row ever unlocked; a lapsed user reaches only the
# current period's rows while paid-era rows sit FROZEN and intact; re-upgrading
# flips the first clause true and restores the whole shelf at once.
# ==========================================================================

from datetime import datetime                      # noqa: E402

from shared import quota as _quota                 # noqa: E402
from shared.db.run import run_db                   # noqa: E402

# §1.2.1 weighted cost. One unlock must not mean both "a paragraph" and "a
# 716-article statute" — but the MEDIAN نظام (18 مواد) must still cost 1, so the
# common case is unchanged.
UNLOCK_COST_MIN = 1
UNLOCK_COST_MAX = 8
ARTICLES_PER_UNLOCK = 25          # regulation with a TRUSTED seo_articles index
# Everything else (chunk-only, or an index rejected by `article_coverage_is_
# trustworthy`) is weighted by CHUNK COUNT at 1 chunk ≈ 3 مواد. This replaced a
# character-length weighting on 2026-08-07: that one had to page every chunk
# BODY through the wire just to sum lengths, where this is one count().
# ⚠ The cap binds far sooner here — article weighting reaches UNLOCK_COST_MAX at
# 176+ مواد, this reaches it at 59 chunks. Accepted 2026-08-06 (4 of 187
# chunk-priced regulations sit at the cap, the same 4 as before). If it starts
# binding on documents it shouldn't, THIS rate is the lever — not UNLOCK_COST_MAX
# and not the coverage threshold below.
ARTICLES_PER_CHUNK = 3
CHUNKS_PER_UNLOCK = ARTICLES_PER_UNLOCK / ARTICLES_PER_CHUNK   # 25/3 ≈ 8.33

# Article-coverage fallback (2026-08-07). `seo_articles` rows are keyed by
# `article_no`, so a document whose highest article_no far exceeds its row count
# has HOLES — مواد that exist in the نظام and have no row. Rendering that index
# drops them SILENTLY: اللائحة التنفيذية لنظام العمل ج2 shipped «68 مادة» for a
# 232-مادة لائحة and said nothing about the other 164. Past these thresholds the
# chunks are the more honest surface even though they are coarser.
ARTICLE_GAP_MIN_MISSING = 3       # absolute floor — ignore small documents
ARTICLE_GAP_MAX_RATIO = 0.10      # >10% of the document missing → distrust it

# Content types that are never gated and therefore never charged (§1.3): a
# government service is policy-open, so it produces no ledger row at all. What
# this guards is the CHAT reveal of a service citation.
#
# ⚠ ``'compliance'`` (the service-GUIDE wing) is deliberately absent, and its
# absence is not an oversight: an ungated wing never asks. The guide page reads
# through ``get_compliance_guide``, which resolves no gate and charges nothing,
# so no content type for it ever arrives here. Adding one would imply the
# opposite — that something in that wing is unlockable.
NEVER_CHARGED_TYPES = ("service",)

# The one column set Layer B reads off the ledger.
_UNLOCK_COLS = "unlock_id, content_type, content_id, period_key, cost, unlocked_at"

__all__ += [
    # Layer B — entitlement
    "UNLOCK_COST_MIN",
    "UNLOCK_COST_MAX",
    "ARTICLES_PER_UNLOCK",
    "ARTICLES_PER_CHUNK",
    "CHUNKS_PER_UNLOCK",
    "ARTICLE_GAP_MIN_MISSING",
    "ARTICLE_GAP_MAX_RATIO",
    "article_coverage_is_trustworthy",
    "NEVER_CHARGED_TYPES",
    "AccessDecision",
    "unlock_cost",
    "resolve_access",
    "stored_library_count",
    "parent_regulation_of_article",
    "article_has_sharh",
]


def _clamp_cost(value: float) -> int:
    return max(UNLOCK_COST_MIN, min(UNLOCK_COST_MAX, int(value)))


def article_coverage_is_trustworthy(articles: list[dict[str, Any]]) -> bool:
    """Does this ``seo_articles`` index actually cover its document? PURE.

    Rows are keyed by ``article_no``, so the document's apparent length is
    ``max(article_no)`` and anything beyond the row count is a HOLE — a مادة that
    exists in the نظام and has no row. False past both thresholds
    (``ARTICLE_GAP_MIN_MISSING`` **and** ``ARTICLE_GAP_MAX_RATIO``): the caller
    must render from chunks instead.

    ⚠ Gaps are counted from the NUMBERING, never from ``extraction_status`` or
    ``article_text``. On the document this rule was written for
    (``17900_reg_128_p2`` — اللائحة التنفيذية لنظام العمل ج2) EVERY present row is
    healthy: 0 rows are non-``extracted``, 0 have NULL text. The damage is 164
    مواد that are simply absent, so a text-based test scores that page a perfect
    100 and leaves it broken. Do not "improve" this into a content check.

    An empty list returns False, but every caller branches on falsiness first, so
    it never reaches here.
    """
    if not articles:
        return False
    numbers = [int(a.get("article_no") or 0) for a in articles]
    numbers = [n for n in numbers if n > 0]
    if not numbers:
        return False
    apparent_length = max(numbers)
    missing = apparent_length - len(numbers)
    if missing <= ARTICLE_GAP_MIN_MISSING:
        return True
    return (missing / apparent_length) <= ARTICLE_GAP_MAX_RATIO


def _regulation_chunk_count(supabase: SupabaseClient, regulation_id: str) -> int:
    """How many ``chunks_v2`` rows a regulation has. SYNC, read-only.

    A count(), NOT a body scan: the caller only needs the number, and the old
    character-weighting path used to page every chunk's ``content`` through the
    wire to sum lengths. Fail-soft → 0, which callers read as "no chunk surface
    available" and therefore KEEP the article surface (see ``use_article_surface``).
    """
    try:
        res = (
            supabase.table("chunks_v2")
            .select("id", count="exact")
            .eq("regulation_id", str(regulation_id))
            .limit(1)
            .execute()
        )
        return int(getattr(res, "count", None) or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("chunk count failed (%s): %s", regulation_id, e)
        return 0


def use_article_surface(
    supabase: SupabaseClient, regulation_id: str, articles: list[dict[str, Any]]
) -> bool:
    """Should this regulation render (and price) as مواد rather than chunks? SYNC.

    THE single decision point — ``get_regulation_doc`` (anon/ISR page),
    ``get_full_regulation`` (the authed reveal) and ``unlock_cost`` must all call
    this and never re-implement it. If the public page flips to chunks and the
    paid reveal does not, a reader who spent an unlock gets a structurally
    different document than the crawler saw.

    False → the caller takes its chunk path. A regulation with NO chunks keeps the
    article surface however holed it is: a partial document beats a blank one.
    """
    if not articles:
        return False
    if article_coverage_is_trustworthy(articles):
        return True
    if _regulation_chunk_count(supabase, regulation_id) == 0:
        logger.info(
            "article coverage rejected but no chunks exist: reg=%s rows=%d "
            "→ keeping article surface",
            regulation_id,
            len(articles),
        )
        return True

    numbers = [int(a.get("article_no") or 0) for a in articles if a.get("article_no")]
    apparent_length = max(numbers) if numbers else 0
    missing = apparent_length - len(numbers)
    logger.info(
        "article coverage rejected: reg=%s rows=%d max_no=%d missing=%d (%.1f%%) "
        "→ rendering chunks",
        regulation_id,
        len(articles),
        apparent_length,
        missing,
        (missing / apparent_length * 100) if apparent_length else 0.0,
    )
    return False


def parent_regulation_of_article(
    content_id: str, parent_regulation_id: Optional[str] = None
) -> Optional[str]:
    """The parent نظام's ``content_id`` for an article key, or ``None``.

    A published مادة's sidecar key is ``'{regulation_id}#{article_no}'`` (see
    ``_regulation_article_index``), so the parent is the part before the LAST
    ``'#'``. An explicit ``parent_regulation_id`` always wins — callers that
    already resolved the regulation (e.g. the /library/full/article route, which
    starts from the regulation slug) should pass it rather than rely on the key
    shape. Pure function, no DB.
    """
    if parent_regulation_id:
        return str(parent_regulation_id)
    cid = str(content_id or "")
    if "#" not in cid:
        return None
    head = cid.rsplit("#", 1)[0].strip()
    return head or None


def article_has_sharh(supabase: SupabaseClient, content_id: str) -> bool:
    """Does this مادة carry a cached AI شرح? SYNC (run via ``run_db``).

    ``content_id`` is the sidecar key ``'{regulation_id}#{article_no}'`` — the
    same string the ledger and ``resolve_access`` key on, so the item asked about
    here and the item charged there cannot drift.

    This is the ``always_gated`` input to :func:`resolve_access` for a مادة, and
    it exists so the شرح can be METERED without ever METERING NOTHING. Only ~229
    of ~50k مواد carry a شرح; a blanket always-gated flag would charge an unlock
    on an open-tier مادة whose نص the reader already had in full on the public
    page and hand back nothing new — the "trick" feeling §5.1 forbids. The
    frontend conditions its «اعرض الشرح كاملاً» CTA on the same fact
    (``sharh.has_sharh``), so the meter and the button agree on when the reveal
    is worth anything at all.

    Emptiness is treated as absence, matching :func:`_sharh_teaser`: a row whose
    ``sharh_md`` is blank renders no teaser, so it must not trigger a charge.

    Fail-soft to ``False``, which is safe in BOTH directions only because the
    sink is independent: a False leaves the نص free (correct) while
    ``get_full_article`` still refuses to load the شرح without
    ``AccessDecision.is_entitled``. A gated مادة is unaffected either way — it
    goes through the meter regardless.
    """
    cid = str(content_id or "")
    head, sep, tail = cid.rpartition("#")
    if not sep or not head.strip():
        return False
    try:
        article_no = int(tail)
    except (TypeError, ValueError):
        return False

    try:
        res = (
            supabase.table("seo_sharh")
            .select("sharh_md")
            .eq("regulation_id", head.strip())
            .eq("article_no", article_no)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("sharh presence lookup failed (%s): %s", cid, e)
        return False
    rows = res.data or []
    return bool(rows and (rows[0].get("sharh_md") or "").strip())


def unlock_cost(
    supabase: SupabaseClient, content_type: str, content_id: str
) -> int:
    """The weighted charge for unlocking one item (§1.2.1 / D4). SYNC.

    ``article | judgment | circular | form`` → **1**.
    ``regulation`` → priced off whichever surface it will actually RENDER, which
    is ``use_article_surface``'s call and never re-decided here:

      * TRUSTED ``seo_articles`` index →
        ``clamp(ceil(n_articles / ARTICLES_PER_UNLOCK), 1, 8)``.
      * everything else — a chunk-only regulation AND one whose index the
        coverage check rejected — ``clamp(ceil(n_chunks / CHUNKS_PER_UNLOCK),
        1, 8)``, i.e. the 1-chunk-≈-3-مواد rate.
      * neither مواد nor chunks → the minimum.

    Why a regulation is weighted at all: ``/library/full/regulation/{slug}``
    returns EVERY مادة untruncated for one unlock, so a flat cost would let a
    rational extractor charge only at the regulation level and take the whole
    statutory corpus 25× cheaper than the per-مادة price.

    ⚠ The price MUST follow the render decision. A regulation that falls back to
    chunks is a chunk document to the reader, so charging it as a 68-مادة
    document when it ships 60 chunk sections prices a surface it does not serve.

    Only ``article_no`` is read — never ``article_text``. This is a price, not a
    render; paging article BODIES across the wire to arrive at an integer is the
    exact waste the character-scan loop used to commit (it summed ``len()`` over
    every chunk body of the regulation) and it is gone.

    Fail-safe direction is UP, not down: any lookup failure falls back to the
    minimum (1) rather than blocking the user — the real extraction bounds are
    the per-period rate and the route-scoped rate limit, not this number.
    """
    ct = (content_type or "").strip()
    if ct != "regulation":
        return UNLOCK_COST_MIN

    # article_no ONLY — the coverage test is arithmetic on the numbering.
    articles: list[dict[str, Any]] = []
    try:
        offset, page = 0, 1000
        while True:
            res = (
                supabase.table("seo_articles")
                .select("article_no")
                .eq("regulation_id", str(content_id))
                .order("article_no")
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = res.data or []
            articles.extend({"article_no": r.get("article_no")} for r in batch)
            if len(batch) < page:
                break
            offset += page
    except Exception as e:  # noqa: BLE001
        logger.warning("unlock_cost: seo_articles scan failed (%s): %s", content_id, e)
        articles = []

    if use_article_surface(supabase, str(content_id), articles):
        return _clamp_cost(math.ceil(len(articles) / ARTICLES_PER_UNLOCK))

    # Chunk-priced: the legacy chunk-only regulations AND every index the
    # coverage check just rejected, at one rate.
    #
    # Yes, a rejected index counts chunks twice — once inside
    # `use_article_surface` to confirm there is something to fall back to, once
    # here to price it. Accepted: agreeing with the render decision beats saving
    # a round trip on a rare, uncached money path, and threading the count out of
    # the helper would give `unlock_cost` a private door into a decision that
    # exists precisely so all three call sites go through one.
    n_chunks = _regulation_chunk_count(supabase, str(content_id))
    if n_chunks <= 0:
        return UNLOCK_COST_MIN
    # Integer ceiling division, NOT `ceil(n / CHUNKS_PER_UNLOCK)`. The float form
    # is correct today — `25/3` rounds UP to 8.333333333333334, so `n / that`
    # lands just BELOW the integer at n = 25, 50, 75 and ceils the right way
    # (verified over 1..5000, zero divergence from the spec's `ceil(n*3/25)`).
    # But "correct because the rounding error happens to point the safe way" is
    # not a property to leave sitting on the money path for the next person to
    # re-derive. This form has no rounding to reason about.
    # `CHUNKS_PER_UNLOCK` stays as the documented human-readable rate.
    return _clamp_cost(
        -(-n_chunks * ARTICLES_PER_CHUNK // ARTICLES_PER_UNLOCK)
    )


# The ``reason`` values that mean a REAL entitlement stands behind the verdict —
# a ``library_unlocks`` row exists for this (user, item), or would but for a
# ledger blip. ``'open'`` is deliberately ABSENT: it grants access without buying
# anything, so §1.3 ALWAYS-GATED bytes must never ride on it. Allowlist, not a
# ``!= "open"`` test, so a future ``reason`` is unentitled until someone decides
# otherwise on purpose.
_ENTITLED_REASONS = frozenset({"granted", "already_unlocked", "ledger_unavailable"})


@dataclass
class AccessDecision:
    """The Layer B verdict for one (user, item) at one instant.

    ``reason`` ∈ ``open`` · ``already_unlocked`` · ``granted`` · ``anonymous`` ·
    ``locked`` · ``quota_exhausted`` · ``frozen_library`` · ``unresolvable`` ·
    ``ledger_unavailable``. The refusal reasons map 1:1 onto the D14 402 payload
    built by ``backend.app.errors.library_refusal_response``.

    ``charged`` is True ONLY when this call inserted a ledger row. A re-open, an
    open item and a نظام-covered مادة are all ``charged=False`` — re-charging a
    user for something they already paid for (or that was never gated) is
    exactly the "trick" feeling §5.1 forbids.
    """

    may_unlock: bool
    charged: bool
    reason: str
    cost: int = 0
    used: int = 0
    limit: Optional[int] = None
    resets_at: Optional[datetime] = None
    stored_count: int = 0

    @property
    def is_entitled(self) -> bool:
        """Does a ledger row stand behind this verdict? — the ALWAYS-GATED test.

        ``may_unlock`` answers "may this response carry the item's ORDINARY
        bytes". This answers the narrower "did the user actually BUY this item".
        They differ in exactly one place, and it is the expensive one:
        ``reason='open'`` is ``may_unlock=True, is_entitled=False`` — the نص of
        an open-tier مادة is free (correct, intended, §1.2), and the AI شرح
        layered on top of it is not (§1.3 "always gated"). Anything in the
        ALWAYS-GATED class must branch on THIS, never on ``may_unlock``; reading
        ``may_unlock`` there is the shape of H-5 (2026-08-07).
        """
        return self.reason in _ENTITLED_REASONS


def _find_unlock_row(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> Optional[dict[str, Any]]:
    """The user's ledger row for one item, or ``None``. SYNC, read-only.

    A query FAILURE also returns ``None`` — but that is safe here in the only
    direction that matters: a missing row leads to a CHARGE (or a refusal), never
    to free access. Fail-open on this lookup would be a bypass.
    """
    if not user_id or not content_type or content_id is None:
        return None
    try:
        res = (
            supabase.table("library_unlocks")
            .select(_UNLOCK_COLS)
            .eq("user_id", str(user_id))
            .eq("content_type", str(content_type))
            .eq("content_id", str(content_id))
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "library_unlocks lookup failed (%s/%s): %s", content_type, content_id, e
        )
        return None
    rows = res.data or []
    return rows[0] if rows else None


def _stored_library_count(supabase: SupabaseClient, user_id: str) -> int:
    """Total ``library_unlocks`` rows for the user — the shelf size behind the
    «لديك {n} مصدراً محفوظاً في مكتبتك» upgrade CTA (§5B.4). SYNC, read-only.
    Counts ROWS (items on the shelf), not SUM(cost): this is a user-facing
    inventory number, not a billing figure."""
    if not user_id:
        return 0
    try:
        res = (
            supabase.table("library_unlocks")
            .select("unlock_id", count="exact")
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        return int(getattr(res, "count", None) or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("stored library count failed (%s): %s", user_id, e)
        return 0


async def stored_library_count(supabase: SupabaseClient, user_id: str) -> int:
    """Async wrapper over ``_stored_library_count`` for route/service callers."""
    return await run_db(_stored_library_count, supabase, user_id)


def _insert_unlock(
    supabase: SupabaseClient,
    user_id: str,
    content_type: str,
    content_id: str,
    period_key: str,
    cost: int,
    surface: str,
) -> str:
    """Write the ledger row. SYNC. Returns which of THREE things happened:

      ``"inserted"`` — this call wrote the row and the user was charged.
      ``"conflict"``  — the row already existed (concurrent double-click); no charge.
      ``"failed"``    — the write ERRORED. Access is still granted uncharged.

    ``ON CONFLICT (user_id, content_type, content_id) DO NOTHING`` (PostgREST:
    ``resolution=ignore-duplicates`` + an explicit conflict target, since the
    table's PK is a surrogate uuid). With ``return=representation`` an ignored
    duplicate comes back as ZERO rows — that is how a concurrent double-click is
    detected, and it is the reason it can never double-charge.

    ⚠ WHY THE THIRD VALUE EXISTS. Granting access on a write failure is a
    deliberate policy — the user clicked once and the content is theirs, and the
    reverse would let a transient DB blip paywall a paying customer. But this
    used to return the same ``False`` as a conflict, so the caller reported
    ``already_unlocked`` and the response, the balance chip and every dashboard
    reading the ledger all agreed nothing was wrong. ``library_unlocks`` is the
    SOLE revenue control for this design: if writes start failing — permission
    drift, a bad ``period_key``, pool exhaustion, a botched migration — then
    EVERY reveal for EVERY user silently becomes free, indefinitely, behind one
    WARNING line. A total-bypass mode must be alarmable, so the failure is now
    logged at ERROR with a stable ``event=library_ledger_write_failed`` marker and
    surfaced to the caller as a distinct outcome.
    """
    try:
        res = (
            supabase.table("library_unlocks")
            .upsert(
                {
                    "user_id": str(user_id),
                    "content_type": str(content_type),
                    "content_id": str(content_id),
                    "period_key": str(period_key),
                    "cost": int(cost),
                    "surface": str(surface or "library"),
                },
                on_conflict="user_id,content_type,content_id",
                ignore_duplicates=True,
            )
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        # ERROR, not WARNING: this is the meter failing open. Alert on
        # `event=library_ledger_write_failed`.
        logger.error(
            "event=library_ledger_write_failed user=%s item=%s/%s period=%s err=%s "
            "— ACCESS GRANTED UNCHARGED; if this repeats the library is unmetered",
            user_id, content_type, content_id, period_key, e,
        )
        return "failed"
    return "inserted" if res.data else "conflict"


def _predicate_passes(row: dict[str, Any], state: "_quota.LibraryQuotaState") -> bool:
    """The §1.2 access predicate against ONE existing row:
    ``paid plan OR row.period_key == current period``."""
    if state.is_paid:
        return True
    return bool(state.period_key) and str(row.get("period_key")) == str(state.period_key)


async def resolve_access(
    supabase: SupabaseClient,
    user_id: Optional[str],
    content_type: str,
    content_id: str,
    *,
    surface: str = "library",
    parent_regulation_id: Optional[str] = None,
    always_gated: bool = False,
) -> AccessDecision:
    """May this user unlock this item right now? — the Layer B entry point.

    ``user_id`` is a **users.user_id** (NOT an auth id); route callers map it via
    ``case_service.get_user_id``. ``surface`` is analytics only ('library' |
    'reference') and MUST NEVER affect the charge, or the reference panel becomes
    a bypass again.

    Decision order (this IS the §1.2 predicate; do not reorder):
      1. ``service`` → open, free, no ledger row (policy-never-gated).
      2. no user     → ``anonymous`` (anon allowance is 0).
      3. item gate is ``'open'`` → free, no ledger row — SKIPPED ENTIRELY when
         ``always_gated``.
      4. existing row → paid OR same period ⇒ ``already_unlocked``; else
         ``frozen_library`` + the shelf count for the upgrade CTA.
      5. مادة whose parent نظام is already unlocked ⇒ ``already_unlocked`` (D5).
         The reverse does NOT hold: unlocking one مادة does not unlock the نظام.
      6. locked account → ``locked``.
      7. quota available → INSERT (idempotent) ⇒ ``granted`` / ``charged=True``.
      8. otherwise → ``quota_exhausted`` with used/limit/resets_at.

    ``always_gated`` marks a reveal that carries bytes from the §1.3 ALWAYS-GATED
    class — today exactly one thing, the AI شرح of a مادة. It skips step 3 only:
    an item may be Layer-A ``'open'`` and still cost an unlock, because what is
    bought there is not the public-domain نص but Rayhan's own layer on top of it.
    Steps 4–8 are untouched, so ``locked`` / ``frozen_library`` /
    ``quota_exhausted`` all apply and a grant writes its ledger row like any
    other. Pass it ONLY when those bytes actually exist for this item (see
    :func:`article_has_sharh`) — charging for a شرح that is not there is the
    "trick" feeling §5.1 forbids.

    NEVER memoize this, and never call it from a cacheable path (D11).
    """
    ct = (content_type or "").strip()
    cid = str(content_id or "")

    # 1. Government services are never gated, never charged, never a ledger row.
    if ct in NEVER_CHARGED_TYPES:
        return AccessDecision(may_unlock=True, charged=False, reason="open")

    # 2. Anonymous: 0 unlocks per period, by policy.
    if not user_id:
        return AccessDecision(may_unlock=False, charged=False, reason="anonymous")

    # 3. Layer A — an OPEN item costs nothing and writes nothing.
    #
    # ⚠ SKIPPED WHOLESALE for an always-gated reveal, and that skip IS the fix for
    # H-5 (security review 2026-08-07). Returning here is correct for the نص and
    # catastrophic for the شرح: step (b) of ``resolve_gate`` makes a مادة inherit
    # its parent نظام's tier, and all 229 of 229 مواد carrying a شرح sit under an
    # OPEN نظام — so this return short-circuited the meter for 100% of the شرح
    # corpus. Everything below was skipped with it: no quota read, no ``locked`` /
    # ``frozen_library`` / ``quota_exhausted``, no ledger row, ``charged=False``.
    # The open نص is still free; only the always-gated layer is bought.
    if not always_gated:
        gate = await run_db(
            resolve_gate, supabase, ct, cid, parent_regulation_id=parent_regulation_id
        )
        if gate == "open":
            return AccessDecision(may_unlock=True, charged=False, reason="open")

    state = await _quota.library_state(supabase, str(user_id))

    # 4. Already on the shelf → free forever if the predicate passes, frozen if not.
    row = await run_db(_find_unlock_row, supabase, str(user_id), ct, cid)
    if row is not None:
        if _predicate_passes(row, state):
            return AccessDecision(
                may_unlock=True, charged=False, reason="already_unlocked",
                cost=int(row.get("cost") or 0), used=state.used,
                limit=state.limit, resets_at=state.resets_at,
            )
        stored = await run_db(_stored_library_count, supabase, str(user_id))
        return AccessDecision(
            may_unlock=False, charged=False, reason="frozen_library",
            used=state.used, limit=state.limit, resets_at=state.resets_at,
            stored_count=stored,
        )

    # 5. D5 — a نظام covers its مواد. Re-charging a user for a مادة they just read
    #    in the continuous view is precisely the "trick" feeling §5.1 forbids.
    if ct == "article":
        parent_id = parent_regulation_of_article(cid, parent_regulation_id)
        if parent_id:
            parent_row = await run_db(
                _find_unlock_row, supabase, str(user_id), "regulation", parent_id
            )
            if parent_row is not None and _predicate_passes(parent_row, state):
                return AccessDecision(
                    may_unlock=True, charged=False, reason="already_unlocked",
                    used=state.used, limit=state.limit, resets_at=state.resets_at,
                )

    # 6. No plan assigned (or no chargeable period) → nothing can be written.
    if state.locked:
        return AccessDecision(
            may_unlock=False, charged=False, reason="locked",
            used=state.used, limit=state.limit, resets_at=state.resets_at,
        )

    # 7/8. Charge, or refuse with the reset date.
    #
    # ⚠ CHEAP REFUSAL FIRST. ``unlock_cost`` is expensive for a chunk-only نظام:
    # it pages through every ``chunks_v2.content`` row for that regulation just to
    # measure ``len()``. Running it before the quota check would let an account
    # that is ALREADY exhausted drive one full-corpus body scan per request, at
    # the whole 20/min route budget, forever, for zero cost to itself and with a
    # 402 as the only visible result — read-amplification against Postgres with no
    # user value. ``UNLOCK_COST_MIN`` is a sound lower bound on any real cost, so
    # refusing on it can never wrongly reject a reveal the user could afford.
    if not state.has_room(UNLOCK_COST_MIN):
        return AccessDecision(
            may_unlock=False, charged=False, reason="quota_exhausted",
            cost=UNLOCK_COST_MIN,
            used=state.used, limit=state.limit, resets_at=state.resets_at,
        )

    cost = await run_db(unlock_cost, supabase, ct, cid)
    if not state.has_room(cost):
        return AccessDecision(
            may_unlock=False, charged=False, reason="quota_exhausted", cost=cost,
            used=state.used, limit=state.limit, resets_at=state.resets_at,
        )

    outcome = await run_db(
        _insert_unlock, supabase, str(user_id), ct, cid,
        str(state.period_key), cost, surface,
    )
    if outcome != "inserted":
        # "conflict" — a concurrent double-click; the row already exists, so this
        #   call charges nothing. Never double-charge.
        # "failed"   — the ledger write ERRORED. Access is still granted (a DB
        #   blip must not paywall a paying customer), but it is reported as
        #   `ledger_unavailable` rather than `already_unlocked` so a
        #   silently-unmetered library is visible in telemetry instead of looking
        #   exactly like normal re-read traffic. Already logged at ERROR.
        return AccessDecision(
            may_unlock=True, charged=False, cost=cost,
            reason="already_unlocked" if outcome == "conflict" else "ledger_unavailable",
            used=state.used, limit=state.limit, resets_at=state.resets_at,
        )
    return AccessDecision(
        may_unlock=True, charged=True, reason="granted", cost=cost,
        used=state.used + cost, limit=state.limit, resets_at=state.resets_at,
    )


# ==========================================================================
# PHASE 2 — CONTENT ENDPOINTS (/regulations docs)
#
# Hub lists + document payloads for the first content launch. Every function
# here is READ-ONLY: the corpus surfaces (``regulations_v2``, ``chunks_v2``,
# ``services``) are VIEWS/tables owned by the ingest pipeline — we never write to
# them, and all SEO state (slug, tier, gate) lives in the ``seo_item_meta``
# sidecar. Gating decisions flow through ``resolve_gate`` / ``truncate_for_gate``
# only. Nothing here mutates a row (no counters); the endpoints are anon +
# hour-cached.
# ==========================================================================

# 9 cards/page — the 3×3 RTL grid the plan locks for every hub.
HUB_PAGE_SIZE = 9

# status_class (regulations_v2) → the public ``status`` exposed in payloads.
# CRITICAL: a repealed/consultation reg must NEVER render as current law. The
# frontend keys its status badge (+ مشروع نظام / ملغي warnings) off this value,
# so the mapping is the single guard against showing non-enacted text as active.
REG_STATUS_MAP = {
    "in_force": "active",
    "in_force_amended": "amended",
    "cancelled": "repealed",
    "consultation_ended": "draft",
    "under_consultation": "draft",
    "in_progress": "draft",
}


def map_reg_status(status_class: Optional[str]) -> str:
    """Map a ``regulations_v2.status_class`` value to the public ``status`` label.

    Returns one of ``'active' | 'amended' | 'repealed' | 'draft'``. Any unknown
    / null ``status_class`` fails to ``'draft'`` (the conservative choice): a
    ``'draft'`` reg is flagged NON-enacted (مشروع نظام) by the frontend, so an
    unrecognised state can never be mislabelled as current, enforceable law.
    """
    return REG_STATUS_MAP.get((status_class or "").strip(), "draft")


# regulations_v2.doc_type_bucket → Arabic display label. The bucket is a raw
# pipeline enum (``law_statute``, ``regulation_generic``, …) that must NEVER
# reach the page as-is (defect: «نوع الوثيقة: law_statute»). Covers every LIVE
# bucket value (queried 2026-07-23); an unknown/new value falls back to the raw
# string in ``map_doc_type_bucket`` so no information is lost. NOTE: this is a
# DISPLAY map only — the hub ``doc_type=`` FILTER keeps matching the RAW bucket
# (see ``_apply_reg_filters``), so labels here never affect filtering.
DOC_TYPE_BUCKET_LABELS = {
    "law_statute": "نظام",
    "regulation_generic": "لائحة/تنظيم",
    "executive_regulation": "لائحة تنفيذية",
    "technical_regulation": "لائحة فنية",
    "organizational_framework": "تنظيم/هيكلة",
    "guide": "دليل",
    "rules": "قواعد",
    "controls": "ضوابط",
    "requirements": "متطلبات",
    "standard_spec": "مواصفة قياسية",
    "procedure": "إجراء",
    "policy": "سياسة",
    "table_list": "جدول/قائمة",
    "instructions": "تعليمات",
    "principles_provisions": "مبادئ وأحكام",
    "agreement": "اتفاقية",
    "program_plan": "برنامج/خطة",
    "report_document": "تقرير/وثيقة",
    "translation": "ترجمة",
    "decision_decree": "قرار/مرسوم",
    "unspecified": "غير محدد",
}


def map_doc_type_bucket(value: Optional[str]) -> Optional[str]:
    """Map a raw ``doc_type_bucket`` to its Arabic display label.

    ``None``/blank → ``None`` (the caller omits the row). A recognised bucket
    returns its Arabic label; an UNKNOWN value falls back to the raw string so a
    newly-added pipeline bucket degrades to its enum name rather than vanishing —
    never surfacing a *known* English enum, never dropping an *unknown* one.
    """
    if value is None:
        return None
    key = str(value).strip()
    if not key:
        return None
    return DOC_TYPE_BUCKET_LABELS.get(key, key)


def _is_uuid(value: str) -> bool:
    """True when ``value`` parses as a UUID (used to route the ``entity`` filter
    to ``entity_id`` vs ``entity_ref``)."""
    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _text_snippet(text: Optional[str], max_len: int = 160) -> str:
    """Collapse whitespace and cut a plain-text field to a short card snippet.

    Best-effort, never raises. Cuts at the last word boundary at/before
    ``max_len`` (falling back to a hard cut) and appends an ellipsis when
    truncated. Not gate-sensitive — only ever fed already-public summary /
    intro fields, never gated body text.
    """
    s = re.sub(r"\s+", " ", (text or "")).strip()
    if len(s) <= max_len:
        return s
    cut = s[:max_len].rstrip()
    space = cut.rfind(" ")
    if space > max_len * 0.6:
        cut = cut[:space].rstrip()
    return cut + "…"


# --- مادة display-text cleaner (visual noise) -----------------------------
# Article bodies sourced from ``articles_v2`` begin with their OWN markdown
# heading line (which duplicates the page H1) plus edit-footnote noise, e.g.
# ``# المادة الثمانون: 44`` / ``##### المادة الرابعة عشرة ٦٠ :`` /
# ``المادة الحادية عشرة ^{٣} :``. The trailing digits (44, ٦٠) and ``^{…}``
# markers are footnote references — meaningless on the public page. These two
# regexes drive ``_clean_article_display_text`` (applied at DISPLAY assembly ONLY,
# never to the stored DB text and never to multi-article chunk fallbacks).

# Inline superscript footnote markers like ``^{٣}`` / ``^{12}`` — removed anywhere.
_INLINE_SUP_RE = re.compile(r"\^\{[^}]*\}")

# A LEADING مادة heading line that ENDS in a colon: optional markdown hashes +
# «المادة» + a SHORT ordinal run (bounded to 40 chars so a real body sentence that
# happens to open with «المادة …:» is never gobbled) + the colon + any footnote
# digits (Arabic-Indic ٠-٩ or Western) sitting before/after the colon. Matches
# from the start of the FIRST line only.
_ARTICLE_HEADER_COLON_RE = re.compile(
    r"^[ \t]*#{0,6}[ \t]*المادة(?:[ \t][^\n:]{0,40})?[ \t]*:[ \t]*[0-9٠-٩]*[ \t]*"
)
# A LEADING مادة heading line with NO colon, but written as a markdown heading
# (``#``-prefixed) — the whole first line is the heading, stripped entirely. The
# ``#`` requirement keeps a colon-less BODY line that merely opens with «المادة …»
# from being mistaken for a heading.
_ARTICLE_HEADER_HASH_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*المادة[^\n:]*$")


def _clean_article_display_text(text: str) -> str:
    """Strip the duplicate مادة heading line + inline footnote markers for DISPLAY.

    Pure function; the stored DB text is never mutated. Applied ONLY to extracted
    single-مادة ``article_text`` at the display-assembly points — NEVER to
    multi-article chunk fallbacks (whose inner heading lines are real separators).

    Two transforms:
      1. LEADING heading line — the FIRST line only, and only when it matches the
         مادة-header shape: optional ``#``s + «المادة …» up to and including its
         colon (plus any Arabic-Indic/Western footnote digits and ``^{…}`` markers
         on that line), or — for a ``#``-prefixed heading — the whole line when it
         carries no colon. Body lines are never touched.
      2. Inline ``^{…}`` superscript footnote markers anywhere in the text.
    The leading blank lines left by (1) are collapsed. Returns the input unchanged
    when neither transform matches.
    """
    if not text:
        return text

    nl = text.find("\n")
    first_line = text if nl == -1 else text[:nl]
    rest = "" if nl == -1 else text[nl:]  # keeps the leading "\n"

    header_stripped = False
    m = _ARTICLE_HEADER_COLON_RE.match(first_line)
    if m:
        first_line = first_line[m.end():]
        header_stripped = True
    elif _ARTICLE_HEADER_HASH_RE.match(first_line):
        first_line = ""
        header_stripped = True

    if header_stripped:
        body = first_line + rest
        # Collapse the leading blank lines / whitespace the removed header left.
        body = re.sub(r"^[ \t\r\n]+", "", body)
    else:
        body = text

    # Inline superscript footnote markers are edit noise anywhere in the text.
    body = _INLINE_SUP_RE.sub("", body)

    # Return the original object when nothing matched (no header, no markers).
    return body if (header_stripped or body != text) else text


def _slug_map(
    supabase: SupabaseClient, content_type: str, content_ids: list[Any]
) -> dict[str, str]:
    """Batch-resolve ``{content_id: slug}`` from the sidecar for one hub page.

    ``content_id`` is TEXT in ``seo_item_meta`` (migration 095), so ids are
    stringified before the ``IN`` lookup. Only rows that actually HAVE a slug are
    returned — a hub lists only slugged (published) items. Fail-soft: a sidecar
    blip yields ``{}`` (the page renders with the still-published items it could
    resolve) rather than 500ing the whole hub.
    """
    # Dedupe (callers like the articles sitemap pass one id per ROW — up to
    # 1,000 with heavy duplication) and chunk the IN lookup: PostgREST encodes
    # `in.(...)` in the query string, and hundreds of uuids blow the server's
    # URL-length limit into a 400 (caught 2026-07-23 on the articles feed).
    ids = list(dict.fromkeys(str(c) for c in content_ids if c is not None))
    if not ids:
        return {}
    _IN_CHUNK = 150
    out: dict[str, str] = {}
    for i in range(0, len(ids), _IN_CHUNK):
        chunk = ids[i : i + _IN_CHUNK]
        try:
            res = (
                supabase.table("seo_item_meta")
                .select("content_id, slug")
                .eq("content_type", content_type)
                .in_("content_id", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("slug map lookup failed (%s): %s", content_type, e)
            continue
        for r in res.data or []:
            cid = r.get("content_id")
            slug = r.get("slug")
            if cid is not None and slug:
                out[str(cid)] = slug
    return out


def _hub_error() -> LunaHTTPException:
    return LunaHTTPException(
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR,
        detail="حدث خطأ أثناء جلب المكتبة",
    )


def _published_ids(
    supabase: SupabaseClient, content_type: str
) -> Optional[list[str]]:
    """Published (slugged) corpus ids for a wing, or ``None`` in steady state.

    ⚠ ONLY WINGS WITHOUT A RANKED VIEW REACH THIS — circulars and compliance. A
    wing that has one (regulations, judgments) has no sample mode to detect: see
    ``_RANKED_HUB_VIEWS`` and the ``SAMPLE_MODE_MAX_IDS`` header.

    Pages the ``seo_item_meta`` sidecar for every ``(content_type=X, slug NOT
    NULL)`` row's ``content_id`` in 1,000-row range chunks — PostgREST clamps any
    response to max-rows=1000 (documented trap at the top of this module), so a
    single unbounded select would silently truncate — until the sidecar is
    exhausted (or the sample ceiling is passed, see below).

    Returns the id list ONLY while the wing is in "sample mode": published count
    ``<= SAMPLE_MODE_MAX_IDS`` (1000). Above that the wing is in full-corpus
    steady state and this returns ``None`` so the hub listers keep their legacy
    corpus-pagination path untouched. (The scan short-circuits as soon as it has
    seen more than the ceiling — a steady-state wing costs exactly one 1,000-row
    id fetch, never a full sidecar walk.)

    The result (a list OR the steady-state ``None``) is memoized per content_type
    behind a ~60s module-level TTL cache — same shape as ``_gate_defaults_cache``.
    Fail-soft: a sidecar error returns the last cached value when one exists, else
    ``None`` (→ legacy path), so a blip degrades to steady-state behaviour rather
    than 500ing the hub.
    """
    now = time.monotonic()
    cached = _published_ids_cache.get(content_type)
    if cached is not None and now < cached.get("expires_at", 0.0):
        return cached.get("value")

    ids: list[str] = []
    offset = 0
    page = 1000
    try:
        while True:
            res = (
                supabase.table("seo_item_meta")
                .select("content_id")
                .eq("content_type", content_type)
                .not_.is_("slug", "null")
                .order("content_id", desc=False)
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = res.data or []
            for r in batch:
                cid = r.get("content_id")
                if cid is not None:
                    ids.append(str(cid))
            if len(batch) < page:
                break
            if len(ids) > SAMPLE_MODE_MAX_IDS:
                # Already past the sample ceiling → steady state; stop early.
                break
            offset += page
    except Exception as e:  # noqa: BLE001
        logger.warning("published-ids scan failed (%s): %s", content_type, e)
        return cached.get("value") if cached is not None else None

    value: Optional[list[str]] = ids if len(ids) <= SAMPLE_MODE_MAX_IDS else None
    _published_ids_cache[content_type] = {
        "value": value,
        "expires_at": now + _PUBLISHED_IDS_TTL_SECONDS,
    }
    return value


# Chunk size for the ``id IN (...)`` corpus fetch in sample mode and for the BM25
# candidate fetch. Same reason ``_slug_map`` chunks its sidecar lookup: PostgREST
# encodes ``in.(...)`` in the query string and hundreds of uuids blow the server's
# URL-length limit into a 400. A sampled wing's published set is
# <= SAMPLE_MODE_MAX_IDS (1000) and a search's is <= HUB_SEARCH_LIMIT (200), so
# this is at most 7 round-trips per wing per page and usually 1–2.
_ID_IN_CHUNK = 150


def _fetch_corpus_by_ids(
    supabase: SupabaseClient,
    table: str,
    select_cols: str,
    ids: list[str],
    apply_filters,
) -> list[dict[str, Any]]:
    """Fetch corpus rows whose ``id`` is in ``ids`` (sample-mode pagination).

    Chunks the ``id IN (...)`` lookup at ``_ID_IN_CHUNK`` (URL-length trap) and
    applies the wing's existing filter builder to each chunk via ``apply_filters``
    (a ``qb -> qb`` callable closing over the wing's filter args). NO DB
    order/range — the id set is ``<= SAMPLE_MODE_MAX_IDS``, so the caller sorts +
    slices the 9-item window in Python. Raises ``_hub_error()`` on a DB failure
    (same contract as the legacy hub path)."""
    out: list[dict[str, Any]] = []
    try:
        for i in range(0, len(ids), _ID_IN_CHUNK):
            chunk = ids[i : i + _ID_IN_CHUNK]
            if not chunk:
                continue
            qb = supabase.table(table).select(select_cols)
            qb = apply_filters(qb)
            qb = qb.in_("id", chunk)
            out.extend(qb.execute().data or [])
    except Exception as e:  # noqa: BLE001
        logger.exception("Error fetching %s by ids (sample mode): %s", table, e)
        raise _hub_error()
    return out


# ============================================
# HUB SEARCH — BM25 behind the existing ``q`` param
# (.claude/plans/bm25_navigation_search.md D8 · §5.2)
#
# ``q`` used to be a single-column ``ilike '%…%'`` per wing: no IDF, no Arabic
# normalization (a reader typing «الايجار» never matched «الإيجار»), no ranking,
# and one arbitrary column per wing — ``clean_title`` for أنظمة, ``short_summary``
# for أحكام. It is now ``bm25_search()`` (migration 111) and NOTHING ELSE about
# the hub changed: same param, same 3-char floor, same 9-item pages, same
# response shape, same URLs.
#
# THE SHAPE OF THE SWAP, and why it is not "just call the RPC":
#
#   * BM25 supplies an ORDERED CANDIDATE ID SET; the wing's OWN filter builder
#     still runs on the corpus rows. The hub filters are not all representable in
#     ``search_index.facets`` — ``entity`` matches ``entity_id`` OR ``entity_ref``,
#     a circular's ``entity`` resolves through an ``entities`` name lookup — so
#     pushing them into the RPC would quietly change what they mean. This way
#     filter semantics are byte-identical to today's and only the ORDER and the
#     MATCHING RULE change.
#   * The id set is capped (``HUB_SEARCH_LIMIT``). That is both a latency bound
#     and the enumeration bound §5.4 asks for: search is a filter dimension
#     stacked on top of the page-depth cap, so a query can walk at most the top
#     200 of a wing.
#   * Ordering by score REPLACES the wing's ordering contract (in-force-first,
#     newest-first, …) for that request only. A search result list ordered by
#     anything but relevance is not a search result list.
#
# ⚠ THE ``q``-ABSENT PATHS ARE UNTOUCHED. Sample mode, the two-partition
# regulations paginator, the sector memos: all of it still runs exactly as
# before when no ``q`` is present, which is the overwhelming majority of hub
# traffic (every ISR bake). Do not "unify" the two — the browse ordering
# contracts are load-bearing and none of them is expressible as a score.
#
# ⚠ ANON NEVER GETS HERE. D9 makes search registered-only, and the ROUTE drops
# ``q`` for an anonymous caller before calling any of these functions
# (``public_library._search_query``). So a non-None ``q`` in this module means
# "an authenticated caller asked", and that is the only place that invariant is
# enforced — do not add a second, drifting check here.
# ============================================


def _bm25_hub_rows(
    supabase: SupabaseClient,
    *,
    corpus: str,
    table: str,
    select_cols: str,
    q: str,
    apply_filters,
) -> tuple[list[dict[str, Any]], bool]:
    """``(rows, truncated)`` for ``q``, ordered by BM25 score (best first). SYNC.

    ``apply_filters`` is the wing's existing ``qb -> qb`` builder with ``q``
    already removed (the RPC owns the text match now; leaving the ``ilike`` on
    would AND a substring test onto a stemmed, normalized match and throw away
    most of what BM25 just bought).

    ``truncated`` is True when the ranked id set came back AT the cap, i.e. there
    were probably more matches than were ranked. It is the honesty flag behind
    the hub envelope's ``total_count_is_exact``: ``len(rows)`` is then a FLOOR,
    not a total, and a UI printing it as «200 نتيجة» would be inventing a number.

    Only slugged rows are in the index, so the result is already the published
    set — which is why this path needs no ``_published_ids`` intersection in
    sample mode.
    """
    ids = search_service.corpus_search_ids(supabase, corpus, q)
    truncated = len(ids) >= search_service.HUB_SEARCH_LIMIT
    if not ids:
        return [], False
    rows = _fetch_corpus_by_ids(supabase, table, select_cols, ids, apply_filters)
    rank = search_service.rank_map(ids)
    # Rows come back in PostgREST's order, in up to two chunks — the ranking has
    # to be re-imposed here or it is simply lost. Unranked rows (impossible today,
    # since every id came FROM the ranking) sort last rather than first.
    rows.sort(key=lambda r: rank.get(str(r.get("id")), len(ids)))
    return rows, truncated


def _hub_result(
    items: list[dict[str, Any]],
    page: int,
    total_pages: int,
    *,
    q: Optional[str] = None,
    total: int = 0,
    truncated: bool = False,
) -> dict[str, Any]:
    """The hub envelope every lister returns.

    ⚠ THE ENVELOPE DOES NOT CHANGE SHAPE FOR A SEARCH (D8 / plan §5.2). ``items``
    are the SAME cards browse returns, snippet included — §5.3 requires a result
    card to render the static free excerpt it already renders, and it can only do
    that if the search response still carries it. There is no separate hit shape
    on this endpoint; ``SearchHit`` belongs to ``/api/v1/search``, which is a
    different (cross-wing) surface.

    ``total_count`` is ADDITIVE and populated ONLY for a search — a browse
    listing has ``total_pages`` and needs nothing else, whereas a result list
    wants to say how many. It is NOT necessarily exact: the ranked id set is cut
    at ``HUB_SEARCH_LIMIT``, and ``bm25_search`` itself cuts at ``p_candidates``
    before scoring. ``total_count_is_exact`` reports which of the two you have,
    so a UI can print «17 نتيجة» when it is true and «أفضل 200 نتيجة» when it is
    not, instead of asserting a number the backend does not actually know.
    """
    return {
        "items": items,
        "page": page,
        "total_pages": total_pages,
        "total_count": total if q else None,
        "total_count_is_exact": (not truncated) if q else True,
    }


# ============================================
# CROSS-WING COUNTS — the sector grid + the unified hub tabs
# (library_sectors.md §5 · §7.2 · §7.3)
#
# ⚠ THESE COUNT WHAT IS *SERVABLE*, NOT WHAT IS IN THE CORPUS. Read this before
# "fixing" a number that looks too small.
#
# The hub listers paginate the PUBLISHED set — a ranked view for regulations and
# judgments, the ``_published_ids`` sample list for circulars and compliance.
# These two functions follow the same rule per wing, independently, because the
# alternative is worse than a cosmetic mismatch — measured on live data
# 2026-08-01:
#
#     sector (أنظمة)          corpus   servable   a corpus-based paginator says
#     المواصفات والمقاييس        695          0   78 pages, every one EMPTY
#     الأمن الغذائي              406          0   46 pages, every one EMPTY
#     المعاملات التجارية         693         24   77 pages, 3 of them real
#
# The frontend derives BOTH its D9 thin-page ``noindex`` decision and its
# ``generateStaticParams`` filter from these counts, so a corpus-based 695/0
# sector passes the "fat enough to index" test and gets prerendered as a static,
# indexable, EMPTY page — soft-404s at scale, which is the exact failure D9
# exists to prevent. Servable counts fix indexability, prerendering and display
# at once, with no frontend change.
#
# ⚠ THE STEADY-STATE RPC IS ``library_sector_counts_published()`` (migration 124),
# NOT ``library_sector_counts()`` (migration 109). The two have the identical
# signature and 109 is still installed, so this is a one-word difference that
# changes every number on /library: 109 counts CORPUS rows. It was only ever
# correct here by accident — no wing had crossed ``SAMPLE_MODE_MAX_IDS``, so
# nothing reached the fall-through. Publishing ~10,000 judgments and 1,188
# regulations reaches it on both wings at once, and the failure is SILENT: the
# grid keeps rendering, advertising 3,951 أنظمة and 30,531 أحكام that the wings
# cannot serve. Do not "simplify" this back to 109.
#
# SELF-HEALING, and that is the whole point of the shape: nothing here is a
# rollout hack to unwind later. A wing with a ranked view is always counted from
# the published relation; a wing without one asks ``_published_ids`` on every
# memo refresh, so the moment ``build_seo_slugs --apply`` pushes it past
# ``SAMPLE_MODE_MAX_IDS`` it moves to the published RPC on its own. Mixed states
# are normal and correct: one wing may be sampled while another is complete.
#
# Two functions, because the second NEVER sums to the first and a caller that
# derives one from the other is wrong twice over (both measured live):
#
#   * ``library_corpus_counts`` — one servable total per wing. Sizes the unified
#     hub's four tab chips, whose paginators walk exactly that set.
#   * ``sector_counts``         — 38 × 4 per-sector counts. A row carries
#     MULTIPLE sectors, so the columns OVER-count (over the full corpus the
#     regulations column sums to 8,971 against 3,373 rows, judgments to 31,924);
#     and ``cases.legal_domains`` is only 67.7% populated (20,671 of 30,531 —
#     plan D10), so the judgment columns simultaneously MISS 9,860 judgments that
#     stay reachable only through the unfiltered /judgments hub.
#
# No arithmetic between them, in either direction.
# ============================================

# The published-only counts RPC (migration 124). Same signature as migration
# 109's ``library_sector_counts()``, which counts the CORPUS — see the ⚠ above.
_SECTOR_COUNTS_RPC = "library_sector_counts_published"

# section name → (corpus table, sidecar content_type, sector array column).
# Section names match the wing vocabulary used everywhere else in this file
# (``_total_pages_memo`` keys, item-budget sections, the sitemap map); the
# content_type is the sidecar's own singular spelling.
#
# ⚠ THE CORPUS TABLE HERE IS NOT ALWAYS WHAT GETS COUNTED. A wing with a ranked
# view is counted over the VIEW (``_RANKED_HUB_VIEWS``, defined beside the hub
# table constants below, because that is where the view names are owned); the
# corpus table is then only the fallback nobody reaches. The content_type and the
# array column are used by both paths.
#
# ⚠ ``compliance`` READS A VIEW AND IS NEVER COUNTED BY THE RPC. The wing is the
# 169 service GUIDES (``library_compliance_v`` = ``service_guides`` ⋈ ``services``
# — migration 142), and it rejoined this map on 2026-08-19 when the guides
# shipped. The counts RPC ALSO returns a ``compliance`` column, and that column is
# a DIFFERENT NUMBER: it counts the ``services`` corpus (4,746 rows), which this
# wing does not publish. ``_RPC_SECTOR_COUNT_EXCLUDED`` below is what keeps the two
# apart; do not "fix" the RPC to match, and do not delete that guard.
_SECTION_SOURCES: dict[str, tuple[str, str, str]] = {
    "regulations": ("regulations_v2", "regulation", "sectors"),
    "judgments": ("cases", "judgment", "legal_domains"),
    "compliance": ("library_compliance_v", "compliance", "sectors"),
    "circulars": ("circulars", "circular", "sectors"),
}

SECTOR_COUNT_SECTIONS: tuple[str, ...] = tuple(_SECTION_SOURCES)
"""The four wings a sector page has tabs for, in tab order (plan D3)."""

# Sections whose per-sector counts must NEVER be read from
# ``library_sector_counts_published()``. Exactly one member, and it is a
# fail-safe rather than a routine path: ``compliance`` is a 169-row wing, so it
# is always in sample mode (ceiling 1,000) and ``_published_sample_counts``
# answers for it. The ONE way the RPC branch could be reached is a sidecar blip
# making ``_published_ids`` return ``None`` — and the RPC's ``compliance`` column
# would then hand the sector grid the ``services`` corpus (4,746 rows of
# procedures we do not publish) instead of the guides. Counting zero is the
# correct degradation; counting the wrong corpus is not.
_RPC_SECTOR_COUNT_EXCLUDED: frozenset[str] = frozenset({"compliance"})


def _published_sample_counts(
    supabase: SupabaseClient, section: str
) -> Optional[tuple[int, dict[str, int]]]:
    """``(servable rows, {slug: rows})`` for one wing's PUBLISHED sample.

    Returns ``None`` — the caller's signal to use the published RPC instead — for
    a wing that has NO sample to count, which is now two different situations:

      * the wing has a RANKED VIEW (``_RANKED_HUB_VIEWS``: regulations,
        judgments). Its published set is a relation, not a list of ids, and can
        be any size; scanning it into memory was never going to survive ~10,000
        judgments. Short-circuits before touching the sidecar.
      * the wing has no ranked view and is past ``SAMPLE_MODE_MAX_IDS``
        (``_published_ids`` → ``None``).

    The set counted here is EXACTLY the set the wing's sample-mode lister
    paginates — same ids, same ``_fetch_corpus_by_ids`` reader — so a sector
    page's ``total_pages`` and the page it actually serves cannot disagree. That
    equality is the point; §12.2 failed before it because page 1 reported the
    lister's sample total while page 2 reported the corpus total. The ranked-view
    wings hold the same equality by a stronger route: both numbers come from the
    same published relation.

    A row is counted once per sector it carries (matching what the sector filter
    would return for each of them) and sector values outside the 38 are dropped:
    they have no slug, therefore no public page.
    """
    if section in _RANKED_HUB_VIEWS:
        return None
    table, content_type, column = _SECTION_SOURCES[section]
    pub_ids = _published_ids(supabase, content_type)
    if pub_ids is None:
        return None
    if not pub_ids:
        return 0, {}

    rows = _fetch_corpus_by_ids(
        supabase, table, f"id, {column}", pub_ids, lambda qb: qb
    )
    tally: dict[str, int] = {}
    for row in rows:
        for name in row.get(column) or []:
            slug = slug_for_sector(name)
            if slug:
                tally[slug] = tally.get(slug, 0) + 1
    return len(rows), tally


def library_corpus_counts(supabase: SupabaseClient) -> dict[str, int]:
    """Servable row count per wing — the unified hub's three tab chips (§7.3).

    Per wing, in order of preference: one ``count='exact'`` head query over the
    wing's RANKED VIEW (published by construction — regulations, judgments), else
    the published sample's size while the wing is sampled, else one
    ``count='exact'`` over the corpus. The route memoises the result for 5
    minutes (§5).

    ⚠ THE LAST OF THOSE THREE IS THE ONE THAT LIES, and it is unreachable today.
    A corpus count over ``cases`` says 30,531 while the wing serves ~10,000; the
    tab chip it sizes belongs to a paginator that walks the published set. A wing
    reaches it only by having neither a ranked view nor a sample — i.e. by being
    published past ``SAMPLE_MODE_MAX_IDS`` with no view, which is the state
    migration 123 exists to make impossible. Give a wing a ranked view before
    publishing it past the ceiling.

    ⚠ ``judgments`` is this wing's own total and is NOT derivable from
    ``sector_counts`` — see the block comment above. Count the wing; do not do
    arithmetic on sectors.
    """
    out: dict[str, int] = {}
    try:
        for section, (table, _content_type, _column) in _SECTION_SOURCES.items():
            view = _RANKED_HUB_VIEWS.get(section)
            if view is None:
                sample = _published_sample_counts(supabase, section)
                if sample is not None:
                    out[section] = sample[0]
                    continue
            res = (
                supabase.table(view or table)
                .select("id", count="exact")
                .limit(1)
                .execute()
            )
            out[section] = int(res.count or 0)
    except LunaHTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Error counting the library corpora: %s", e)
        raise _hub_error()
    return out


def sector_counts(supabase: SupabaseClient) -> dict[str, dict[str, int]]:
    """All 152 sector×wing counts — ``{slug: {regulations, …, total}}``.

    §5 makes a sector a SECTION rather than a filter, which means real counts on
    38 pages × 4 tabs, and is equally explicit that they must not cost a count
    query apiece. Per wing:

      * SAMPLED  → one ``id IN (...)`` read of the published rows (~100
        circulars, 169 compliance guides), tallied in Python. One query for that
        wing's whole 38-sector column. Only wings with no ranked view — circulars
        and compliance — can be here.
      * OTHERWISE → ``library_sector_counts_published()`` (migration 124), which
        does all 38 in one grouped ``unnest`` over the corpus ⋈ sidecar —
        PostgREST cannot express that, which is why the RPC exists. It is issued
        ONCE per refresh and covers every non-sampled wing at the same time —
        every one EXCEPT the members of ``_RPC_SECTOR_COUNT_EXCLUDED``, whose
        same-named RPC column counts a different corpus.

    So the worst case is two small reads plus one RPC per 5-minute memo refresh,
    and the end state is a single RPC. Wings leave the sampled path on their own
    as ``build_seo_slugs`` publishes them; a mixed state is normal.

    ⚠ NOT ``library_sector_counts()`` (migration 109) — that one counts the
    CORPUS and still exists. See the ⚠ in the block comment above; swapping the
    name back silently re-publishes 3,951 أنظمة and 30,531 أحكام to the grid.

    Every one of the 38 slugs is present in the result, seeded to zero, so a
    sector that holds nothing servable still returns a row (the frontend needs
    the zero — it is what makes D9 drop the tab and skip prerendering) instead of
    vanishing from the grid. ``total`` is the sum of the four wings.
    """
    per_section: dict[str, dict[str, int]] = {}
    steady: list[str] = []
    for section in SECTOR_COUNT_SECTIONS:
        sample = _published_sample_counts(supabase, section)
        if sample is None:
            steady.append(section)
        else:
            per_section[section] = sample[1]

    if steady:
        try:
            res = supabase.rpc(_SECTOR_COUNTS_RPC, {}).execute()
        except Exception as e:  # noqa: BLE001
            logger.exception("Error reading %s(): %s", _SECTOR_COUNTS_RPC, e)
            raise _hub_error()
        for row in res.data or []:
            # The VOCABULARY is shared/library/sectors.py, not the corpus: a
            # sector value the pipeline invents has no slug, therefore no page.
            slug = slug_for_sector(row.get("sector"))
            if not slug:
                continue
            for section in steady:
                # ⚠ NOT every wing's column here means what the wing serves —
                # see ``_RPC_SECTOR_COUNT_EXCLUDED``.
                if section in _RPC_SECTOR_COUNT_EXCLUDED:
                    continue
                per_section.setdefault(section, {})[slug] = int(row.get(section) or 0)

    counts: dict[str, dict[str, int]] = {}
    for slug in SECTOR_SLUGS.values():
        per_wing = {
            section: int(per_section.get(section, {}).get(slug, 0))
            for section in SECTOR_COUNT_SECTIONS
        }
        per_wing["total"] = sum(per_wing.values())
        counts[slug] = per_wing
    return counts


# --- /regulations hub -----------------------------------------------------


def _apply_reg_filters(qb, entity, doc_type, sector, q=None):
    """Apply the regulations hub FACET filters to a query builder (chainable).

    ``entity`` matches ``entity_id`` when it is a UUID, else ``entity_ref``;
    ``doc_type`` = ``doc_type_bucket``; ``sector`` = array-contains on
    ``sectors``. Empty/blank filters are no-ops.

    ⚠ ``q`` IS ACCEPTED AND IGNORED. It was an ``ilike`` on ``clean_title`` until
    Wave B moved the text match to ``bm25_search()`` (see ``_bm25_hub_rows``); the
    parameter survives only so the wing's callers can keep passing their filter
    tuple around positionally without every call site having to change. Passing a
    ``q`` here is not an error and does nothing — the text match happens BEFORE
    this builder runs, by selecting which ids are fetched at all.
    """
    entity = (entity or "").strip()
    doc_type = (doc_type or "").strip()
    sector = (sector or "").strip()
    if entity:
        if _is_uuid(entity):
            qb = qb.eq("entity_id", entity)
        else:
            qb = qb.eq("entity_ref", entity)
    if doc_type:
        qb = qb.eq("doc_type_bucket", doc_type)
    if sector:
        qb = qb.contains("sectors", [sector])
    return qb


def _reg_count(supabase, entity, doc_type, sector, q, *, in_force_only=False) -> int:
    """Filtered regulation count. With ``q`` present this counts the BM25 match
    set (capped at ``HUB_SEARCH_LIMIT``) so the number agrees with what the
    lister will actually page through — a wall reporting one total while the
    paginator walks another is the exact contradiction §12.2 failed on."""
    if q:
        rows, _truncated = _bm25_hub_rows(
            supabase,
            corpus="regulation",
            table=_REG_HUB_TABLE,
            select_cols="id, status_class",
            q=q,
            apply_filters=lambda qb: _apply_reg_filters(qb, entity, doc_type, sector),
        )
        if in_force_only:
            rows = [r for r in rows if r.get("status_class") == "in_force"]
        return len(rows)

    qb = supabase.table(_REG_HUB_TABLE).select("id", count="exact")
    qb = _apply_reg_filters(qb, entity, doc_type, sector)
    if in_force_only:
        qb = qb.eq("status_class", "in_force")
    return int((qb.limit(1).execute().count) or 0)


# ⚠ THE /regulations WING READS THE RANKED VIEW, NOT THE CORPUS (migration 116).
# `library_regulations_ranked` is `regulations_v2` INNER JOIN the `seo_item_meta`
# sidecar on `slug IS NOT NULL`, carrying `slug` + `rank`. Three things follow,
# and all three are load-bearing:
#
#   * It IS the published set. No `_published_ids` intersection, no sample-mode
#     branch, no `_slug_map` round-trip — an unpublished row cannot appear
#     because the join drops it.
#   * `rank` makes the ordering contract a single sortable column, so one
#     `.order("rank").order("id").range(...)` replaces the two-partition
#     in-force/rest straddle the wing used to need.
#   * Counts over it are counts of what is SERVABLE, which is what every page
#     number on this wing has to mean.
_REG_HUB_TABLE = "library_regulations_ranked"

# ⚠ AND THE /judgments WING DOES THE SAME (migration 123). Identical shape over
# `cases` ⋈ the sidecar, and it exists for a failure that had already been
# demonstrated on the other wing: below `SAMPLE_MODE_MAX_IDS` the judgments hub
# paginated a list of published ids, above it the CORPUS — all 30,531 rows — and
# it then dropped the unslugged ones AFTER paging, so at ~10,000 of 30,531
# published a nine-card page would have rendered about three cards across ~3,393
# mostly-empty pages. Publishing more than 1,000 judgments without this view is
# the bug; the view is not an optimisation.
#
# `rank` is exposed but NOT YET WRITTEN for judgments (`build_usage_rank.py`
# ranks regulations only), which is why this wing still orders by date — see
# `list_judgments_hub`. Do not switch the ordering to `rank` until something
# populates it: PostgREST's ascending NULLS LAST would put the whole wing in
# arbitrary id order behind nothing at all.
_JUDGMENT_HUB_TABLE = "library_judgments_ranked"

# section name → its published-only ranked view. THE switch that says a wing has
# no sample mode: its lister, its page counts and its sector counts all read the
# published relation at any corpus size. Read by `_published_sample_counts` and
# `library_corpus_counts`, which are defined ABOVE this line and resolve it at
# call time — the view names are owned here, next to the wings that read them,
# and duplicating the strings upstream is how the two would drift.
#
# A wing joins this map by getting a `library_<wing>_ranked` view (mirror
# migration 123) — NOT by having its ceiling raised.
_RANKED_HUB_VIEWS: dict[str, str] = {
    "regulations": _REG_HUB_TABLE,
    "judgments": _JUDGMENT_HUB_TABLE,
}

# Column set the /regulations hub reads. `slug` rides along from the view, which
# is why this path issues no sidecar lookup.
_REG_HUB_SELECT = (
    "id, reg_ref, clean_title, title, entity_name, status_class, "
    "doc_type_bucket, summary, sectors, slug"
)


# ⚠ THE FOUR ``_*_hub_item`` BUILDERS BELOW ARE THE ONLY DEFINITIONS OF THEIR
# WING'S CARD SHAPE, and they are shared by TWO readers: the hub lister, and the
# «اقرأ تاليًا» / «الأنظمة المذكورة» strips on the document pages
# (`.claude/plans/read_next_related_items.md` §5.1). The strips feed the SAME
# frontend card component the hub grid does, so the two payloads must be
# byte-identical — and a duplicated dict literal is exactly how they would drift
# (one wing gains a field, the other silently does not, and the card renders
# blank on half the site). Extend the shape HERE or nowhere.


def _reg_hub_item(row: dict[str, Any]) -> dict[str, Any]:
    """One /regulations card from a ``_REG_HUB_SELECT`` row.

    ``slug`` is read straight off the row: every source of these rows is
    ``library_regulations_ranked``, a published-only view that carries it (the
    caller still guards on it as a cheap invariant check). ``clean_title`` is
    NULL on 43% of the corpus, hence the ``coalesce`` to ``title``.
    """
    return {
        "slug": row.get("slug"),
        "title": (row.get("clean_title") or row.get("title") or "").strip(),
        "entity_name": row.get("entity_name"),
        "status": map_reg_status(row.get("status_class")),
        "doc_type": map_doc_type_bucket(row.get("doc_type_bucket")),
        "summary_snippet": _text_snippet(row.get("summary"), 160),
        "sectors": row.get("sectors") or [],
    }


def regulations_hub_total_pages(
    supabase: SupabaseClient,
    entity: Optional[str] = None,
    doc_type: Optional[str] = None,
    sector: Optional[str] = None,
    q: Optional[str] = None,
) -> int:
    """Total hub pages for the filtered regulations set (for the anon-cap body).

    Counts the PUBLISHED rows — ``_reg_count`` reads the same ranked view the
    lister paginates, so the page count and the pages actually served cannot
    disagree at any corpus size. (That equality used to need a sample-mode
    branch here, because counting the corpus while the lister paginated the
    published sample is precisely how §12.2 shipped page 1 reporting one total
    and page 2 reporting another.) With ``q`` present it counts the BM25 match
    set instead, for the same reason.
    """
    try:
        total = _reg_count(supabase, entity, doc_type, sector, q)
    except LunaHTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Error counting regulations hub: %s", e)
        raise _hub_error()
    return max(1, math.ceil(total / HUB_PAGE_SIZE)) if total else 1


def list_regulations_hub(
    supabase: SupabaseClient,
    *,
    page: int = 1,
    entity: Optional[str] = None,
    doc_type: Optional[str] = None,
    sector: Optional[str] = None,
    q: Optional[str] = None,
) -> dict[str, Any]:
    """One page (9 items) of the /regulations hub.

    Ordering contract = ``seo_item_meta.rank`` ascending, then ``id``. Rank is
    written by ``scripts/build_usage_rank.py`` from how often the deep-search
    pipeline actually cited each regulation (plan: ranking_criteria.md), so page
    1 is the corpus's most-used codes rather than an alphabetical accident.

    ⚠ THIS USED TO BE THREE CODE PATHS AND IS NOW ONE. The old contract was
    "in-force first, then ``clean_title``" — which no single column expresses, so
    browse needed a two-partition straddle (in-force title-ordered, then the
    rest), sample mode needed a parallel Python sort of the published ids, and
    the two did not actually agree: the DB path ordered on ``clean_title`` alone
    while the Python path coalesced to ``title``, and ``clean_title`` is NULL on
    43% of the corpus. It also put one titleless row and eight «النظام الأساس
    لشركة … للتأمين التعاوني» charters on page 1, because Arabic titles begin with
    their document type, so alphabetical sorts by type-word. One indexed integer
    on the published-only view replaces all of it.

    ⚠ NULL ``rank`` SORTS LAST, NOT FIRST. PostgREST's default for ``.order()``
    ascending is NULLS LAST, which is what a freshly published, not-yet-ranked
    row should do: appear at the end of the list rather than ahead of نظام
    المعاملات المدنية. Publish then rank; between the two the new rows queue at
    the back instead of taking the front page.

    Returns ``{"items": [...], "page": page, "total_pages": N}``. The anon
    depth-cap is enforced by the route (this function always returns real data).

    SEARCH MODE (``q`` present, therefore an authenticated caller — D9): ids come
    from ``bm25_search()`` in score order, the wing's facet filters are applied to
    those rows, and the window is sliced by RANK. The rank contract does not
    apply to a search result list — a result list ordered by anything other than
    relevance is not a result list.
    """
    page = max(1, int(page or 1))
    ps = HUB_PAGE_SIZE
    offset = (page - 1) * ps

    raw_rows: list[dict[str, Any]]
    truncated = False

    if q:
        # SEARCH MODE — relevance order, no rank contract (see the block comment
        # at ``_bm25_hub_rows``).
        all_rows, truncated = _bm25_hub_rows(
            supabase,
            corpus="regulation",
            table=_REG_HUB_TABLE,
            select_cols=_REG_HUB_SELECT,
            q=q,
            apply_filters=lambda qb: _apply_reg_filters(qb, entity, doc_type, sector),
        )
        total = len(all_rows)
        raw_rows = all_rows[offset : offset + ps]
    else:
        # BROWSE — one query over the published-only ranked view. Never fetches
        # more than the 9 rows the page shows.
        try:
            total = _reg_count(supabase, entity, doc_type, sector, None)
            qb = supabase.table(_REG_HUB_TABLE).select(_REG_HUB_SELECT)
            qb = _apply_reg_filters(qb, entity, doc_type, sector)
            qb = qb.order("rank").order("id").range(offset, offset + ps - 1)
            raw_rows = qb.execute().data or []
        except Exception as e:  # noqa: BLE001
            logger.exception("Error listing regulations hub: %s", e)
            raise _hub_error()

    total_pages = max(1, math.ceil(total / ps)) if total else 1

    items: list[dict[str, Any]] = []
    for r in raw_rows:
        # The view is an INNER JOIN on a non-null slug, so this cannot be empty;
        # the guard stays as a cheap invariant check rather than a filter.
        if not r.get("slug"):
            continue
        items.append(_reg_hub_item(r))

    return _hub_result(
        items, page, total_pages, q=q, total=total, truncated=truncated
    )


# --- /regulations/{slug} doc page -----------------------------------------


def _legal_authority_basis(value: Any) -> Optional[str]:
    """Extract the human-readable decree citation from ``legal_authority``.

    The corpus stores ``legal_authority`` as a JSON-encoded analysis object
    (authority_level, authority_score, ...); the only field fit for public
    display is ``authority_basis`` — the decree citation, e.g.
    «مرسوم ملكي رقم (م/51) وتاريخ 23/8/1426هـ». Anything unparseable → None
    (the metadata row is simply omitted; raw JSON must never reach the page).
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s.startswith("{"):
        return s  # already a plain label
    try:
        basis = json.loads(s).get("authority_basis")
    except (ValueError, AttributeError):
        return None
    if isinstance(basis, str) and basis.strip():
        return basis.strip()
    return None


def _reg_metadata(reg: dict[str, Any]) -> list[dict[str, str]]:
    """Build the MetadataCard rows (label/value), skipping empty values."""
    md: list[dict[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value is None:
            return
        s = value if isinstance(value, str) else str(value)
        s = s.strip()
        if s:
            md.append({"label": label, "value": s})

    add("الجهة المصدرة", reg.get("entity_name"))
    add("نوع الوثيقة", map_doc_type_bucket(reg.get("doc_type_bucket")))
    add("الأساس النظامي", _legal_authority_basis(reg.get("legal_authority")))
    add("تاريخ السريان", reg.get("start_date"))
    sectors = reg.get("sectors") or []
    if sectors:
        add("القطاعات", "، ".join(str(s) for s in sectors if s))
    return md


def _regulation_article_index(
    supabase: SupabaseClient, regulation_id: str
) -> list[dict[str, Any]]:
    """The doc-page TOC → مادة-page link list — ONLY the PUBLISHED مواد.

    مادة pages are OPT-IN: an article has a public page only when an operator has
    published it (``scripts/publish_articles.py``), which sets a slug on its
    ``seo_item_meta`` sidecar row (``content_type='article'``,
    ``content_id='{regulation_id}#{article_no}'``). So this returns
    ``[{article_no, article_label, slug}, ...]`` — one entry per published sidecar
    article row for this regulation — ordered by ``article_no`` ascending, or
    ``[]`` when NONE are published (the default; the frontend then falls back to
    the chunk/article TOC without live links).

    Driven entirely by the sidecar (``content_id LIKE '{regulation_id}#%' AND slug
    NOT NULL``); the ``seo_articles`` index is NOT consulted here — publishing is
    validated against it up front by the publish script. ``article_no`` is parsed
    from the ``content_id`` suffix; ``article_label`` is derived («المادة {N}»).
    Fail-soft: a query error degrades to ``[]`` so the doc page still renders.
    Read-only.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    page = 1000
    try:
        while True:
            res = (
                supabase.table("seo_item_meta")
                .select("content_id, slug")
                .eq("content_type", "article")
                .like("content_id", f"{regulation_id}#%")
                .not_.is_("slug", "null")
                .order("content_id")
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = res.data or []
            rows.extend(batch)
            if len(batch) < page:
                break
            offset += page
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not load published article index (%s): %s", regulation_id, e)
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        slug = r.get("slug")
        cid = r.get("content_id") or ""
        if not slug or "#" not in cid:
            continue
        suffix = cid.rsplit("#", 1)[1]
        if not suffix.isdigit():
            continue
        no = int(suffix)
        out.append(
            {"article_no": no, "article_label": f"المادة {no}", "slug": slug}
        )
    out.sort(key=lambda x: x["article_no"])
    return out


def _seo_articles_for_regulation(
    supabase: SupabaseClient, regulation_id: str
) -> list[dict[str, Any]]:
    """Every ``seo_articles`` row of a regulation, ordered by ``article_no``.

    The derived per-مادة index (migration 097, built from ``articles_v2`` by
    ``scripts/build_seo_article_index.py``). Returns ``[]`` when the regulation has
    no rows (a chunk-only / article-less regulation — the caller then keeps the
    legacy chunk path). Fail-soft: a query error degrades to ``[]``. Read-only.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    page = 1000
    try:
        while True:
            res = (
                supabase.table("seo_articles")
                .select(
                    "article_no, article_label, slug, chunk_id, article_text, "
                    "extraction_status"
                )
                .eq("regulation_id", str(regulation_id))
                .order("article_no")
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = res.data or []
            rows.extend(batch)
            if len(batch) < page:
                break
            offset += page
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not load seo_articles (%s): %s", regulation_id, e)
        return []
    return rows


# Chunk streams. ``position`` is scoped PER STREAM, not per document: a
# regulation's appendix chunks (``corpus='appendix'``, ``chunk_ref`` ending
# ``_apx_NNN``) restart at position 1 alongside its body chunks
# (``with_articles`` / ``without_articles``, ``_chunk_NNN``). **1,184 regulations
# carry both streams**, so ordering by ``position`` ALONE interleaves the
# appendices into the body — on 17900_reg_128_p2 «ملحق رقم (1)» lands between
# المادة السادسة and المادة الثانية عشرة — and with no tiebreaker the pairing
# order is not even stable between requests, which an ISR page bakes in.
#
# ``corpus DESC`` is what puts the body first: alphabetically 'without_articles'
# > 'with_articles' > 'appendix', and a regulation carries at most ONE body
# stream plus an optional appendix. ``chunk_ref`` is the stable tiebreaker.
#
# ⚠ This leans on every body-stream name sorting AFTER 'appendix' descending. A
# new corpus value that doesn't (say 'annex') would silently reorder documents;
# ``test_chunk_stream_order_puts_body_before_appendix`` guards it.
_CHUNK_BODY_CORPORA = ("with_articles", "without_articles")
_CHUNK_APPENDIX_CORPUS = "appendix"


def _ordered_chunk_query(
    supabase: SupabaseClient, regulation_id: str, columns: str
) -> Any:
    """A ``chunks_v2`` query for one regulation, in DOCUMENT reading order.

    THE one place chunk order is defined. Every caller that renders chunks as a
    document must go through this — ordering by ``position`` alone is wrong
    whenever the regulation has an appendix (see the note above), and a bare
    ``.order("position")`` looks so obviously right that it will be
    reintroduced by anyone who has not read that note.

    Returns the query builder, NOT the rows, so callers can still ``.limit(3)``
    for a gated preview and get the first three sections of the DOCUMENT rather
    than the first three of an interleaved jumble.
    """
    return (
        supabase.table("chunks_v2")
        .select(columns)
        .eq("regulation_id", str(regulation_id))
        .order("corpus", desc=True)
        .order("position")
        .order("chunk_ref")
    )


def _chunk_row_map(
    supabase: SupabaseClient, chunk_ids: list[Any]
) -> dict[str, dict[str, str]]:
    """Batch-resolve ``{chunk_id: {"title", "content"}}`` from ``chunks_v2``.

    An article whose ``extraction_status != 'extracted'`` renders its owning chunk
    as the body — this fetches those chunk rows in one (chunked) ``IN`` lookup.
    Dedupes ids and chunks the ``in.(...)`` at 150 (PostgREST URL-length trap).
    Fail-soft: a blip yields ``{}`` (those sections render empty rather than 500).

    ``title`` is carried alongside ``content`` because a fallback chunk usually
    spans a RUN of مواد and titles itself accordingly («المادة (1) – المادة (4):
    التعاريف …») — ``_merge_article_sections`` uses it as the heading when it
    collapses such a run into one section.
    """
    ids = list(dict.fromkeys(str(c) for c in chunk_ids if c))
    if not ids:
        return {}
    out: dict[str, dict[str, str]] = {}
    for i in range(0, len(ids), 150):
        chunk = ids[i : i + 150]
        try:
            res = (
                supabase.table("chunks_v2")
                .select("id, title, content")
                .in_("id", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("chunk row map lookup failed: %s", e)
            continue
        for r in res.data or []:
            cid = r.get("id")
            if cid is not None:
                out[str(cid)] = {
                    "title": r.get("title") or "",
                    "content": r.get("content") or "",
                }
    return out


def _article_sections(
    articles: list[dict[str, Any]],
    chunk_rows: dict[str, dict[str, str]],
    *,
    gate: str,
    free_chars: int,
    merge_chunk_runs: bool,
) -> list[dict[str, Any]]:
    """Turn ``seo_articles`` rows into rendered sections. Pure (no DB).

    One section per مادة — ``{id: 'art-{no}', title, text, is_truncated,
    hidden_placeholder_lines, also_ids}`` — with the body taken from the extracted
    ``article_text`` when there is one, and from the owning chunk otherwise.

    ``merge_chunk_runs`` collapses a CHUNK-FALLBACK run. A fallback chunk is
    multi-مادة by nature (it titles itself «المادة (1) – المادة (4): …»), so
    emitting it once per مادة repeats the same paragraphs 4–5×. Bounded to a
    3-مادة preview that was invisible; across a whole open نظام it is ~94k chars of
    duplicate text on one page. Merged, the run becomes ONE section headed by the
    chunk's own مادة-range title, and the مواد it swallowed ride along in
    ``also_ids`` so the page can still emit an anchor for every TOC row.

    ``gate``/``free_chars`` are handed to ``truncate_for_gate``, so an ``'open'``
    gate returns every section whole and the hidden bytes of a gated one are
    dropped here, server-side, exactly as before.
    """
    sections: list[dict[str, Any]] = []
    # chunk_id → index in `sections` of the section already carrying that body.
    run_index: dict[str, int] = {}

    for a in articles:
        no = int(a.get("article_no") or 0)
        extracted = a.get("extraction_status") == "extracted" and a.get("article_text")

        if extracted:
            # Extracted single-مادة text → strip its duplicate heading + footnote
            # noise for DISPLAY, and never merge (it belongs to this مادة alone).
            body = _clean_article_display_text(a.get("article_text") or "")
            title = a.get("article_label")
        else:
            chunk_id = str(a.get("chunk_id") or "")
            if merge_chunk_runs and chunk_id and chunk_id in run_index:
                sections[run_index[chunk_id]]["also_ids"].append(f"art-{no}")
                continue
            row = chunk_rows.get(chunk_id) or {}
            body = row.get("content") or ""
            title = a.get("article_label")
            if merge_chunk_runs and chunk_id:
                # The chunk's own title names the مادة RANGE the merged section
                # actually contains; the single مادة label would under-describe
                # it. Only in the merged path — an unmerged section still stands
                # for one مادة and keeps its own label.
                title = (row.get("title") or "").strip() or title
                run_index[chunk_id] = len(sections)

        cut = truncate_for_gate(body, gate, free_chars=free_chars)
        sections.append(
            {
                "id": f"art-{no}",
                "title": title,
                "text": cut["visible_text"],
                "is_truncated": cut["is_truncated"],
                "hidden_placeholder_lines": cut["hidden_placeholder_lines"],
                "also_ids": [],
            }
        )

    return sections


def get_regulation_doc(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """Full /regulations/{slug} document payload, or ``None`` when the slug is
    unknown (the route turns ``None`` into a 404 «النظام غير موجود»).

    Resolves ``slug → content_id`` via the sidecar, loads the reg row, resolves the
    gate ONCE, and builds the reading surface from the BEST source:

      ARTICLES-FIRST (the regulation has ``seo_articles`` rows AND that index
      passes ``use_article_surface`` — mere existence is NOT enough):
        - ``toc``: EVERY مادة — ``{id: slug, title: article_label,
          position: article_no}`` — always free.
        - ``visible_sections``: EVERY مادة when the gate is ``'open'``, the first 3
          (by article_no) when it is ``'gated'``; each body run through
          ``truncate_for_gate(text, gate, free_chars=600)`` — text = the مادة's
          ``article_text`` (fallback: its owning chunk's content). id =
          ``'art-{no}'``. Gated bytes never leave the server.
        - ``hidden_section_count`` = 0 when open, else total مواد − 3.

      CHUNK FALLBACK — a regulation with NO ``seo_articles`` rows (article-less /
      chunk-only) **or** one whose index has too many holes to be trusted: the
      original chunk-based ``toc`` (id=chunk id) + every chunk (open) / the first
      3 (gated) as ``visible_sections``. The second case is what
      ``17900_reg_128_p2`` (اللائحة التنفيذية لنظام العمل ج2) hit — 68 rows for a
      232-مادة لائحة, so this page advertised «68 مادة» and silently dropped 164.

    ⚠ This branch and ``get_full_regulation``'s MUST agree. They share
    ``use_article_surface`` on the same inputs for that reason: if the anon page
    flips to chunks and the paid reveal does not, a reader who spent an unlock
    gets a structurally different document than the crawler saw.

    ⚠ OPEN MEANS OPEN. An ``'open'`` نظام ships whole here — full text and
    ``official_sources`` — to anonymous readers and crawlers alike, with
    ``hidden_section_count = 0`` and no truncation, which is what switches every
    downstream gate affordance off. Only a ``'gated'`` نظام gets the preview.

    ``article_index`` lists ONLY the PUBLISHED مواد (opt-in; empty by default) and
    is additive to either path.

    TWO CARD STRIPS ride at the foot of the payload, both lists of reg hub cards
    and both UNGATED (`.claude/plans/read_next_related_items.md`):
    ``cited_regulations`` («الأنظمة المذكورة» — أنظمة this نظام cites, one card
    per نظام, <= 7) and ``related_next`` («اقرأ تاليًا» — same-type neighbours,
    <= 7). They are DISJOINT: the citation strip resolves first and its ids are
    excluded from the other (D13), which is load-bearing precisely here because
    this is the one page where both strips hold أنظمة.
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    try:
        meta = (
            supabase.table("seo_item_meta")
            .select("content_id")
            .eq("content_type", "regulation")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        meta_rows = meta.data or []
        if not meta_rows:
            return None
        content_id = meta_rows[0].get("content_id")
        if not content_id:
            return None

        reg_res = (
            supabase.table("regulations_v2")
            .select(
                "id, reg_ref, clean_title, title, entity_name, doc_type_bucket, "
                "status_class, legal_authority, start_date, sectors, summary, "
                "llm_summary, landing_url, pdf_url"
            )
            .eq("id", content_id)
            .limit(1)
            .execute()
        )
        reg_rows = reg_res.data or []
        if not reg_rows:
            return None
        reg = reg_rows[0]
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading regulation doc (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب النظام",
        )

    gate = resolve_gate(supabase, "regulation", str(content_id))
    status = map_reg_status(reg.get("status_class"))
    article_index = _regulation_article_index(supabase, str(content_id))

    articles = _seo_articles_for_regulation(supabase, str(content_id))

    # An OPEN نظام is open END TO END — the whole statute ships in this anon/ISR
    # payload, so a crawler and a signed-out reader get every مادة and NOTHING on
    # the page offers a reveal. The 3-مادة preview below is the GATED reading
    # surface and only that; applying it to an open-tier نظام too was the bug that
    # put «سجّل مجانًا لعرض النظام كاملًا» on نظام العمل — a document nothing gates
    # — and kept 229 of its 232 مواد out of the crawlable HTML.
    #
    # `hidden_section_count = 0` + `is_truncated = False` everywhere is what turns
    # the CTA off downstream: the page derives `gated` from those, so there is no
    # separate "hide the button" flag to keep in sync.
    is_open = gate == "open"

    # Not `if articles:` — an index that exists but is full of holes renders from
    # chunks instead. Same helper, same args as `get_full_regulation` (see above).
    if use_article_surface(supabase, str(content_id), articles):
        # ARTICLES-FIRST — toc + (open: every مادة | gated: first-3) preview.
        toc = [
            {
                "id": str(a.get("slug") or ""),
                "title": a.get("article_label"),
                "position": int(a.get("article_no") or 0),
            }
            for a in articles
        ]
        rendered = articles if is_open else articles[:3]
        # Only fallback (non-'extracted') مواد need their owning chunk body.
        fallback_ids = [
            a.get("chunk_id")
            for a in rendered
            if a.get("extraction_status") != "extracted" or not a.get("article_text")
        ]
        chunk_rows = _chunk_row_map(supabase, fallback_ids)

        visible_sections = _article_sections(
            rendered,
            chunk_rows,
            gate=gate,
            free_chars=600,
            # Merge only on the open full render (see `_article_sections`): the
            # gated 3-مادة preview stays byte-identical to what it shipped before.
            merge_chunk_runs=is_open,
        )
        hidden_section_count = 0 if is_open else max(0, len(articles) - 3)
    else:
        # CHUNK FALLBACK — the legacy chunk-based toc + first-3-chunk preview.
        try:
            toc_res = _ordered_chunk_query(
                supabase, str(content_id), "id, title, position"
            ).execute()
            toc_rows = toc_res.data or []

            # Open → every chunk (the whole نظام); gated → the 3-chunk preview.
            vis_qb = _ordered_chunk_query(
                supabase, str(content_id), "id, title, position, content"
            )
            if not is_open:
                vis_qb = vis_qb.limit(3)
            vis_rows = vis_qb.execute().data or []
        except Exception as e:  # noqa: BLE001
            logger.exception("Error loading regulation doc chunks (%s): %s", slug, e)
            raise LunaHTTPException(
                status_code=500,
                code=ErrorCode.INTERNAL_ERROR,
                detail="حدث خطأ أثناء جلب النظام",
            )

        # ``position`` here is the DOCUMENT index (1..N in reading order), NOT the
        # raw ``chunks_v2.position``.
        #
        # ⚠ Emitting the raw column re-creates the interleave this query just
        # removed. The chunk position is scoped PER STREAM — a regulation's
        # appendix chunks restart at 1 alongside the body — and the doc page sorts
        # the TOC by this field (`app/regulations/[slug]/page.tsx:149`
        # `.sort((a, b) => a.position - b.position)`). So the rows arrive in
        # document order and the client shuffles «ملحق رقم (1)» straight back
        # between المادة السادسة and المادة الثانية عشرة. Sections looked right,
        # «محتويات النظام» did not, and the two disagreed with each other.
        #
        # A payload whose own sort key does not reproduce its own order is the
        # bug; renumbering here fixes it for every client rather than asking each
        # one to preserve array order. Migration 121 makes the same thing true of
        # the underlying data, after which this is belt-and-braces.
        toc = [
            {
                "id": str(r.get("id")),
                "title": r.get("title"),
                "position": i,
            }
            for i, r in enumerate(toc_rows, start=1)
        ]

        visible_sections = []
        for r in vis_rows:
            cut = truncate_for_gate(r.get("content") or "", gate, free_chars=600)
            visible_sections.append(
                {
                    "id": str(r.get("id")),
                    "title": r.get("title"),
                    "text": cut["visible_text"],
                    "is_truncated": cut["is_truncated"],
                    "hidden_placeholder_lines": cut["hidden_placeholder_lines"],
                    "also_ids": [],
                }
            )
        hidden_section_count = 0 if is_open else max(0, len(toc_rows) - 3)

    # WITHHELD FOR GATED ITEMS ONLY (user decision 2026-07-28, reversing the plan's
    # §1.2 "always shown"; narrowed to gated-only on 2026-08-01).
    #
    # The block is a per-item deep link carrying the source system's own id (the
    # BOE law UUID; an opaque encrypted NCAR document id), so across the GATED
    # corpus it is a slug → official-ID crosswalk, and it is served instead by
    # ``official_sources_for_item`` through the authed reveal.
    #
    # An OPEN نظام has no crosswalk to protect: this payload already carries its
    # entire text to anonymous crawlers, and a document that is open end-to-end
    # cannot credibly hide the link to its own official source. Open items never
    # reveal (nothing is gated on them), so exactly one renderer still fires per
    # page — here for open, the reveal for gated.
    #
    # Not viewer-dependent either way: this is Layer A (a property of the item,
    # not the caller), so the ISR payload stays cacheable.
    official_sources: list[dict[str, str]] = []
    if is_open and reg.get("landing_url"):
        official_sources.append({"title": "الموقع الرسمي", "href": reg["landing_url"]})

    # THE TWO STRIPS, AND THE ORDER IS THE CONTRACT (§5.4 · D13). «الأنظمة
    # المذكورة» resolves FIRST and wins; «اقرأ تاليًا» is told what it already
    # rendered and backfills past it. THIS IS THE ONE PAGE WHERE THAT DEDUP DOES
    # ANYTHING — both strips hold أنظمة here, and a نظام that cites its
    # لائحة is also the نظام most likely to be its top related neighbour, so
    # without the exclusion the same card renders twice, one above the other.
    #
    # Both are ungated and both are fail-soft: they return `[]`, never raise, so
    # a related-items outage costs two strips and not the statute.
    cited_regulations, cited_ids = _regulation_cited_regulations(
        supabase, str(content_id)
    )
    related_next = get_related_next(
        supabase, "regulation", str(content_id), exclude_ids=cited_ids
    )

    return {
        "slug": slug,
        "title": (reg.get("clean_title") or reg.get("title") or "").strip(),
        "status": status,
        # status_raw = the underlying status_class value (e.g. 'in_force_amended')
        # so the frontend keeps the draft sub-state distinction; NOT the separate
        # regulations_v2.status_raw source column.
        "status_raw": reg.get("status_class"),
        "metadata": _reg_metadata(reg),
        "summary_md": reg.get("llm_summary") or reg.get("summary"),
        "gate": gate,
        "toc": toc,
        # article_index links each مادة page from the doc TOC (empty until the
        # seo_articles index is built for this regulation). Additive — existing
        # fields are unchanged.
        "article_index": article_index,
        "visible_sections": visible_sections,
        "hidden_section_count": hidden_section_count,
        "official_sources": official_sources,
        "draft_notice": status == "draft",
        # «الأنظمة المذكورة» — أنظمة this نظام cites, one card per نظام, <= 7.
        "cited_regulations": cited_regulations,
        # «اقرأ تاليًا» — same-type neighbours, <= 7, published only.
        "related_next": related_next,
    }


# ==========================================================================
# /compliance — THE SERVICE-GUIDES WING (`service_guides`, migration 142)
# (.claude/plans/compliance_service_guides.md · §0 §1 §4.1)
#
# «دليل مبسط لأكثر الخدمات استخداماً» — 169 guides to the most-used Saudi
# government services, each one RAYHAN'S OWN AUTHORED REWRITE of the issuing
# entity's official PDF user-guide, with our own screenshot pipeline
# (`service_guide_images`, 3,180 rows). Published IN FULL and UNGATED: no
# `resolve_gate` call, no `truncate_for_gate`, no CTA wall on the body. This is
# the wing's SEO bet — the whole guide is the ranking food.
#
# ⚠ THE FOUNDING RULE OF THE OLD WING IS SUPERSEDED, NOT BENT. The 2026-08-03
# retirement was of a wing that REPUBLISHED the `services` corpus — الشروط /
# المستندات المطلوبة / الخطوات, someone else's procedure text restated under
# ريحان's chrome, stale the moment the entity edits it. A service GUIDE is
# different in kind: we wrote it. What survives from that decision is the shape
# of the outbound link, and it is absolute:
#
#   * the ONLY outbound link is the service's own page (`services.service_url`),
#     surfaced as «صفحة الخدمة على موقع الجهة الرسمي»;
#   * `source_pdf_url` IS NEVER SURFACED. `library_compliance_v` does not even
#     select the column, so the payload cannot carry it by accident. Do not add
#     it to the view, to `_COMPLIANCE_DOC_SELECT`, or to any response model.
#   * `services.steps` / `requirements` / `required_documents` stay
#     retrieval-only — behind the agent, never on a public page.
#
# The wing reads ONE relation, `library_compliance_v` (= `service_guides` ⋈
# `services` on the canonical rows), because the sector axis, the provider and
# the service URL live on `services` while the guide body lives on
# `service_guides`, and both tables are PIPELINE-OWNED (the ingest rebuilds
# them). App-owned shape stays in the view and in the `seo_item_meta` sidecar.
# ==========================================================================

COMPLIANCE_WING_READY = True
"""The wing serves real guides (2026-08-19). Was ``COMPLIANCE_TABLE_READY``.

The old name promised a `compliance_table` that will never exist:
``service_guides`` IS the table the wing was waiting for. Kept as a named
constant — rather than deleted — because the frontend's empty-wing robots rule,
the sitemap sections and ``_SECTION_SOURCES`` all flipped together with it, and
a future reader tracing "when did /compliance turn on" lands here.
"""

# The wing's read surface. NOTE what is absent: `source_pdf_url`. See the block
# comment above — the view is the structural half of that guarantee and this
# select list is the other half.
_COMPLIANCE_HUB_TABLE = "library_compliance_v"

# Hub cards never fetch `guide_md` (169 bodies averaging several KB is a page
# render's worth of bytes for a 9-card grid that shows none of it).
_COMPLIANCE_HUB_SELECT = (
    "id, service_ref, title, summary, image_count, most_used_rank, "
    "provider_name, sectors"
)

# The document page. `service_url` joins here; `source_pdf_url` does not exist
# on the view at all.
_COMPLIANCE_DOC_SELECT = (
    "id, title, summary, guide_md, image_count, provider_name, service_url"
)

# Storage bucket holding the screenshots. PUBLIC (flipped 2026-08-18), so the
# URLs are plain and permanent — no signing, no expiry, nothing per-caller in the
# payload. That is what lets the guide page keep the shared anon hour-cache.
_GUIDE_IMAGE_BUCKET = "service-guide-images"

# THE ONE REGEX (REFERENCE.md §3.1). A hole is a line that is ONLY a
# `{guide_ref}_{n}` token. Never anchor on «الصورة {n}»: 2,804 of those sit
# INSIDE prose sentences and substituting there would rewrite normal Arabic into
# image tags. Never key on position either — 28% of guides place their holes out
# of numeric order, so "the 3rd image in the document" is not `image_index = 3`.
# Resolution is by `image_ref` and by nothing else.
_GUIDE_HOLE_RE = re.compile(r"^[ \t]*(\d+_\d+)[ \t]*$", re.M)


def _guide_image_base() -> str:
    """Public-object URL prefix for the screenshots bucket.

    Built from ``SUPABASE_URL`` (the config validator has already stripped its
    trailing slash) so the project ref is never hardcoded — a restore into a
    different project must not need a code change to find its own images.
    """
    return (
        f"{get_settings().SUPABASE_URL}/storage/v1/object/public/"
        f"{_GUIDE_IMAGE_BUCKET}"
    )


def _strip_unresolved_holes(guide_md: str, known_refs: set[str]) -> str:
    """Blank every hole line whose ``image_ref`` has no image row.

    DEFENSE IN DEPTH, and it is the failure mode this whole design exists to
    prevent: a raw ``223719_1`` token rendered onto a user-facing page. The
    renderer (``GuideBody.tsx``) applies the same rule client-side — REFERENCE.md
    §3.2 rules 1–2 — but a payload that carries an unresolvable token is already
    one bad `split()` away from printing it, so the server does not ship one.

    Today the removed set is EMPTY on every guide (invariant §8: every hole has
    exactly one image row and every row's token appears as a hole), which is
    exactly why this must be code and not a comment: nothing else would notice
    the day an ingest rebuild breaks the pairing.

    The matched LINE is replaced with the empty string (the newline around it
    stays), matching the reference implementation. Pure function.
    """
    if not guide_md:
        return guide_md or ""
    return _GUIDE_HOLE_RE.sub(
        lambda m: m.group(0) if m.group(1) in known_refs else "", guide_md
    )


def _compliance_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    """Wing ordering: ``most_used_rank`` ASC, then ``title``, then ``id``.

    Rank is the government portal's own popularity order (lower = more used), so
    page 1 is «أكثر الخدمات استخداماً» rather than an alphabetical accident — the
    same reasoning as ``seo_item_meta.rank`` on /regulations, with the number
    supplied by the corpus instead of by our citation log. A NULL rank sorts
    LAST (there are none live; ordering must still be total, since the alternative
    is a page whose contents shuffle between two requests).
    """
    raw = row.get("most_used_rank")
    try:
        rank = int(raw)
        missing = 0
    except (TypeError, ValueError):
        rank = 0
        missing = 1
    return (missing, rank, (row.get("title") or ""), str(row.get("id") or ""))


def _compliance_matches(
    row: dict[str, Any],
    provider: Optional[str],
    sector: Optional[str],
    entity: Optional[str] = None,
) -> bool:
    """The hub filters, in Python — see ``_compliance_published_rows``.

    ``sector`` = array containment on ``sectors``, the §7.1 convention every wing
    spells the same way (the value is the RAW Arabic sector name, already
    resolved from its Latin slug by the route).

    ⚠ ``provider`` AND ``entity`` ARE TWO AXES OVER ONE COLUMN, WITH TWO
    DELIBERATELY DIFFERENT PREDICATES. Do not collapse them:

      * ``provider`` is a FREE-TEXT FACET — a case-insensitive SUBSTRING of
        ``provider_name`` (the ``ilike`` the other wings push into PostgREST),
        >= 3 chars, anon-available, unbounded in value space. «التجارة» matching
        both وزارة التجارة and المركز السعودي للأعمال الاقتصادية is correct
        behaviour for a facet.
      * ``entity`` is a SECTION — the EXACT ``provider_name`` a slug from the
        closed 28-value vocabulary (``shared/library/entities.py``) claims,
        compared with ``==``. It is exact BY CONSTRUCTION, and that is the whole
        argument that lets the axis report real counts to an anonymous caller
        (`.claude/plans/compliance_entity_sections.md` §2/D1): 28 fixed numbers
        that move only when the corpus does are not an enumeration oracle. Make
        this a substring and the counts stop being fixed — «وزارة» would fold
        eleven ministries into one «section» whose total drifts with the query —
        and the exemption that keeps this axis out of the ``filtered`` flag no
        longer holds.

    ``q`` IS NO LONGER HANDLED HERE — see ``_compliance_published_rows``, which
    now takes the BM25 path. This function is the POST-FILTER applied to whatever
    candidate set that produced.
    """
    if provider:
        if provider.lower() not in (row.get("provider_name") or "").lower():
            return False
    if entity:
        # Exact, not ``ilike``. See the block above; and note the comparison is on
        # the STRIPPED value because the vocabulary stores the corpus string
        # verbatim, harakat included (`taqeem`'s fatha is a live example).
        if (row.get("provider_name") or "").strip() != entity:
            return False
    if sector:
        sectors = [str(s) for s in (row.get("sectors") or [])]
        if sector not in sectors:
            return False
    return True


def _compliance_published_rows(
    supabase: SupabaseClient,
    select_cols: str,
    provider: Optional[str] = None,
    sector: Optional[str] = None,
    q: Optional[str] = None,
    entity: Optional[str] = None,
) -> tuple[list[dict[str, Any]], dict[str, str], bool]:
    """``(filtered+ordered published rows, {id: slug}, truncated)`` for the wing.

    BROWSE (``q`` absent) — SAMPLE MODE ALL THE WAY DOWN, and permanently so: the
    wing is 337 rows, one guide per guided service, which is an order of
    magnitude below ``SAMPLE_MODE_MAX_IDS``. So the published set comes from the
    sidecar (``_published_ids('compliance')``), the rows come back in one or two
    ``id IN (...)`` chunks, and the filtering, ordering and 9-item slicing all
    happen in Python. That is the same shape ``list_circulars_hub`` uses for its
    sample, and it is what guarantees a page is never mysteriously empty (the
    failure that made sample mode exist: paginating a corpus whose first pages
    hold none of the published rows). Ordering is ``_compliance_sort_key``.

    ⚠ SEARCH (``q`` present) — THE PREMISE REVERSED ON 2026-08-23. This function
    used to hand ``q`` to ``_compliance_matches`` as a substring over
    ``title + summary``, under a comment that read «``q`` HERE IS NOT BM25 AND
    MUST NOT BECOME IT», because the guides were deliberately absent from
    ``search_index`` (the wing's own plan §9 held the corpus decision back). THAT
    PREMISE IS NOW VOID: the guides joined ``search_index`` as the ``compliance``
    corpus (`.claude/plans/compliance_entity_sections.md` §6, migration applied
    2026-08-23), so ``q`` takes the SAME ``corpus_search_ids`` → ``rank_map``
    path every other wing takes, via ``_bm25_hub_rows``. The comment is rewritten
    rather than deleted so the reversal, and its date, stay on the record — the
    substring was never a style choice, it was the honest answer while the corpus
    was missing.

    What the swap changes, and what it deliberately does not:

      * ORDER. A search result set comes back in BM25 score order and the
        ``most_used_rank`` contract does NOT apply to it — a result list ordered
        by anything other than relevance is not a result list. Browse ordering is
        untouched.
      * MATCHING. Arabic normalization + IDF instead of a literal substring, and
        the whole ``guide_md`` body is now searchable rather than just
        ``title + summary``.
      * FILTERS. ``provider`` / ``sector`` / ``entity`` stay POST-FILTERS over the
        ranked candidates, exactly as on the other wings — the RPC's facets are
        not a superset of what these predicates mean, and pushing them down would
        quietly change them.
      * PUBLICATION. Only slugged rows are in the index, so the ranked set is
        already the published set; the ``slugs`` map below still drops anything
        unslugged, which costs one dict lookup and removes a whole failure mode.
      * ``truncated`` is True when the ranked id set came back AT
        ``HUB_SEARCH_LIMIT`` (200) — reachable now that the wing is 337 guides,
        which is why the third tuple element exists at all. It feeds
        ``total_count_is_exact``: ``len(rows)`` is then a FLOOR, and a UI printing
        it as «200 نتيجة» would be inventing a number.

    ``q`` is registered-only either way — the ROUTE drops it for anon before this
    module ever sees it (``public_library._search_query``), which is why there is
    no anon branch here and must not be a second, drifting check.

    The slug map is computed over the CANDIDATE set rather than the served page,
    because it does double duty — it carries the card's ``slug`` AND it is the
    fallback publication filter for the one path where ``_published_ids`` cannot
    answer (a sidecar blip returns ``None``; the whole view is read instead and
    the unslugged rows are dropped here). Both paths therefore list exactly the
    slugged guides, never a row with no public URL.
    """
    truncated = False

    if q:
        # SEARCH MODE — relevance order (see the ``_bm25_hub_rows`` block
        # comment). The candidate fetch is by id, so ``apply_filters`` is a no-op
        # and every predicate runs as a post-filter below, unchanged.
        rows, truncated = _bm25_hub_rows(
            supabase,
            corpus="compliance",
            table=_COMPLIANCE_HUB_TABLE,
            select_cols=select_cols,
            q=q,
            apply_filters=lambda qb: qb,
        )
    else:
        pub_ids = _published_ids(supabase, "compliance")
        if pub_ids is not None:
            rows = (
                _fetch_corpus_by_ids(
                    supabase, _COMPLIANCE_HUB_TABLE, select_cols, pub_ids, lambda qb: qb
                )
                if pub_ids
                else []
            )
        else:
            try:
                res = (
                    supabase.table(_COMPLIANCE_HUB_TABLE).select(select_cols).execute()
                )
                rows = res.data or []
            except Exception as e:  # noqa: BLE001
                logger.exception("Error reading %s: %s", _COMPLIANCE_HUB_TABLE, e)
                raise _hub_error()

    slugs = _slug_map(supabase, "compliance", [r.get("id") for r in rows])
    kept = [
        r
        for r in rows
        if slugs.get(str(r.get("id")))
        and _compliance_matches(r, provider, sector, entity)
    ]
    if not q:
        # BROWSE only. Re-sorting a search result by ``most_used_rank`` would
        # throw away the ranking ``_bm25_hub_rows`` just imposed.
        kept.sort(key=_compliance_sort_key)
    return kept, slugs, truncated


def _compliance_hub_item(row: dict[str, Any], slug: str) -> dict[str, Any]:
    """One /compliance card from a ``_COMPLIANCE_HUB_SELECT`` row.

    See the block comment above ``_reg_hub_item`` — one definition, two readers.
    ``slug`` is PASSED IN rather than read off the row: ``library_compliance_v``
    carries no slug column, so both readers resolve it through the sidecar
    (``_slug_map`` / ``_compliance_published_rows``) and hand it here.
    """
    try:
        image_count = int(row.get("image_count") or 0)
    except (TypeError, ValueError):
        image_count = 0
    return {
        "slug": slug,
        "title": (row.get("title") or "").strip(),
        "provider_name": row.get("provider_name"),
        "summary": _text_snippet(row.get("summary"), 220),
        "image_count": image_count,
    }


def compliance_hub_total_pages(
    supabase: SupabaseClient,
    provider: Optional[str] = None,
    sector: Optional[str] = None,
    q: Optional[str] = None,
    entity: Optional[str] = None,
) -> int:
    """Total hub pages for the filtered guide set (for the anon-cap body).

    Counts exactly the set ``list_compliance_hub`` paginates — same published
    ids, same Python filters, same ``entity`` predicate — so the page count and
    the pages actually served cannot disagree (§12.2's failure was a wall
    reporting one total while the paginator walked another). ``ceil(n / 9)``,
    floored at ``1``: the paginator and the CTA wall both read this as "how many
    pages exist", and zero pages renders as a broken paginator rather than as one
    empty page.
    """
    try:
        rows, _slugs, _truncated = _compliance_published_rows(
            supabase,
            "id, title, summary, provider_name, sectors",
            provider,
            sector,
            q,
            entity,
        )
        total = len(rows)
    except LunaHTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Error counting compliance hub: %s", e)
        raise _hub_error()
    return max(1, math.ceil(total / HUB_PAGE_SIZE)) if total else 1


def list_compliance_hub(
    supabase: SupabaseClient,
    *,
    page: int = 1,
    provider: Optional[str] = None,
    sector: Optional[str] = None,
    q: Optional[str] = None,
    entity: Optional[str] = None,
) -> dict[str, Any]:
    """One page (9 cards) of the /compliance hub — «دليل الخدمات».

    Ordering = ``most_used_rank`` ascending (most-used service first), tiebroken
    ``(title, id)`` — see ``_compliance_sort_key``. Filters: ``provider``
    (free-text SUBSTRING of the issuing entity's name), ``entity`` (the EXACT
    ``provider_name`` of one of the 28 sections — a different axis with a
    different predicate; see ``_compliance_matches``), ``sector`` (§7.1
    containment on the joined ``services.sectors``) and ``q`` (BM25 over the
    ``compliance`` corpus since 2026-08-23, registered-only — the route drops it
    for anon). Only slugged (published) guides are listed.

    ⚠ ``entity`` DOES NOT CHANGE THE ORDERING. A section is the same wing with a
    narrower base set: ``most_used_rank`` still decides page 1, so
    /compliance/ministry-of-justice opens on the 115 justice guides people
    actually file. The section does not get its own sort contract.

    SEARCH MODE (``q`` present, therefore an authenticated caller — D9): ids come
    from ``bm25_search()`` in score order and the rank contract does not apply;
    ``provider``/``entity``/``sector`` still post-filter those candidates.

    Card = ``{slug, title, provider_name, summary, image_count}``. ``summary`` is
    the guide's own one-paragraph abstract, cut to a card-sized snippet at a word
    boundary (the same ``_text_snippet`` treatment every other wing's card text
    gets — a card is not the page). ``image_count`` is how many screenshots the
    guide carries, and the frontend needs it to decide whether the title reads
    «الدليل الشامل بالصور» or plain «الدليل الشامل» (10 guides are legitimately
    text-only, and promising صور on one of those is the lie that carve-out
    exists to prevent).

    NOTHING HERE IS GATED. There is no ``resolve_gate`` call in this wing and no
    truncation anywhere in it — the cards, the bodies and the screenshots are all
    open to anon by design. The only limits that apply are the wing-agnostic ones
    the ROUTE owns: the browse-depth cap and the per-user item budget.
    """
    page = max(1, int(page or 1))
    ps = HUB_PAGE_SIZE
    offset = (page - 1) * ps

    all_rows, slugs, truncated = _compliance_published_rows(
        supabase, _COMPLIANCE_HUB_SELECT, provider, sector, q, entity
    )
    total = len(all_rows)
    total_pages = max(1, math.ceil(total / ps)) if total else 1
    rows = all_rows[offset : offset + ps]

    items: list[dict[str, Any]] = []
    for r in rows:
        slug = slugs.get(str(r.get("id")))
        if not slug:
            continue
        items.append(_compliance_hub_item(r, slug))

    # ``total_count`` rides the envelope only for a search. ⚠ IT IS NO LONGER
    # ALWAYS EXACT: this wing's ``q`` was an exhaustive substring pass until
    # 2026-08-23 and is now a BM25 set cut at ``HUB_SEARCH_LIMIT`` (200) over 337
    # guides, so ``truncated`` is reachable and has to be carried — a UI printing
    # a ceiling as «200 نتيجة» is asserting a number the backend does not know.
    return _hub_result(
        items, page, total_pages, q=q, total=total, truncated=truncated
    )


def compliance_entity_counts(supabase: SupabaseClient) -> dict[str, int]:
    """PUBLISHED guide count per entity slug — all 28, in ``ENTITY_ORDER``.

    Feeds the «تصفّح حسب الجهة» grid and every entity section's ``total_pages``
    (`.claude/plans/compliance_entity_sections.md` §4.1). ONE read of the whole
    published set (337 rows, two ``id IN (...)`` chunks) grouped in Python, then
    behind the route's 5-minute memo — NOT 28 ``count='exact'`` head queries the
    way ``court_counts`` does it. The two wings differ because their listers do:
    /judgments pages a ranked DB view, so a per-bucket count is one index-only
    query; /compliance is sample-mode all the way down and already fetches every
    published row for any hub page, so grouping that same read is strictly
    cheaper than 28 round-trips and cannot disagree with the lister by
    construction.

    ⚠ THESE ARE COUNTS OF WHAT IS SERVABLE — the same ``_compliance_published_rows``
    set ``list_compliance_hub`` paginates, so «وزارة العدل 115» and that section's
    last page agree. The guide numbers in ``shared/library/entities.py``'s
    comments document the CORPUS as of 2026-08-22 and are not read by anything;
    they will drift, and that is fine.

    Every slug is present, seeded to zero: an entity whose guides all lost their
    slugs still renders (at zero) rather than vanishing from the grid — the
    contract ``court_counts`` and ``sector_counts`` both hold. A ``provider_name``
    the vocabulary does not claim is counted into NO bucket and logged once by
    ``shared.library.entities`` at import; its guides stay reachable through the
    unfiltered hub, the sitemap and search.

    Fail-soft: a read error costs the grid's numbers, never the page — the caller
    memoises whatever comes back and the tiles render without counts.
    """
    counts: dict[str, int] = {slug: 0 for slug in ENTITY_ORDER}
    try:
        rows, _slugs, _truncated = _compliance_published_rows(
            supabase, "id, provider_name"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("compliance entity counts failed: %s", e)
        return counts
    for row in rows:
        slug = slug_for_provider(row.get("provider_name"))
        if slug is not None:
            counts[slug] += 1
    return counts


def get_compliance_guide(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """Full /compliance/{slug} guide payload, or ``None`` for an unknown slug
    (the route turns ``None`` into a 404 «الدليل غير موجود»).

    Resolves ``slug → content_id`` through the sidecar (``content_type
    ='compliance'``, ``content_id = service_guides.id``), reads the guide row from
    ``library_compliance_v`` and its screenshots from ``service_guide_images``
    ordered by ``image_index``. Read-only, no counters.

    Returns::

        {slug, title, summary, provider_name, service_url, image_count,
         guide_md, images: [{image_ref, description, url, width, height}],
         related_next: [ComplianceHubItem]}

    ``related_next`` is «اقرأ تاليًا» — <= 7 published خدمات, same-type only
    (D2). THERE IS NO ``cited_regulations`` KEY on this wing and there must not
    be one: no corpus carries نظام citations for a خدمة (D14).

    Three properties of that payload are decisions, not details:

      * NO ``source_pdf_url``. Not in the view, not in the select, not in the
        dict. The entity's SERVICE page is the only outbound link.
      * NO GATE AND NO TRUNCATION. ``guide_md`` ships whole to anon. Adding a
        ``resolve_gate`` call here would silently re-gate an open wing; the
        signup carrot on this wing is the chat, not a hidden half of the guide.
      * ``guide_md`` HAS BEEN HOLE-SWEPT (``_strip_unresolved_holes``): every
        remaining ``\\d+_\\d+`` line has a matching entry in ``images``, so a
        renderer that resolves by ``image_ref`` cannot print a raw token.

    ``image_count`` is what this payload ACTUALLY CARRIES (``len(images)``), not
    the corpus counter — the title treatment «بالصور» is derived from it, so it
    must describe the bytes being shipped. The two agree across the live corpus
    (invariant §8: 3,180 rows, every hole paired, ``uploaded_at`` never null).
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    try:
        meta = (
            supabase.table("seo_item_meta")
            .select("content_id")
            .eq("content_type", "compliance")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        meta_rows = meta.data or []
        if not meta_rows:
            return None
        content_id = meta_rows[0].get("content_id")
        if not content_id:
            return None

        guide_res = (
            supabase.table(_COMPLIANCE_HUB_TABLE)
            .select(_COMPLIANCE_DOC_SELECT)
            .eq("id", content_id)
            .limit(1)
            .execute()
        )
        guide_rows = guide_res.data or []
        if not guide_rows:
            return None
        guide = guide_rows[0]

        # `image_index` is a stable LABEL, ordered here for a predictable listing;
        # the holes are still resolved by `image_ref` (REFERENCE.md §4.2). The
        # biggest guide carries 69 screenshots, so the PostgREST 1,000-row clamp
        # is never in play.
        img_res = (
            supabase.table("service_guide_images")
            .select("image_ref, description, storage_path, width, height")
            .eq("guide_id", content_id)
            .order("image_index", desc=False)
            .execute()
        )
        image_rows = img_res.data or []
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading compliance guide (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب الدليل",
        )

    base = _guide_image_base()
    images: list[dict[str, Any]] = []
    for row in image_rows:
        image_ref = str(row.get("image_ref") or "").strip()
        storage_path = str(row.get("storage_path") or "").strip()
        # No token or no bytes ⇒ no image, and therefore no resolvable hole. The
        # sweep below drops the matching line rather than shipping a dead <img>.
        if not image_ref or not storage_path:
            continue
        images.append(
            {
                "image_ref": image_ref,
                # The description is the ALT TEXT: a real Arabic sentence
                # describing the screenshot (188–1,031 chars, never empty), which
                # is what keeps a guide usable with images off entirely.
                "description": (row.get("description") or "").strip(),
                "url": f"{base}/{urllib.parse.quote(storage_path, safe='/')}",
                "width": row.get("width"),
                "height": row.get("height"),
            }
        )

    guide_md = _strip_unresolved_holes(
        guide.get("guide_md") or "", {im["image_ref"] for im in images}
    )

    # «اقرأ تاليًا» — خدمات only (D2). THERE IS NO «الأنظمة المذكورة» ON THIS WING
    # and there must not be one: `cross_references_v2` carries `case` and
    # `reg_chunk` sources and nothing else, so a خدمة has no citation data at all
    # (D14). The field is ABSENT from `ComplianceGuideDoc`, not shipped empty —
    # an empty list would read as "this guide cites nothing", which is a claim we
    # cannot make. Extracting نظام mentions from guide prose is a separate project.
    related_next = get_related_next(supabase, "compliance", str(content_id))

    return {
        "slug": slug,
        "title": (guide.get("title") or "").strip(),
        "summary": (guide.get("summary") or "").strip(),
        "provider_name": guide.get("provider_name"),
        "service_url": guide.get("service_url"),
        "image_count": len(images),
        "guide_md": guide_md,
        "images": images,
        "related_next": related_next,
    }


# ==========================================================================
# PHASE 3 — مادة (ARTICLE) PAGES (/regulations/{slug}/articles/{article_slug})
#
# The library's highest-value template: one page per مادة. Body gating flows
# through the SAME resolve_gate / truncate_for_gate path as every other content
# type — the article's gate is resolved under content_type='article',
# content_id='{regulation_id}#{article_no}', inheriting the PARENT regulation's
# tier when it has no own override (migration 097 gating-key convention). Read-
# only: seo_articles is derived (built by scripts/build_seo_article_index.py) and
# the corpus (chunks_v2, regulations_v2) is never written.
# ==========================================================================


def _article_nav(
    supabase: SupabaseClient, regulation_id: str, article_no: int, *, after: bool
) -> Optional[dict[str, Any]]:
    """The prev/next مادة within a regulation, by ``article_no``.

    ``after=True`` → the smallest ``article_no`` greater than ``article_no``
    (next); ``after=False`` → the largest smaller (prev). Returns
    ``{"slug", "article_label"}`` or ``None`` at an end / on error (fail-soft:
    navigation is a nicety, never a reason to 500 the page)."""
    try:
        qb = supabase.table("seo_articles").select("slug, article_label").eq(
            "regulation_id", str(regulation_id)
        )
        if after:
            qb = qb.gt("article_no", article_no).order("article_no", desc=False)
        else:
            qb = qb.lt("article_no", article_no).order("article_no", desc=True)
        res = qb.limit(1).execute()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "article nav lookup failed (%s @%s after=%s): %s",
            regulation_id, article_no, after, e,
        )
        return None
    rows = res.data or []
    if not rows:
        return None
    r = rows[0]
    return {"slug": r.get("slug"), "article_label": r.get("article_label")}


def _sharh_teaser(
    supabase: SupabaseClient, regulation_id: str, article_no: int
) -> dict[str, Any]:
    """The anon شرح teaser for one مادة — gate #3, so the FULL شرح NEVER leaves here.

    Looks up the cached ``seo_sharh`` row by ``(regulation_id, article_no)`` (the
    stable key, migration 100) and returns ``{has_sharh, teaser, hidden_placeholder
    _lines}``:
      - no cached row (or empty body) → ``{False, None, 0}`` (the anon page renders
        no شرح block — generation is NOT triggered on read; see
        ``scripts/generate_sharh.py``).
      - cached → a whitespace-cut ``SHARH_TEASER_CHARS`` (~170) preview only, sized
        by ``truncate_for_gate`` so the hidden remainder is DROPPED, never shipped.
        A 150–300-word شرح always exceeds the teaser budget → ``has_sharh=True`` with
        a real placeholder-line count for the signup-gated bars.

    Fail-soft: a lookup error degrades to "no شرح" rather than 500ing the مادة page.
    """
    try:
        res = (
            supabase.table("seo_sharh")
            .select("sharh_md")
            .eq("regulation_id", str(regulation_id))
            .eq("article_no", int(article_no))
            .limit(1)
            .execute()
        )
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "sharh teaser lookup failed (%s @%s): %s", regulation_id, article_no, e
        )
        return {"has_sharh": False, "teaser": None, "hidden_placeholder_lines": 0}

    if not rows:
        return {"has_sharh": False, "teaser": None, "hidden_placeholder_lines": 0}
    full = (rows[0].get("sharh_md") or "").strip()
    if not full:
        return {"has_sharh": False, "teaser": None, "hidden_placeholder_lines": 0}

    # Always gate the شرح (value-add), independent of the article's body gate. The
    # full sharh_md is intentionally passed ONLY to truncate_for_gate, which returns
    # just the visible teaser — the remainder is never placed in the payload.
    cut = truncate_for_gate(full, "gated", free_chars=SHARH_TEASER_CHARS)
    return {
        "has_sharh": True,
        "teaser": cut["visible_text"],
        "hidden_placeholder_lines": cut["hidden_placeholder_lines"],
    }


def get_regulation_article(
    supabase: SupabaseClient, slug: str, article_slug: str
) -> Optional[dict[str, Any]]:
    """Full /regulations/{slug}/articles/{article_slug} payload, or ``None`` when
    the regulation slug or the article slug is unknown (route → 404
    «المادة غير موجودة»).

    Resolves the regulation via its sidecar slug (same pattern as
    ``get_regulation_doc``), then the ``seo_articles`` row by
    ``(regulation_id, slug=article_slug)``. The gate is resolved ONCE via
    ``resolve_gate('article', '{regulation_id}#{article_no}',
    parent_regulation_id=regulation_id)`` and the body is truncated by
    ``truncate_for_gate(..., free_chars=ARTICLE_FREE_CHARS)``. Body text = the
    extracted ``article_text`` when ``extraction_status='extracted'``, otherwise
    the owning chunk's full ``content`` (``is_fallback_body=True``). ``context_title``
    is the owning chunk's title; ``prev``/``next`` navigate by ``article_no``.
    Read-only, no counters.
    """
    slug = (slug or "").strip()
    article_slug = (article_slug or "").strip()
    if not slug or not article_slug:
        return None

    try:
        meta = (
            supabase.table("seo_item_meta")
            .select("content_id")
            .eq("content_type", "regulation")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        meta_rows = meta.data or []
        if not meta_rows:
            return None
        regulation_id = meta_rows[0].get("content_id")
        if not regulation_id:
            return None

        art_res = (
            supabase.table("seo_articles")
            .select(
                "article_no, article_label, slug, chunk_id, article_text, "
                "extraction_status"
            )
            .eq("regulation_id", str(regulation_id))
            .eq("slug", article_slug)
            .limit(1)
            .execute()
        )
        art_rows = art_res.data or []
        if not art_rows:
            return None
        art = art_rows[0]

        reg_res = (
            supabase.table("regulations_v2")
            .select("clean_title, title, status_class")
            .eq("id", regulation_id)
            .limit(1)
            .execute()
        )
        reg_rows = reg_res.data or []
        reg = reg_rows[0] if reg_rows else {}

        # The owning chunk supplies context_title always, and the body itself
        # when extraction fell back to the whole chunk.
        chunk = {}
        chunk_id = art.get("chunk_id")
        if chunk_id:
            ch_res = (
                supabase.table("chunks_v2")
                .select("title, content")
                .eq("id", chunk_id)
                .limit(1)
                .execute()
            )
            ch_rows = ch_res.data or []
            chunk = ch_rows[0] if ch_rows else {}
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading article (%s/%s): %s", slug, article_slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المادة",
        )

    article_no = int(art.get("article_no") or 0)

    # OPT-IN publish gate (the new default = ZERO published). A مادة has a public
    # page ONLY when an operator has published it — ``scripts/publish_articles.py``
    # sets a slug on its sidecar row (content_type='article',
    # content_id='{regulation_id}#{article_no}'). No sidecar row, or one whose slug
    # is NULL (unpublished), means the page does not exist → 404. This is a
    # PRESENCE check distinct from ``resolve_gate`` below, which still layers
    # open/gated truncation on top of a PUBLISHED مادة exactly as before.
    pub_meta = get_item_meta(supabase, "article", f"{regulation_id}#{article_no}")
    if not pub_meta or not pub_meta.get("slug"):
        return None

    is_fallback_body = art.get("extraction_status") != "extracted"

    gate = resolve_gate(
        supabase,
        "article",
        f"{regulation_id}#{article_no}",
        parent_regulation_id=str(regulation_id),
    )

    body = art.get("article_text")
    if is_fallback_body or not body:
        # extraction failed → render the whole owning chunk as the body.
        body = chunk.get("content") or ""
        is_fallback_body = True
    else:
        # Extracted single-مادة text — strip the duplicate heading line + footnote
        # noise for DISPLAY, BEFORE truncation so the free gate budget isn't spent
        # on noise. (Chunk fallbacks above are multi-article — left untouched.)
        body = _clean_article_display_text(body)

    cut = truncate_for_gate(body, gate, free_chars=ARTICLE_FREE_CHARS)

    # شرح teaser (gate #3): the FULL sharh_md is NEVER in this anon payload — only a
    # ~170-char whitespace-cut preview + placeholder-line count when a row is cached.
    sharh = _sharh_teaser(supabase, str(regulation_id), article_no)

    return {
        "slug": art.get("slug") or article_slug,
        "article_no": article_no,
        "article_label": art.get("article_label") or f"المادة {article_no}",
        "regulation": {
            "slug": slug,
            "title": (reg.get("clean_title") or reg.get("title") or "").strip(),
            "status": map_reg_status(reg.get("status_class")),
        },
        "gate": gate,
        "is_fallback_body": is_fallback_body,
        "context_title": chunk.get("title"),
        "text": cut["visible_text"],
        "is_truncated": cut["is_truncated"],
        "hidden_placeholder_lines": cut["hidden_placeholder_lines"],
        # Additive: {has_sharh, teaser, hidden_placeholder_lines}. Full شرح is a
        # gated account feature (served by get_full_article), never here.
        "sharh": sharh,
        "prev": _article_nav(supabase, regulation_id, article_no, after=False),
        "next": _article_nav(supabase, regulation_id, article_no, after=True),
    }


# --- articles sitemap feed -------------------------------------------------


def sitemap_article_urls(
    supabase: SupabaseClient,
    base_url: str,
    page: int = 1,
    page_size: int = SITEMAP_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int]:
    """Sitemap feed for the ``articles`` section — ONLY PUBLISHED مواد.

    مادة pages are OPT-IN, so the feed is driven by the SIDECAR, not the full
    ``seo_articles`` index: one URL per ``seo_item_meta`` article row that has a
    slug (``content_type='article'``, ``slug NOT NULL``) whose PARENT regulation is
    ALSO published (its own sidecar row carries a slug). The article's
    ``content_id`` is ``'{regulation_id}#{article_no}'``; the ``regulation_id``
    prefix is joined to the regulation slug map so the nested path resolves:
    ``loc = {base}/regulations/{reg slug}/{article slug}`` (both percent-encoded),
    ``lastmod = seo_item_meta.updated_at``. Rows whose regulation is not published
    are skipped (unservable URL). Both the article slug and the reg slug must be
    present, so the feed is EMPTY until the operator publishes مواد (correct — the
    new default is zero published). Same ``(urls, total_pages)`` contract as
    ``sitemap_blog_urls``. Read-only.
    """
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or SITEMAP_PAGE_SIZE))
    offset = (page - 1) * page_size

    try:
        result = (
            supabase.table("seo_item_meta")
            .select("content_id, slug, updated_at", count="exact")
            .eq("content_type", "article")
            .not_.is_("slug", "null")
            .order("updated_at", desc=True)
            .order("content_id", desc=False)
            .range(offset, offset + page_size - 1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error building articles sitemap feed: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب خريطة الموقع",
        )

    total = int(result.count or 0)
    total_pages = max(1, math.ceil(total / page_size)) if total else 1

    rows = result.data or []
    reg_ids = [
        (r.get("content_id") or "").rsplit("#", 1)[0]
        for r in rows
        if r.get("content_id") and "#" in (r.get("content_id") or "")
    ]
    reg_slugs = _slug_map(supabase, "regulation", reg_ids)

    urls: list[dict[str, Any]] = []
    for row in rows:
        cid = row.get("content_id") or ""
        art_slug = row.get("slug")
        if not art_slug or "#" not in cid:
            continue
        reg_id = cid.rsplit("#", 1)[0]
        reg_slug = reg_slugs.get(str(reg_id))
        if not reg_slug:
            continue  # parent regulation not published → URL unservable, skip.
        loc = _join(
            base_url,
            f"/regulations/{urllib.parse.quote(reg_slug, safe='')}"
            f"/{urllib.parse.quote(art_slug, safe='')}",
        )
        urls.append({"loc": loc, "lastmod": row.get("updated_at")})

    return urls, total_pages


# ==========================================================================
# PHASE 5 — /circulars
#
# One page per تعميم. Role = mesh glue between regs and judgments. The
# ``circulars`` table is a public BASE table (not a v2 view) but is STILL only
# read here — all SEO state (slug, gate override) lives in the ``seo_item_meta``
# sidecar, never on the corpus row. Gating is deliberately light: metadata +
# summary snippet are always free and the body is gated ONLY when it is long —
# ``effective_circular_gate`` downgrades a short (<=800-char) gated تعميم to
# 'open' so a 4-line circular never renders ~90% placeholder bars.
#
# LIVE FINDINGS (2026-07-22, 1,843 rows):
#   - ``source`` is a PROVENANCE LABEL, not a URL — the only values in the corpus
#     are ``'entity'`` (pulled from the issuing entity's own site) and
#     ``'scraped'``. 0 rows are URL-shaped. So ``official_sources`` is populated
#     ONLY if a future row ever stores an http(s) URL; today it is always empty
#     and the label is surfaced verbatim in ``source_label`` for the frontend to
#     display or hide as it sees fit (it is NOT injected into the metadata card).
#   - ``entity_id`` (FK → entities) is non-null on every row; the issuing
#     authority's Arabic name is ``entities.entity_name``.
#   - ``title`` and ``circ_ref`` are non-null and ``circ_ref`` is unique.
#   - content length: min 31 / median ~1,205 / max ~168k; 26% are <=800 chars
#     (render fully open), 74% are gated.
# ==========================================================================

# A UUID that cannot match any real row — used as the ``in_`` sentinel when an
# entity-name filter resolves to zero entities, so the typed ``entity_id`` column
# stays a valid uuid comparison (an empty ``in_`` list / non-uuid sentinel would
# error on the uuid column) and the hub correctly returns no items.
_NO_MATCH_UUID = "00000000-0000-0000-0000-000000000000"


def _normalize_circular_source(source: Any) -> tuple[Optional[str], list[dict[str, str]]]:
    """Normalize ``circulars.source`` into ``(source_label, official_sources)``.

    In the current corpus ``source`` is a provenance LABEL (``'entity'`` /
    ``'scraped'``), never a URL — so this returns ``(label, [])``. Should a row
    ever store an http(s) URL, it is instead surfaced as a single official-source
    link ``[{"title": "المصدر الرسمي", "href": url}]`` (mirroring the regulations
    doc page) and ``source_label`` is ``None``. A blank source → ``(None, [])``.
    """
    s = (source or "").strip() if isinstance(source, str) else ""
    if not s:
        return None, []
    if s.startswith("http://") or s.startswith("https://"):
        return None, [{"title": "المصدر الرسمي", "href": s}]
    return s, []


def _resolve_entity_ids(
    supabase: SupabaseClient, entity: Optional[str]
) -> Optional[list[str]]:
    """Resolve the hub ``entity`` filter to a list of ``entities.id`` values.

    The circulars corpus has no denormalized entity name, so a name filter is
    resolved via a second query: ``entity`` is matched ``ilike`` against
    ``entities.entity_name`` and the matching ``id``s are returned for an
    ``entity_id IN (...)`` filter on ``circulars``. A bare UUID is treated as a
    direct ``entity_id`` (no lookup). Returns ``None`` when no filter was
    requested (blank), or ``[]`` when a name filter matched no entity (→ the hub
    returns no items). Fail-soft: a lookup error degrades to ``[]``.
    """
    entity = (entity or "").strip()
    if not entity:
        return None
    if _is_uuid(entity):
        return [entity]
    try:
        res = (
            supabase.table("entities")
            .select("id")
            .ilike("entity_name", f"%{entity}%")
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("entity name filter lookup failed (%r): %s", entity, e)
        return []
    return [r.get("id") for r in (res.data or []) if r.get("id")]


def _entity_name_map(
    supabase: SupabaseClient, entity_ids: list[Any]
) -> dict[str, str]:
    """Batch-resolve ``{entity_id: entity_name}`` for one circulars hub page.

    One ``IN`` lookup over the distinct ``entity_id``s on the page. Fail-soft: an
    ``entities`` blip yields ``{}`` (cards render without the issuing-authority
    badge) rather than 500ing the hub. Read-only.
    """
    ids = list(dict.fromkeys(str(e) for e in entity_ids if e))
    if not ids:
        return {}
    try:
        res = (
            supabase.table("entities")
            .select("id, entity_name")
            .in_("id", ids)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("entity name map lookup failed: %s", e)
        return {}
    out: dict[str, str] = {}
    for r in res.data or []:
        eid = r.get("id")
        name = r.get("entity_name")
        if eid and name:
            out[str(eid)] = name
    return out


def _apply_circular_filters(
    qb,
    entity_ids: Optional[list[str]],
    q: Optional[str],
    sector: Optional[str] = None,
):
    """Apply the circulars hub filters to a query builder (chainable).

    ``entity_ids`` (already resolved by ``_resolve_entity_ids``): ``None`` = no
    entity filter; a list = ``entity_id IN (...)`` — an EMPTY list means the name
    filter matched nothing, so a non-matching sentinel UUID is used to force zero
    rows on the typed ``entity_id`` column. ``sector`` = array-contains on
    ``sectors`` (GIN-indexed; 100% populated — 1,843 of 1,843 rows, verified
    2026-08-01). Blank filters are no-ops.

    ⚠ ``q`` is accepted and IGNORED — it was an ``ilike`` on ``title`` until
    Wave B moved the text match to ``bm25_search()`` (see ``_apply_reg_filters``).

    ``sector`` is the LAST of the four wings to get this filter
    (``library_sectors.md`` §7.1): without it the التعاميم tab on a sector page
    cannot scope, despite the column being fully populated.
    """
    sector = (sector or "").strip()
    if entity_ids is not None:
        qb = qb.in_("entity_id", entity_ids if entity_ids else [_NO_MATCH_UUID])
    if sector:
        qb = qb.contains("sectors", [sector])
    return qb


def _circular_search_rows(
    supabase,
    entity_ids: Optional[list[str]],
    q: str,
    sector: Optional[str],
    select_cols: str,
) -> tuple[list[dict[str, Any]], bool]:
    """``(rows, truncated)`` for a ``q`` request (shared by lister + counter)."""
    return _bm25_hub_rows(
        supabase,
        corpus="circular",
        table="circulars",
        select_cols=select_cols,
        q=q,
        apply_filters=lambda qb: _apply_circular_filters(qb, entity_ids, None, sector),
    )


def _circular_count(
    supabase,
    entity_ids: Optional[list[str]],
    q: Optional[str],
    sector: Optional[str] = None,
) -> int:
    if q:
        # SEARCH MODE — count the BM25 match set, not the corpus.
        return len(_circular_search_rows(supabase, entity_ids, q, sector, "id")[0])
    qb = supabase.table("circulars").select("id", count="exact")
    qb = _apply_circular_filters(qb, entity_ids, None, sector)
    return int((qb.limit(1).execute().count) or 0)


# Column set the /circulars hub reads (shared by the legacy + sample paths).
_CIRCULAR_HUB_SELECT = "id, circ_ref, title, content, source, entity_id"


def _circular_hub_sort_key(r: dict[str, Any]) -> tuple[str, str]:
    """Python ordering for a sample-mode /circulars page: ``title`` then ``id`` —
    the same contract the legacy DB ``.order(title).order(id)`` expresses."""
    return ((r.get("title") or ""), str(r.get("id") or ""))


def _circular_hub_item(
    row: dict[str, Any], slug: str, entity_name: Optional[str]
) -> dict[str, Any]:
    """One /circulars card from a ``_CIRCULAR_HUB_SELECT`` row.

    See the block comment above ``_reg_hub_item`` — one definition, two readers.
    ``slug`` and ``entity_name`` are PASSED IN: neither rides on ``circulars``
    (the slug lives in the sidecar, the name in ``entities``), and both readers
    resolve them in ONE batched lookup per page rather than per row.

    ``body_snippet`` is the first ~160 chars of the ALWAYS-FREE ``content`` —
    the same never-gated lead the hub prints. A card never carries gated bytes.
    """
    source_label, _ = _normalize_circular_source(row.get("source"))
    content = row.get("content") or ""
    return {
        "slug": slug,
        "title": (row.get("title") or "").strip(),
        "entity_name": entity_name,
        "source_label": source_label,
        "body_snippet": _text_snippet(content, 160),
        "body_length": len(content),
    }


def circulars_hub_total_pages(
    supabase: SupabaseClient,
    entity: Optional[str] = None,
    q: Optional[str] = None,
    sector: Optional[str] = None,
) -> int:
    """Total hub pages for the filtered circulars set (for the anon-cap body).

    In SAMPLE MODE (``_published_ids`` → list) this counts only the filtered
    PUBLISHED circulars — an EXACT page count. In full-corpus steady state
    (``_published_ids`` → None) it counts filtered CORPUS rows (9/page); every
    circular is slugged then (``build_seo_slugs``), so counting all vs. slugged
    rows is identical.

    ``sector`` is appended LAST in the signature on purpose: the route passes the
    hub filters positionally into ``_wall_total_pages`` and an insertion in the
    middle would silently re-bind ``q``.
    """
    try:
        entity_ids = _resolve_entity_ids(supabase, entity)
        pub_ids = None if q else _published_ids(supabase, "circular")
        if pub_ids is not None:
            rows = _fetch_corpus_by_ids(
                supabase,
                "circulars",
                "id",
                pub_ids,
                lambda qb: _apply_circular_filters(qb, entity_ids, None, sector),
            )
            total = len(rows)
        else:
            # With ``q`` this counts the BM25 match set; without it, the corpus.
            total = _circular_count(supabase, entity_ids, q, sector)
    except LunaHTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Error counting circulars hub: %s", e)
        raise _hub_error()
    return max(1, math.ceil(total / HUB_PAGE_SIZE)) if total else 1


def list_circulars_hub(
    supabase: SupabaseClient,
    *,
    page: int = 1,
    entity: Optional[str] = None,
    q: Optional[str] = None,
    sector: Optional[str] = None,
) -> dict[str, Any]:
    """One page (9 items) of the /circulars hub.

    Ordering = ``title`` (ascending), a single partition (no in-force split like
    regs) → one DB ``range`` query + one batched sidecar slug lookup + one batched
    ``entities`` name lookup. Filters: ``entity`` (issuing-authority name, ilike
    via ``entities`` → ``entity_id IN``, or a bare UUID direct), ``sector``
    (array-contains on ``sectors`` — §7.1) and ``q`` (title
    ilike). Only slugged (published) rows are returned. Card item shape =
    ``{slug, title, entity_name, source_label, body_snippet, body_length}`` where
    ``body_snippet`` is the first ~160 chars of ``content`` (always-free summary,
    never gated body). Returns ``{"items": [...], "page": page,
    "total_pages": N}``; the anon depth-cap is enforced by the route.

    SAMPLE MODE (stage-1 rollout): when ``_published_ids`` returns a list, the
    page is cut from the PUBLISHED set — all matching published rows are fetched
    by id, sorted in Python by ``title`` (then id), and the 9-item window is
    sliced — so a page never comes back empty. In steady state (``_published_ids``
    → None) the legacy single-``range`` DB query below runs unchanged.

    SEARCH MODE (``q`` present, therefore an authenticated caller — D9): ids come
    from ``bm25_search()`` in score order; title ordering does not apply to a
    search result list.
    """
    page = max(1, int(page or 1))
    ps = HUB_PAGE_SIZE
    offset = (page - 1) * ps

    rows: list[dict[str, Any]]
    truncated = False
    entity_ids = _resolve_entity_ids(supabase, entity)
    pub_ids = None if q else _published_ids(supabase, "circular")

    if q:
        # SEARCH MODE — relevance order (see the ``_bm25_hub_rows`` block comment).
        all_rows, truncated = _circular_search_rows(
            supabase, entity_ids, q, sector, _CIRCULAR_HUB_SELECT
        )
        total = len(all_rows)
        rows = all_rows[offset : offset + ps]
    elif pub_ids is not None:
        # SAMPLE MODE — paginate the published set in Python (set is
        # <= SAMPLE_MODE_MAX_IDS; 100 published today).
        all_rows = _fetch_corpus_by_ids(
            supabase,
            "circulars",
            _CIRCULAR_HUB_SELECT,
            pub_ids,
            lambda qb: _apply_circular_filters(qb, entity_ids, None, sector),
        )
        all_rows.sort(key=_circular_hub_sort_key)
        total = len(all_rows)
        rows = all_rows[offset : offset + ps]
    else:
        # LEGACY (full-corpus steady state) — unchanged single-range query.
        try:
            total = _circular_count(supabase, entity_ids, None, sector)
            qb = supabase.table("circulars").select(_CIRCULAR_HUB_SELECT)
            qb = _apply_circular_filters(qb, entity_ids, None, sector)
            res = qb.order("title").order("id").range(offset, offset + ps - 1).execute()
            rows = res.data or []
        except Exception as e:  # noqa: BLE001
            logger.exception("Error listing circulars hub: %s", e)
            raise _hub_error()

    total_pages = max(1, math.ceil(total / ps)) if total else 1

    slugs = _slug_map(supabase, "circular", [r.get("id") for r in rows])
    names = _entity_name_map(supabase, [r.get("entity_id") for r in rows])

    items: list[dict[str, Any]] = []
    for r in rows:
        slug = slugs.get(str(r.get("id")))
        if not slug:
            continue
        items.append(
            _circular_hub_item(r, slug, names.get(str(r.get("entity_id"))))
        )

    return _hub_result(
        items, page, total_pages, q=q, total=total, truncated=truncated
    )


def get_circular_doc(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """Full /circulars/{slug} payload, or ``None`` when the slug is unknown
    (the route turns ``None`` into a 404 «التعميم غير موجود»).

    Resolves ``slug → content_id`` via the sidecar, loads the circular row + its
    issuing-authority name (``entities.entity_name``), resolves the gate ONCE, and
    applies the short-circular downgrade: ``gate_effective =
    effective_circular_gate(resolve_gate('circular', id), len(content))`` — a
    ``<=800``-char body renders fully open. The body is truncated by
    ``truncate_for_gate(content, gate_effective,
    free_chars=GATE_FREE_CHARS_DEFAULT)`` server-side (gated bytes never leave the
    server). ``metadata`` = الجهة المصدرة (entity name) + المرجع (circ_ref);
    ``source`` is normalized (label → ``source_label``; URL → ``official_sources``
    — always the label branch in the current corpus). Read-only, no counters.

    ``related_next`` is «اقرأ تاليًا» — <= 7 published تعاميم, same-type only
    (D2), ungated (the cards carry the always-free lead, never a gated byte).
    NO ``cited_regulations`` key on this wing (D14): تعاميم carry no citation
    data anywhere in the corpus.
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    try:
        meta = (
            supabase.table("seo_item_meta")
            .select("content_id")
            .eq("content_type", "circular")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        meta_rows = meta.data or []
        if not meta_rows:
            return None
        content_id = meta_rows[0].get("content_id")
        if not content_id:
            return None

        circ_res = (
            supabase.table("circulars")
            .select("id, circ_ref, title, content, source, entity_id")
            .eq("id", content_id)
            .limit(1)
            .execute()
        )
        circ_rows = circ_res.data or []
        if not circ_rows:
            return None
        circ = circ_rows[0]

        entity_name: Optional[str] = None
        entity_id = circ.get("entity_id")
        if entity_id:
            ent_res = (
                supabase.table("entities")
                .select("entity_name")
                .eq("id", entity_id)
                .limit(1)
                .execute()
            )
            ent_rows = ent_res.data or []
            if ent_rows:
                entity_name = ent_rows[0].get("entity_name")
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading circular doc (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب التعميم",
        )

    content = circ.get("content") or ""
    body_length = len(content)

    gate = resolve_gate(supabase, "circular", str(content_id))
    gate_effective = effective_circular_gate(gate, body_length)
    cut = truncate_for_gate(content, gate_effective, free_chars=GATE_FREE_CHARS_DEFAULT)

    source_label, official_sources = _normalize_circular_source(circ.get("source"))
    # Withheld for GATED تعاميم only — see get_regulation_doc. A short تعميم whose
    # effective gate is 'open' renders whole here, so it keeps its source link and
    # never reveals. A no-op in the current corpus (``circulars.source`` is a
    # provenance label, never a URL) but wired so the rule holds if that changes.
    if gate_effective != "open":
        official_sources = []

    metadata: list[dict[str, str]] = []
    if entity_name:
        metadata.append({"label": "الجهة المصدرة", "value": entity_name})
    if circ.get("circ_ref"):
        metadata.append({"label": "المرجع", "value": str(circ["circ_ref"])})

    # «اقرأ تاليًا» — تعاميم only (D2), and no «الأنظمة المذكورة» on this wing
    # either (D14): a تعميم has no citation data anywhere in the corpus. Runs
    # bonus-only (entity + sectors) until the topic-BM25 base axis of Wave E, so
    # expect a thin strip here or none at all. Ungated and fail-soft.
    related_next = get_related_next(supabase, "circular", str(content_id))

    return {
        "slug": slug,
        "title": (circ.get("title") or "").strip(),
        "entity_name": entity_name,
        "source_label": source_label,
        "official_sources": official_sources,
        "metadata": metadata,
        "gate_effective": gate_effective,
        "text": cut["visible_text"],
        "is_truncated": cut["is_truncated"],
        "hidden_placeholder_lines": cut["hidden_placeholder_lines"],
        "body_length": body_length,
        # «اقرأ تاليًا» — <= 7 published تعاميم. Never gated bytes: a card is a
        # title plus the always-free 160-char lead the hub already shows anon.
        "related_next": related_next,
    }


# ==========================================================================
# PHASE 3 — /forms (نماذج wing)
#
# Unlike every other wing (which reads pipeline-owned corpus VIEWS/tables), the
# forms wing serves ORIGINAL content authored INTO the ``public.forms`` BASE
# table (migration 098): scripts/draft_forms.py AI-drafts a row, a human legal
# reviewer approves it, and only THEN does it publish. Two things follow:
#
#   1. LIABILITY HARD GATE (plan § Cross-cutting risks — lawyer review). A form
#      may be served publicly ONLY when ``review_status='approved' AND
#      is_published=true``. EVERY public read here filters on BOTH flags; a draft
#      (what draft_forms.py writes) is invisible to anon callers, the sitemap, and
#      the writer handoff. There is no code path that serves an unapproved form.
#
#   2. Layered gating (plan § Forms). ``use_case_md`` (متى تستخدمه) + ``intro_md``
#      (شرح) are FREE — the SEO ranking food. ``body_md`` (the template body) is
#      GATED through the SAME resolve_gate / truncate_for_gate path as every other
#      content type (content_type='form'; seed default 'gated' in migration 095) —
#      the full body NEVER reaches an anon client; only a short preview does.
#
# Forms slugs live ON the forms table (``forms.slug`` UNIQUE), NOT in the
# seo_item_meta sidecar — so the hub/detail/sitemap here read the forms table
# directly (resolve_gate still keys off content_type='form' + the form id; a
# sidecar gate_override could still be set per-form if ever needed). Read-only,
# no counters — same anon + hour-cache discipline as the other wings.
# ==========================================================================

# Free preview budget for a gated form BODY before truncation. A form body is
# the scarce, signup-carrot artefact, so the free window is deliberately tight
# (tighter than the section default) — enough to prove the template's shape,
# not enough to use it without an account.
FORM_BODY_FREE_CHARS = 300

# The recognised form categories (الفئة) — draft_forms.py emits one of these and
# the hub filters on them. Kept as a tuple for the API layer to expose/validate.
FORM_CATEGORIES = ("عمل", "تقاضي", "تجاري", "عام")


def _form_legal_basis(value: Any) -> list[dict[str, str]]:
    """Normalize ``forms.legal_basis`` (jsonb) into ``[{"label": ...}, ...]``.

    draft_forms.py writes labels-only citations (e.g.
    ``{"label": "المادة 74 من نظام العمل"}``); this tolerates a bare string entry
    or a richer dict (falling back to ``article_no``) so a hand-edited row never
    500s the page. Non-string / empty labels are dropped. Pure, never raises.
    """
    out: list[dict[str, str]] = []
    if not isinstance(value, list):
        return out
    for item in value:
        label: Optional[str] = None
        if isinstance(item, dict):
            raw = item.get("label") or item.get("article_no")
            if raw is not None:
                label = str(raw).strip()
        elif isinstance(item, str):
            label = item.strip()
        if label:
            out.append({"label": label})
    return out


def _apply_form_filters(qb, category: Optional[str], q: Optional[str]):
    """Apply the forms hub filters to a query builder (chainable).

    ``category`` = exact match on ``category``; ``q`` = ilike on ``title_ar``.
    Blank filters are no-ops.
    """
    category = (category or "").strip()
    q = (q or "").strip()
    if category:
        qb = qb.eq("category", category)
    if q:
        qb = qb.ilike("title_ar", f"%{q}%")
    return qb


def forms_hub_total_pages(
    supabase: SupabaseClient,
    category: Optional[str] = None,
    q: Optional[str] = None,
) -> int:
    """Total hub pages for the filtered, PUBLISHED forms set (for the anon-cap
    body). Counts ONLY ``review_status='approved' AND is_published`` rows (9/page)
    — the same hard gate the hub list enforces (empty today; correct)."""
    try:
        qb = (
            supabase.table("forms")
            .select("id", count="exact")
            .eq("review_status", "approved")
            .eq("is_published", True)
        )
        qb = _apply_form_filters(qb, category, q)
        total = int((qb.limit(1).execute().count) or 0)
    except Exception as e:  # noqa: BLE001
        logger.exception("Error counting forms hub: %s", e)
        raise _hub_error()
    return max(1, math.ceil(total / HUB_PAGE_SIZE)) if total else 1


def list_forms_hub(
    supabase: SupabaseClient,
    *,
    page: int = 1,
    category: Optional[str] = None,
    q: Optional[str] = None,
) -> dict[str, Any]:
    """One page (9 items) of the /forms hub — PUBLISHED forms only.

    Hard gate: ``review_status='approved' AND is_published`` (draft rows are never
    listed). Ordering = ``created_at`` desc (matches the migration-098 partial
    publish index), then ``slug`` for stability — a single DB ``range`` query, no
    sidecar lookup (form slugs live on the forms row). Card item shape =
    ``{slug, title, category, use_case_snippet}`` where ``use_case_snippet`` is the
    first ~160 chars of the (always-free) ``use_case_md``. Returns
    ``{"items": [...], "page": page, "total_pages": N}``; the anon depth-cap is
    enforced by the route.
    """
    page = max(1, int(page or 1))
    ps = HUB_PAGE_SIZE
    offset = (page - 1) * ps

    try:
        qb = (
            supabase.table("forms")
            .select("slug, title_ar, category, use_case_md", count="exact")
            .eq("review_status", "approved")
            .eq("is_published", True)
        )
        qb = _apply_form_filters(qb, category, q)
        res = (
            qb.order("created_at", desc=True)
            .order("slug")
            .range(offset, offset + ps - 1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error listing forms hub: %s", e)
        raise _hub_error()

    total = int(res.count or 0)
    total_pages = max(1, math.ceil(total / ps)) if total else 1

    items: list[dict[str, Any]] = []
    for r in res.data or []:
        slug = r.get("slug")
        if not slug:
            continue
        items.append(
            {
                "slug": slug,
                "title": (r.get("title_ar") or "").strip(),
                "category": r.get("category"),
                "use_case_snippet": _text_snippet(r.get("use_case_md"), 160),
            }
        )

    return {"items": items, "page": page, "total_pages": total_pages}


def get_form_detail(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """Full /forms/{slug} payload, or ``None`` when the slug is unknown OR the
    form is not published (route → 404 «النموذج غير موجود»).

    HARD GATE: the SELECT filters ``review_status='approved' AND is_published`` —
    so a draft form (or an unpublished/unknown slug) is indistinguishable from a
    missing one to an anon client (both → 404), which is the liability requirement.

    Gating (plan § Forms): ``use_case_md`` + ``intro_md`` ship FREE; ``body_md`` is
    resolved through ``resolve_gate('form', <form id>)`` (seed default 'gated') and
    truncated by ``truncate_for_gate(..., free_chars=FORM_BODY_FREE_CHARS)`` — the
    FULL body_md is NEVER in this anon payload, only ``body_preview``
    (text/is_truncated/hidden_placeholder_lines). ``has_docx`` flags whether a
    gated download exists (the file itself is served via the PDF/download proxy,
    not here). Read-only, no counters.
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    try:
        res = (
            supabase.table("forms")
            .select(
                "id, slug, title_ar, category, use_case_md, intro_md, body_md, "
                "legal_basis, docx_path"
            )
            .eq("slug", slug)
            .eq("review_status", "approved")
            .eq("is_published", True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
        form = rows[0]
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading form detail (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب النموذج",
        )

    form_id = form.get("id")
    gate = resolve_gate(supabase, "form", str(form_id))
    cut = truncate_for_gate(
        form.get("body_md") or "", gate, free_chars=FORM_BODY_FREE_CHARS
    )

    docx_path = form.get("docx_path")
    has_docx = bool(str(docx_path).strip()) if docx_path is not None else False

    return {
        "slug": form.get("slug") or slug,
        "title": (form.get("title_ar") or "").strip(),
        "category": form.get("category"),
        "use_case_md": form.get("use_case_md"),
        "intro_md": form.get("intro_md"),
        "body_preview": {
            "text": cut["visible_text"],
            "is_truncated": cut["is_truncated"],
            "hidden_placeholder_lines": cut["hidden_placeholder_lines"],
        },
        "legal_basis": _form_legal_basis(form.get("legal_basis")),
        "has_docx": has_docx,
    }


def open_form_in_writer(
    supabase: SupabaseClient, auth_id: str, slug: str
) -> dict[str, Any]:
    """Copy a PUBLISHED form's ``{title, body_md}`` into the caller's قوالبي.

    The forms→writer handoff (plan § Forms → ``OpenInRayhanCta``, «افتح هذا
    النموذج في ريحان»). Reuses the existing per-user templates service
    (``templates_service.create_template`` → ``user_templates``, migration 055) —
    the same table قوالبي and the writer read — so the full template lands in the
    user's library ready to edit.

    HARD GATE mirrors the public page: the form must be
    ``review_status='approved' AND is_published`` or this raises 403 (Arabic) — a
    draft can never be handed off. An unknown slug raises 404 (Arabic). ``auth_id``
    is the Supabase auth id of the authenticated caller (the route supplies it);
    ``create_template`` resolves it to the internal ``user_id`` and scopes the
    insert. Returns ``{"template_id": ..., "title": ...}``.
    """
    slug = (slug or "").strip()
    if not slug:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="النموذج غير موجود",
        )

    try:
        res = (
            supabase.table("forms")
            .select("id, slug, title_ar, body_md, review_status, is_published")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading form for writer handoff (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء فتح النموذج",
        )

    if not rows:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="النموذج غير موجود",
        )

    form = rows[0]
    if not (form.get("review_status") == "approved" and form.get("is_published")):
        # Liability gate: an unapproved / unpublished form is never handed off.
        raise LunaHTTPException(
            status_code=403,
            code=ErrorCode.FORBIDDEN,
            detail="هذا النموذج غير متاح بعد",
        )

    # Reuse the قوالبي service (user_templates). Imported locally to avoid any
    # import-time coupling between the two service modules.
    from backend.app.services.templates_service import create_template

    title = (form.get("title_ar") or "").strip() or "نموذج"
    body_md = form.get("body_md") or ""
    row = create_template(supabase, auth_id, title=title, content_md=body_md)
    return {
        "template_id": row.get("template_id"),
        "title": row.get("title") or title,
    }


def sitemap_forms_urls(
    supabase: SupabaseClient,
    base_url: str,
    page: int = 1,
    page_size: int = SITEMAP_PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int]:
    """Sitemap feed for the ``forms`` section — PUBLISHED forms only.

    Reads the ``forms`` table directly (form slugs live on the row, not the
    sidecar), filtering ``review_status='approved' AND is_published`` so the feed
    never leaks a draft URL. ``loc = {base}/forms/{percent-encoded slug}``,
    ``lastmod = forms.updated_at``. Same ``(urls, total_pages)`` contract as
    ``sitemap_blog_urls``. Read-only, no side effects. Empty today (no approved
    forms) — correct.
    """
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or SITEMAP_PAGE_SIZE))
    offset = (page - 1) * page_size

    try:
        result = (
            supabase.table("forms")
            .select("slug, updated_at", count="exact")
            .eq("review_status", "approved")
            .eq("is_published", True)
            .not_.is_("slug", "null")
            .order("updated_at", desc=True)
            .order("slug", desc=False)
            .range(offset, offset + page_size - 1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error building forms sitemap feed: %s", e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب خريطة الموقع",
        )

    total = int(result.count or 0)
    total_pages = max(1, math.ceil(total / page_size)) if total else 1

    urls: list[dict[str, Any]] = []
    for row in result.data or []:
        slug = row.get("slug")
        if not slug:
            continue
        encoded = urllib.parse.quote(slug, safe="")
        urls.append(
            {
                "loc": _join(base_url, f"/forms/{encoded}"),
                "lastmod": row.get("updated_at"),
            }
        )
    return urls, total_pages


# ==========================================================================
# PHASE 5 — /judgments
#
# One page per حكم, read from ``public.cases`` (30,531 rows) — the JUDGMENTS
# corpus. NOTE the name trap: ``lawyer_cases`` is the PRIVATE per-user table for
# the app's case workspaces and is NEVER touched here; ``cases`` is the public
# MOJ judgment corpus. It is pipeline-owned (re-ingested), so — like every other
# wing — it is READ-ONLY here and all SEO state (slug, gate override) lives in
# the ``seo_item_meta`` sidecar keyed ``('judgment', cases.id::text)``.
#
# NO TITLE, NO SLUG COLUMN. Unlike every other wing, a judgment row has neither.
# Both are DERIVED by ``shared/seo/judgment_naming.py`` — the SINGLE source of
# truth shared with ``scripts/build_judgment_slugs.py`` (which froze the slugs
# into the sidecar). Never re-derive a title inline here: a read path that
# computed a different subject than the one the slug was cut from would serve a
# page whose H1 no longer matches its own URL.
#
# THE BODY IS THE RULING ITSELF. The page renders ``cases.content`` — the real
# judgment as issued — parsed into the document's own ``##`` sections by
# ``_parse_judgment_body``. ``short_summary`` stays as the always-free lead at the
# top of the page; it is a labelled ملخص above the text, never a substitute for
# it. The per-stage columns (facts / reasoning / ruling …) are pipeline-written
# SUMMARIES and are not published as the body.
#
# The free/gated split is positional, along a deliberate line:
#
#   FREE  → the opening section (plus the ``short_summary`` lead): parties,
#           الوقائع, المطالبات — the narrative setup. It carries the search terms,
#           tells a reader whether this ruling is about their problem, and is what
#           ranks.
#   GATED → everything after it: التسبيب and المنطوق — the reasoning and the
#           disposition, which is what a lawyer reads a judgment FOR and therefore
#           exactly what an account is worth signing up for.
#
# Gating mechanics are the same as every other wing and nothing special: the gate
# is resolved ONCE per doc via ``resolve_gate('judgment', id)`` and each GATED
# section is cut by ``truncate_for_gate`` server-side. Hidden bytes are DROPPED
# from the payload — there is no CSS/JS gate (module-header security invariant).
# FREE sections are never truncated regardless of the gate.
#
# LIVE FINDINGS (2026-07-25, 30,531 rows):
#   - ``content`` is present on 30,530 of 30,531 rows; ``short_summary`` on
#     29,567. date_gregorian 19,112 · legal_domains 20,671.
#   - ``court`` is non-null on every row; ``city`` 24,820; ``judgment_number``
#     15,510; ``appeal_result`` 4,965 — so the metadata card is built by omission.
#   - ``details_url`` (20,671 rows) is always an https MOJ link when present.
#   - ``court_level`` ∈ first_instance (23,932) / appeal (6,474) / supreme (125).
# ==========================================================================

# THE BODY IS ``cases.content`` — THE REAL RULING TEXT, NOT A SUMMARY.
#
# The eleven per-stage columns (facts / claims / reasoning / ruling / …) are
# PIPELINE-GENERATED SUMMARIES of the judgment, not the judgment. Rendering them
# as the page body published a paraphrase of a court ruling under that ruling's
# own URL — the same defect that had regulation pages rendering derived text
# before they were moved onto ``articles_v2``. ``content`` (``source.content_source
# = 'case_md'``) is the actual document as issued.
#
# ``short_summary`` REMAINS the always-free lead (``summary_md``) at the top of
# the page — a labelled ملخص above the real text, not a substitute for it.
#
# Shape of ``content`` across the published sample (verified live, and it is
# HETEROGENEOUS — do not assume a fixed template):
#   - 854 → 91,279 chars, mean ~11k (vs ~475 for a `reasoning` column).
#   - 38/100 open with a `# القضية رقم N` H1 + a `- **court**: …` front-matter
#     block that DUPLICATES the metadata card and official-source link; it is
#     stripped rather than rendered twice.
#   - 50/100 carry `## نص الحكم`; the rest use their own headings (`## نص القرار`,
#     `## تسبيب الحكم`, `## رأي اللجنة:`, `## حكم الاستئناف`, …) and some carry
#     none at all. Sections are therefore parsed from whatever `##` headings the
#     document actually has, never from a fixed list.
_JUDGMENT_FRONTMATTER_KEYS = (
    "court", "city", "date", "judgment_number", "case_number", "details_url",
    "case_ref", "court_level", "appeal_result",
)
_JUDGMENT_H1_RE = re.compile(r"^#\s+.*$")
_JUDGMENT_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_JUDGMENT_META_LINE_RE = re.compile(
    r"^\s*-\s*\*\*(" + "|".join(_JUDGMENT_FRONTMATTER_KEYS) + r")\*\*\s*:", re.I
)


def _strip_judgment_frontmatter(content: str) -> str:
    """Drop the machine-written header block from a judgment's ``content``.

    Removes the leading ``# القضية رقم N`` H1 and the ``- **court**: …`` metadata
    bullets, which the page already renders as the H1, the metadata card and the
    official-source link. Only lines at the TOP of the document are considered —
    an identical-looking bullet deeper in the ruling is real content and is kept.
    """
    lines = (content or "").splitlines()
    index = 0
    seen_meta = False
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if not seen_meta and _JUDGMENT_H1_RE.match(line):
            index += 1
            continue
        if _JUDGMENT_META_LINE_RE.match(line):
            seen_meta = True
            index += 1
            continue
        break
    return "\n".join(lines[index:]).strip()


def _parse_judgment_body(content: str) -> list[dict[str, str]]:
    """Split a judgment's ``content`` into ``[{id, title, text}]`` sections.

    Sections come from the document's OWN ``##`` headings, so a ruling that uses
    «نص القرار» or «رأي اللجنة:» keeps its own words rather than being forced into
    a template. Text appearing before the first heading becomes an untitled
    leading section, and a document with no headings at all yields exactly one
    untitled section — that is the common case and must stay renderable.

    ``id`` is positional (``s1``, ``s2``, …) rather than derived from the heading:
    ids are anchor targets AND the join key the authed full-content swap uses, so
    they must be stable, unique and URL-safe even when a document repeats a
    heading or titles a section with punctuation only.
    """
    body = _strip_judgment_frontmatter(content)
    if not body:
        return []

    sections: list[dict[str, str]] = []
    title = ""
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text or title:
            sections.append(
                {"id": f"s{len(sections) + 1}", "title": title, "text": text}
            )

    for line in body.splitlines():
        heading = _JUDGMENT_H2_RE.match(line.strip())
        if heading:
            flush()
            title = heading.group(1).strip().rstrip(":：").strip()
            buffer = []
            continue
        buffer.append(line)
    flush()

    return [s for s in sections if s["text"]]


def _rayhan_summary(row: dict[str, Any]) -> Optional[str]:
    """«ملخص ريحان» — ``cases.summary``, cleaned, or ``None`` when there is none.

    This is NOT ``short_summary`` (the ~250-char always-free lead the page already
    prints above the ruling). It is the pipeline's structured AI summary of the
    ruling — mean ~2.2k chars across ``## الملخص / الوقائع / المطالبات / تسبيب
    الحكم / منطوق الحكم`` — present on 30,513 of 30,531 rows (live 2026-08-11).

    ⚠ ALWAYS through ``strip_pipeline_sections``: 16.5k rows end in an internal
    «المراجع النظامية المحلولة» appendix (corpus ids, chunk ids, match scores) and
    252 carry a ``## classification_error`` Python traceback. Neither may reach a
    reader. Returns ``None`` — never ``""`` — when nothing survives the strip, so
    callers can treat "no summary" as one condition.
    """
    cleaned = strip_pipeline_sections((row.get("summary") or "").strip()).strip()
    return cleaned or None


# How many «الأنظمة المذكورة» cards a judgment page shows. NOT a gate — the list
# carries only the نظام's title and the snippet its hub card already shows anon
# (never a line of the regulation's content), and it IS the internal-linking mesh
# this wing exists to build. 7 is the STRIP SIZE (D7: 7 cards, 3 in view,
# horizontal RTL scroller), shared with «اقرأ تاليًا», and it never binds: 0 of
# 30,531 judgments cite more than 10 distinct أنظمة and the mean is 2.65 entries.
#
# ⚠ IT IS APPLIED AFTER RESOLUTION AND AFTER THE PUBLISH FILTER, not before.
# Capping the raw ref list first would let a judgment citing nine أنظمة of which
# four are published render three cards.
#
# ``None`` still means "no cap" for anyone who wants it back. Kept as a literal
# rather than an alias of ``RELATED_NEXT_LIMIT``: that constant is declared with
# the related-items reader far below this line, and a module-level alias would
# be a NameError at import.
JUDGMENT_CITED_FREE_LIMIT: Optional[int] = 7

# Exposure dial for a GATED judgment — a fraction of the RULING, not a budget
# per section. See `GateBudget` / `gate_decision` for the mechanism and
# `.claude/plans/gate_exposure_budget.md` for why it changed.
#
# ⚠ WHAT THIS REPLACED, so nobody reinstates it: `JUDGMENT_FREE_CHARS = 1200`
# granted 1,200 chars to EVERY section, and `JUDGMENT_FREE_LEADING_SECTIONS = 1`
# rendered section 1 WHOLE on top of that. Its comment claimed the rule withheld
# "roughly 85–90% of a typical judgment" and asserted that a single-section
# document was the common case. Measured on prod 2026-08-10, on 10,000 published
# أحكام: 40% (3,994) have ≥2 sections, mean exposure was 42.0% of the body — not
# 10–15% — and 846 rulings shipped ≥90% free. A five-section ruling collected
# 4 × 1,200 chars PLUS the entirety of its وقائع.
#
# 0.15 / 600 / 2000 brings that to a measured 16.8%. The floor keeps a rankable
# passage of the court's own words on the page (thin content ranks badly, and an
# over-tight gate costs the traffic this wing is published for); the ceiling
# stops a 30k-char ruling from leaking 4.5k.
JUDGMENT_BUDGET = GateBudget(ratio=0.15, floor=600, ceiling=2000)

# Column set the /judgments hub reads — from ``_JUDGMENT_HUB_TABLE`` (the ranked
# view), browse and search alike.
# It carries ALL FOUR title-source columns (short_summary → summary → facts →
# ruling) even though the card only prints a short_summary snippet: the card
# title and the doc-page H1 must be byte-identical, and both come from
# ``judgment_display_title`` walking that same chain. Selecting fewer columns
# would silently give the ~1k summary-less judgments a different title on the hub
# than on their own page.
#
# ``slug`` rides along from the sidecar half of the view, which is what retired
# the ``_slug_map`` round-trip this lister used to do per page.
_JUDGMENT_HUB_SELECT = (
    "id, case_ref, court, court_level, city, case_number, judgment_number, "
    "date_hijri, date_gregorian, legal_domains, short_summary, summary, "
    "facts, ruling, slug"
)

# Column set the /judgments/{slug} doc page reads: metadata + the title chain +
# the mesh source + ``content`` (the real ruling text that becomes the body).
# `facts`/`reasoning`/`ruling`… are deliberately NOT selected: they are summaries
# of the document, and the document itself is what this page publishes.
#
# ``source`` + the embedded ``entities(entity_name)`` feed
# ``shared.library.case_sources.judgment_provenance``: two thirds of the corpus is وزارة
# العدل and says where it came from via ``details_url``, but the other 9,860 rulings were
# parsed out of PDFs and carry that only in ``source``. The entity comes along as a
# PostgREST embed rather than a second read — it titles the source links, and this is
# already the one round-trip the page makes for the row.
_JUDGMENT_DOC_SELECT = (
    "id, case_ref, court, court_level, city, case_number, judgment_number, "
    "date_hijri, date_gregorian, appeal_result, legal_domains, short_summary, "
    "summary, details_url, referenced_regulations, content, source, "
    "entities(entity_name)"
)

# Leading bullet / list noise on a summary line. ``short_summary`` is stored as a
# markdown bullet list («- نزاع حول…\n- المحكمة قضت…»), which reads as broken
# punctuation once collapsed into a one-line card snippet.
_JUDGMENT_BULLET_RE = re.compile(r"^[ \t]*[-*•·—–]+[ \t]*", re.MULTILINE)

# ⚠ ``_JUDGMENT_ARABIC_INDIC`` / ``_judgment_article_int`` USED TO LIVE HERE and
# are deliberately gone. They normalized a cited «الرقم» («16/1», Arabic-Indic
# digits) into the article integer that keys a مادة page's sidecar row, because
# the cited-regulations mesh used to emit one card PER مادة. It now emits ONE
# CARD PER نظام (D8 — the section is «الأنظمة المذكورة», not المواد المذكورة), so
# nothing resolves an article number any more. The measured cost of dropping the
# مادة links is 5 URLs: only 5 مواد are slugged corpus-wide.


def _strip_bullets(text: Optional[str]) -> str:
    """Drop leading list markers from each line so a summary reads as prose.

    Applied ONLY to hub-card snippet input. Newlines survive (only the marker and
    its padding are removed) and ``_text_snippet`` collapses them afterwards, so
    «- أ.\\n- ب.» becomes «أ. ب.» rather than «- أ. - ب.».
    """
    if not text:
        return ""
    return _JUDGMENT_BULLET_RE.sub("", str(text))


def _iso_date(value: Any) -> Optional[str]:
    """Normalize a ``date``/ISO-string/None column to an ISO string or ``None``.

    PostgREST hands dates back as ``'YYYY-MM-DD'`` strings, but a direct psycopg
    read (and every test fixture that uses ``datetime.date``) hands back a date
    object — both must serialize identically in the payload.
    """
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    s = str(value).strip()
    return s or None


def _apply_judgment_filters(
    qb,
    court_level: Optional[str],
    domain: Optional[str],
    q: Optional[str] = None,
    court_variants: Optional[Sequence[str]] = None,
):
    """Apply the judgments hub FACET filters to a query builder (chainable).

    ``court_level`` = exact match ('first_instance' | 'appeal' | 'supreme');
    ``domain`` = array-contains on ``legal_domains``. Blank filters are no-ops.

    ``court_variants`` is the COURT SECTION (plan §2.3): the raw ``cases.court``
    strings one bucket claims, straight from ``shared.library.courts``. Three
    things about it are deliberate:

      * it is ``in.()``, i.e. EXACT matching against a closed vocabulary — never
        a LIKE and never a regex. ``cases.court`` is free text (30 distinct
        values, the same body spelled with and without a city), so a pattern
        match would silently redraw the bucket every time the pipeline ingests a
        new spelling, and the section's counts would move with it. Exact matching
        is what keeps a court a SECTION rather than a filter — see the
        enumeration-oracle comment in ``public_library.py``.
      * the caller passes VARIANTS, not a slug. The slug→variants resolution is
        in-memory and happens at the route boundary, before any DB work, so an
        unknown court costs a dict lookup rather than a query.
      * an EMPTY tuple is a no-op, not "match nothing". Only ``None``-vs-value
        distinguishes "no court asked for"; a bucket with zero variants cannot
        exist (``shared/library/courts.py`` builds them from the map).

    ⚠ ``q`` is accepted and IGNORED — it was an ``ilike`` on ``short_summary``
    until Wave B moved the text match to ``bm25_search()``. The property that
    made the old filter safe is preserved and strengthened: ``search_index``
    holds the SAME always-free lead (``short_summary``) and nothing else, so a
    query can still never be used as an oracle for a gated section.
    """
    court_level = (court_level or "").strip()
    domain = (domain or "").strip()
    if court_level:
        qb = qb.eq("court_level", court_level)
    if domain:
        qb = qb.contains("legal_domains", [domain])
    if court_variants:
        qb = qb.in_("court", list(court_variants))
    return qb


def _judgment_search_rows(
    supabase,
    court_level: Optional[str],
    domain: Optional[str],
    q: str,
    select_cols: str,
    court_variants: Optional[Sequence[str]] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """``(rows, truncated)`` for a ``q`` request (shared by lister + counter).

    Reads ``_JUDGMENT_HUB_TABLE``, not ``cases``: the ranked view is where the
    ``slug`` lives, and the card cannot be built without one. BM25 already
    returns published-scoped ids, so the view removes no result it would have
    found — it just spares the lister a sidecar round-trip per page.
    """
    return _bm25_hub_rows(
        supabase,
        corpus="judgment",
        table=_JUDGMENT_HUB_TABLE,
        select_cols=select_cols,
        q=q,
        apply_filters=lambda qb: _apply_judgment_filters(
            qb, court_level, domain, court_variants=court_variants
        ),
    )


def _judgment_count(
    supabase,
    court_level: Optional[str],
    domain: Optional[str],
    q: Optional[str],
    court_variants: Optional[Sequence[str]] = None,
) -> int:
    """Filtered PUBLISHED judgment count.

    Counts ``_JUDGMENT_HUB_TABLE`` — the published relation the lister pages —
    so the page count and the pages actually served cannot disagree at any
    corpus or publish size. Counting ``cases`` here (as this did until migration
    123) reported 30,531 for a wing that serves ~10,000. With ``q`` it counts the
    BM25 match set instead, for the same reason.
    """
    if q:
        # SEARCH MODE — count the BM25 match set, not the whole wing.
        return len(
            _judgment_search_rows(
                supabase, court_level, domain, q, "id", court_variants
            )[0]
        )
    qb = supabase.table(_JUDGMENT_HUB_TABLE).select("id", count="exact")
    qb = _apply_judgment_filters(qb, court_level, domain, court_variants=court_variants)
    return int((qb.limit(1).execute().count) or 0)


def judgments_hub_total_pages(
    supabase: SupabaseClient,
    *,
    court_level: Optional[str] = None,
    domain: Optional[str] = None,
    q: Optional[str] = None,
    court_variants: Optional[Sequence[str]] = None,
) -> int:
    """Total hub pages for the filtered judgments set (for the anon-cap body).

    Counts the PUBLISHED rows — ``_judgment_count`` reads the same ranked view
    the lister paginates, at 9/page. There is no sample-mode branch and no
    corpus fallback any more: both existed to paper over the gap between "rows
    in ``cases``" and "rows this wing can serve", and migration 123 closed that
    gap by making published a property of the relation.
    """
    try:
        total = _judgment_count(supabase, court_level, domain, q, court_variants)
    except LunaHTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Error counting judgments hub: %s", e)
        raise _hub_error()
    return max(1, math.ceil(total / HUB_PAGE_SIZE)) if total else 1


def _judgment_hub_item(row: dict[str, Any]) -> dict[str, Any]:
    """One /judgments card from a ``_JUDGMENT_HUB_SELECT`` row.

    See the block comment above ``_reg_hub_item`` — one definition, two readers.
    ``slug`` is read off the row (``library_judgments_ranked`` is published-only
    and carries it). ``title`` is DERIVED — ``cases`` has no title column — and
    it must be the same string the doc page's H1 prints, which is why the select
    list carries the whole ``short_summary → summary → facts → ruling`` chain
    ``judgment_display_title`` walks. ``snippet`` is the bullet-stripped
    always-free lead, NEVER a gated section.
    """
    court = (row.get("court") or "").strip()
    return {
        "slug": row.get("slug"),
        "title": judgment_display_title(row),
        "court": court,
        "court_slug": slug_for_court(court),
        "court_level": row.get("court_level"),
        "court_level_label": court_level_label(row.get("court_level")),
        "city": row.get("city"),
        "date_hijri": row.get("date_hijri"),
        "date_gregorian": _iso_date(row.get("date_gregorian")),
        "domains": [d for d in (row.get("legal_domains") or []) if d],
        "snippet": _text_snippet(_strip_bullets(row.get("short_summary")), 160),
    }


def list_judgments_hub(
    supabase: SupabaseClient,
    *,
    page: int = 1,
    court_level: Optional[str] = None,
    domain: Optional[str] = None,
    q: Optional[str] = None,
    court_variants: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """One page (9 items) of the /judgments hub.

    Ordering contract = ``date_gregorian`` DESC with dateless judgments LAST,
    then ``id`` — newest first, because recency is what a reader scanning a
    judgments directory is actually after. One ``.order().range()`` over
    ``_JUDGMENT_HUB_TABLE``; never fetches more than the 9 rows the page shows.

    ⚠ ``nullsfirst=False`` IS LOAD-BEARING. Postgres puts NULLs FIRST on a DESC
    order by default, which would open the hub with the ~11,400 dateless
    judgments (the ديوان المظالم / ZATCA / تأمين feeds carry no
    ``date_gregorian`` at all). This trap has bitten this codebase before.

    ⚠ THE FLIP SIDE OF THAT ORDERING, KNOWN AND ACCEPTED (plan §1.2): those same
    dateless feeds sort behind every dated MOJ row, so on the UNFILTERED hub they
    begin around page ~800. The COURT SECTIONS are the intended entry point for
    them — inside a section the set is homogeneous, so the ordering is fine.

    ⚠ THIS USED TO BE THREE CODE PATHS AND IS NOW ONE (migration 123). The
    sample-mode branch paginated a list of published ids in Python; the legacy
    branch paginated the CORPUS and dropped unslugged rows AFTER paging, which
    only worked while "every judgment is slugged" held. At ~10,000 of 30,531
    published it does not: that path would have rendered ~3 cards per 9-card page
    over ~3,393 pages. The view is the published set, so a page cannot come back
    short.

    Filters: ``court_level`` (exact), ``domain`` (an element of
    ``legal_domains``), ``court_variants`` (the court SECTION — see
    ``_apply_judgment_filters``), ``q`` (BM25).

    Card item shape = ``{slug, title, court, court_slug, court_level,
    court_level_label, city, date_hijri, date_gregorian, domains, snippet}``.
    ``title`` is ``judgment_display_title`` (the derived subject + court + Hijri
    year); ``court_slug`` is the bucket the raw ``court`` string belongs to, or
    ``None`` for an unclaimed value — it is what lets the card's court pill be a
    link to the section instead of dead text; ``snippet`` is the first ~160 chars
    of the bullet-stripped ``short_summary`` — the always-free lead, NEVER a
    gated section. Returns ``{"items": [...], "page": page, "total_pages": N}``;
    the anon depth-cap is enforced by the route.

    SEARCH MODE (``q`` present, therefore an authenticated caller — D9): ids come
    from ``bm25_search()`` in score order. Recency does not order a search result
    list — the reader asked for a subject, not for "what happened last".
    """
    page = max(1, int(page or 1))
    ps = HUB_PAGE_SIZE
    offset = (page - 1) * ps

    rows: list[dict[str, Any]]
    truncated = False

    if q:
        # SEARCH MODE — relevance order (see the ``_bm25_hub_rows`` block comment).
        all_rows, truncated = _judgment_search_rows(
            supabase, court_level, domain, q, _JUDGMENT_HUB_SELECT, court_variants
        )
        total = len(all_rows)
        rows = all_rows[offset : offset + ps]
    else:
        # BROWSE — one query over the published-only ranked view.
        try:
            total = _judgment_count(
                supabase, court_level, domain, None, court_variants
            )
            qb = supabase.table(_JUDGMENT_HUB_TABLE).select(_JUDGMENT_HUB_SELECT)
            qb = _apply_judgment_filters(
                qb, court_level, domain, court_variants=court_variants
            )
            res = (
                qb.order("date_gregorian", desc=True, nullsfirst=False)
                .order("id")
                .range(offset, offset + ps - 1)
                .execute()
            )
            rows = res.data or []
        except Exception as e:  # noqa: BLE001
            logger.exception("Error listing judgments hub: %s", e)
            raise _hub_error()

    total_pages = max(1, math.ceil(total / ps)) if total else 1

    items: list[dict[str, Any]] = []
    for r in rows:
        # The view is an INNER JOIN on a non-null slug, so this cannot be empty;
        # the guard stays as a cheap invariant check rather than a filter.
        if not r.get("slug"):
            continue
        items.append(_judgment_hub_item(r))

    return _hub_result(
        items, page, total_pages, q=q, total=total, truncated=truncated
    )


def court_counts(supabase: SupabaseClient) -> dict[str, int]:
    """PUBLISHED judgment count per court slug — all 12, in ``COURT_ORDER``.

    Feeds the court switcher and every court section's ``total_pages`` (plan
    §2.3.4). ONE ``count='exact'`` head query per bucket over
    ``_JUDGMENT_HUB_TABLE``, i.e. 12 index-only counts, behind the route's
    5-minute memo — so the browse grid costs ~12 cheap queries per five minutes,
    not one per page view. There is no grouped RPC for this the way there is for
    sectors (migration 109/124) because a 12-row answer over a closed vocabulary
    does not need one; if the vocabulary ever grows past a few dozen buckets,
    that is the point to add one.

    ⚠ THESE ARE COUNTS OF WHAT IS SERVABLE. They come from the same relation the
    lister pages, so «المحكمة التجارية 450» and the section's last page agree by
    construction. The corpus numbers in ``shared/library/courts.py``'s comments
    are documentation of the CORPUS and will be larger — do not reconcile them.

    Every slug is present, seeded to zero: a court with nothing published still
    renders (at zero) rather than vanishing from the switcher, which is the same
    contract ``sector_counts`` holds. Fail-soft per bucket — one failing count
    costs that number, not the page.
    """
    counts: dict[str, int] = {slug: 0 for slug in COURT_ORDER}
    for slug in COURT_ORDER:
        variants = COURT_VARIANTS.get(slug) or ()
        try:
            qb = supabase.table(_JUDGMENT_HUB_TABLE).select("id", count="exact")
            qb = _apply_judgment_filters(qb, None, None, court_variants=variants)
            counts[slug] = int((qb.limit(1).execute().count) or 0)
        except Exception as e:  # noqa: BLE001
            logger.warning("court count failed (%s): %s", slug, e)
    return counts


def _judgment_cited_regulations(
    supabase: SupabaseClient, row: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    """«الأنظمة المذكورة» on a judgment page — ``(reg hub cards, total)``.

    Resolves ``cases.referenced_regulations`` (jsonb; 8,411 slugged judgments
    carry entries, 22,278 entries, avg 2.65) into the internal-linking mesh into
    /regulations. ``total`` is how many PUBLISHED أنظمة this ruling cites, BEFORE
    ``JUDGMENT_CITED_FREE_LIMIT`` — so a page can say «و3 مراجع أخرى» if the cap
    ever binds (it does not today: 0 judgments cite more than 10 distinct أنظمة).

    ⚠ THE JOIN IS NON-OBVIOUS AND VERIFIED LIVE. A ref's ``regulation_id`` is the
    corpus's ``reg_ref`` TEXT key («17642_reg_037»), **NOT a uuid** — it joins to
    ``regulations_v2.reg_ref``, and THAT row's ``id::text`` is the
    ``seo_item_meta`` key carrying the regulation page's slug. Two lookups, in
    that order, and neither is skippable.

    THREE THINGS THIS USED TO DO AND NO LONGER DOES
    (`.claude/plans/read_next_related_items.md` §5.3):

      * it deduped by ``(regulation_id, article_no)`` and emitted one entry per
        cited مادة → it now dedupes by ``reg_ref`` ALONE: **one card per نظام,
        no مادة cards** (D8). The heading is الأنظمة المذكورة.
      * it kept UNRESOLVED refs (~30% — لائحة / قرار وزاري / فقه citations the
        pipeline could not match) as text with ``reg_slug=None`` → they are now
        **dropped entirely** (D9). A card in a scroller is an affordance; one
        that does not navigate is a broken link with extra steps.
      * it returned ``{title, article_no, reg_slug, article_slug}`` → it now
        returns **``RegHubItem``-shaped dicts**, the same cards the /regulations
        hub renders, built by the same ``_reg_hub_item``.

    Publication is decided by the hydration step, which reads the published-only
    ranked view — so an unslugged نظام drops out exactly like an unresolved ref.
    First-seen citation order is preserved.

    EVERY lookup is batched, and chunked at ``_ID_IN_CHUNK`` (150): a long
    PostgREST ``in.()`` blows the URL length into a 400, which has bitten the hub
    listers before. Fail-soft throughout — a lookup error costs the strip, never
    the page.
    """
    refs = row.get("referenced_regulations")
    if isinstance(refs, str):
        # Defensive: a raw psycopg/text read hands jsonb back as a string.
        try:
            refs = json.loads(refs)
        except (ValueError, TypeError):
            refs = None
    if not isinstance(refs, list):
        return [], 0

    # Dedupe by reg_ref alone, first-seen order. Refs with no ``regulation_id``
    # are unresolved and never reach a page — dropped here (D9).
    reg_refs: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        reg_ref = str(ref.get("regulation_id") or "").strip()
        if not reg_ref or reg_ref in seen:
            continue
        seen.add(reg_ref)
        reg_refs.append(reg_ref)
    if not reg_refs:
        return [], 0

    # 1. reg_ref → regulations_v2.id, batched + chunked.
    id_by_ref: dict[str, str] = {}
    for i in range(0, len(reg_refs), _ID_IN_CHUNK):
        chunk = reg_refs[i : i + _ID_IN_CHUNK]
        try:
            res = (
                supabase.table("regulations_v2")
                .select("id, reg_ref")
                .in_("reg_ref", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("cited-regulation lookup failed: %s", e)
            continue
        for r in res.data or []:
            rr = r.get("reg_ref")
            rid = r.get("id")
            if rr and rid:
                id_by_ref[str(rr)] = str(rid)

    ordered_ids = list(
        dict.fromkeys(id_by_ref[rr] for rr in reg_refs if rr in id_by_ref)
    )
    if not ordered_ids:
        return [], 0

    # 2. id → the PUBLISHED hub card (the publish filter, and the same bytes the
    #    /regulations grid renders).
    cards = _reg_hub_items_by_ids(supabase, ordered_ids)
    kept = [rid for rid in ordered_ids if rid in cards]

    total = len(kept)
    # The cap is applied LAST — after resolution and after the publish filter —
    # so a ruling citing nine أنظمة of which four are published shows four.
    if JUDGMENT_CITED_FREE_LIMIT is not None:
        kept = kept[: max(0, JUDGMENT_CITED_FREE_LIMIT)]
    return [cards[rid] for rid in kept], total


def _judgment_metadata(row: dict[str, Any]) -> list[dict[str, str]]:
    """The judgment metadata card — label/value pairs, EMPTY VALUES OMITTED.

    Only ``court`` and ``case_number`` are populated corpus-wide; ``city``,
    ``judgment_number``, ``date_hijri``, ``date_gregorian`` and ``appeal_result``
    are each missing on a large slice of the corpus, so the card is built by
    omission rather than rendering «غير متوفر» rows.

    The trailing «المجلد» / «الصفحات» rows come from ``judgment_provenance`` and locate a
    ruling inside the bound مجلد it was parsed out of — 5,538 rulings that previously named
    no source at all. They are the CITATION half only: the matching PDF and collection URLs
    are the crosswalk and belong to the metered reveal (D-CROSSWALK), which is why this
    function reads ``.citation`` and never ``.official_sources``. A وزارة العدل ruling or a
    standalone قرار PDF adds nothing here — its source is a link, and links are gated.
    """
    pairs = (
        ("المحكمة", (row.get("court") or "").strip()),
        ("الدرجة", court_level_label(row.get("court_level")) or ""),
        ("المدينة", (row.get("city") or "").strip()),
        ("رقم القضية", (row.get("case_number") or "").strip()),
        ("رقم الحكم", (row.get("judgment_number") or "").strip()),
        ("التاريخ الهجري", (row.get("date_hijri") or "").strip()),
        ("التاريخ الميلادي", _iso_date(row.get("date_gregorian")) or ""),
        ("نتيجة الاستئناف", (row.get("appeal_result") or "").strip()),
    )
    items = [{"label": label, "value": value} for label, value in pairs if value]
    items.extend(judgment_provenance(row, _judgment_entity_name(row)).citation)
    return items


def _judgment_row_for_slug(
    supabase: SupabaseClient, slug: str, select_cols: str
) -> Optional[dict[str, Any]]:
    """Resolve a judgment ``slug`` → its ``cases`` row via the sidecar, or ``None``.

    Two reads (sidecar → corpus) shared by the anon doc reader and the authed
    full-content reader, so the slug contract lives in exactly one place. Raises
    the Arabic 500 on a DB failure; returns ``None`` for an unknown slug or a
    sidecar row that points at a vanished case (route → 404).
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    try:
        meta = (
            supabase.table("seo_item_meta")
            .select("content_id, indexable")
            .eq("content_type", "judgment")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        meta_rows = meta.data or []
        if not meta_rows:
            return None
        content_id = meta_rows[0].get("content_id")
        if not content_id:
            return None

        case_res = (
            supabase.table("cases")
            .select(select_cols)
            .eq("id", content_id)
            .limit(1)
            .execute()
        )
        case_rows = case_res.data or []
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading judgment (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب الحكم",
        )

    if not case_rows:
        return None

    # Carry the sidecar's `indexable` (migration 130) onto the corpus row under a
    # private key. The alternative — a second `get_item_meta` round-trip inside
    # `get_judgment_doc` — would ask the same table the same question twice on
    # every ISR render of every judgment page. `cases` has no such column, so the
    # underscore prefix cannot collide with a corpus field.
    row = dict(case_rows[0])
    row["_indexable"] = bool(meta_rows[0].get("indexable"))
    return row


def get_judgment_doc(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """Full /judgments/{slug} payload, or ``None`` when the slug is unknown
    (the route turns ``None`` into a 404 «الحكم غير موجود»).

    Resolves ``slug → content_id`` via the sidecar, loads the case row, resolves
    the gate ONCE (``resolve_gate('judgment', id)``) and renders the ordered
    section model:

      * ``summary_md`` = ``short_summary`` — the ALWAYS-FREE lead at the top of
        the page, never gated. A labelled ملخص ABOVE the ruling, not a stand-in
        for it.
      * ``has_summary`` = does a «ملخص ريحان» (``cases.summary``) exist. A
        BOOLEAN, never the text: that summary is gated and is served only by
        ``get_full_judgment``. The page renders its reveal button on this flag, so
        the ~18 rulings with no summary offer no action rather than spending an
        unlock on nothing.
      * ``sections`` = the REAL ruling text (``content``) parsed by
        ``_parse_judgment_body`` into the document's own ``##`` sections, each
        ``{id, title, text, is_truncated, hidden_placeholder_lines, is_free}``.
        ONE document-wide budget — ``gate_decision(total, gate,
        JUDGMENT_BUDGET)`` — is spent across them in reading order by
        ``spend_budget_across_sections``; the hidden bytes are DROPPED here, not
        hidden client-side. ``id`` is positional (``s1``, ``s2``…) and matches the
        authed ``get_full_judgment`` payload, so the client-side enhancer can
        swap section-for-section.
      * ``cited_regulations`` = «الأنظمة المذكورة» — the internal-linking mesh
        into /regulations, as ``RegHubItem`` CARDS: one per cited نظام (D8),
        unresolved and unpublished citations dropped (D9/D5), <= 7. See
        ``_judgment_cited_regulations``; ``cited_total`` is its pre-cap size.
      * ``related_next`` = «اقرأ تاليًا» — same-type (أحكام) neighbours from the
        precomputed graph, <= 7, published only. USUALLY EMPTY and that is the
        design (§3.6): three quarters of the slugged corpus sits in المحكمة
        التجارية, where nothing clears the floor.
        Neither strip is gated — both carry the same bytes anon and paid, which
        is forced anyway by this page being ISR-baked.
      * ``hidden_section_count`` counts the sections ACTUALLY truncated — it
        sizes the placeholder bars and the CTA. It is NOT the exposure measure:
        ``withheld_chars`` / ``withheld_pct`` are, and they are what the §5 audit
        and ``test_gated_judgment_withholds_the_majority_of_the_ruling`` read.
      * ``gate_effective`` may be ``'open'`` on a ``'gated'`` ruling too short to
        gate honestly (``gate_decision`` step 3) — the same downgrade
        ``effective_circular_gate`` has always applied to short تعاميم. Such a
        page ships whole, drops its CTA, and publishes ``official_sources``.

    ``title`` / ``subject`` / ``court_level_label`` / ``hijri_year`` are all
    DERIVED via ``shared/seo/judgment_naming`` — the same module the frozen slug
    was cut from. Read-only, no counters.
    """
    row = _judgment_row_for_slug(supabase, slug, _JUDGMENT_DOC_SELECT)
    if row is None:
        return None

    slug = (slug or "").strip()
    content_id = row.get("id")
    gate = resolve_gate(supabase, "judgment", str(content_id))

    parsed_sections = _parse_judgment_body(row.get("content") or "")

    # ONE budget for the whole ruling, measured against the ruling's OWN length
    # (the parsed body — frontmatter is already stripped, so raw `content` would
    # overstate the document and buy the reader free chars for metadata bullets).
    total_chars = sum(len(p["text"]) for p in parsed_sections)
    gate_effective, free_chars = gate_decision(total_chars, gate, JUDGMENT_BUDGET)
    cuts = spend_budget_across_sections(
        [p["text"] for p in parsed_sections], gate_effective, free_chars
    )

    sections: list[dict[str, Any]] = []
    hidden_section_count = 0
    withheld_chars = 0
    for parsed, cut in zip(parsed_sections, cuts):
        if cut["is_truncated"]:
            hidden_section_count += 1
        withheld_chars += len(parsed["text"]) - len(cut["visible_text"])
        sections.append(
            {
                "id": parsed["id"],
                "title": parsed["title"],
                "text": cut["visible_text"],
                "is_truncated": cut["is_truncated"],
                "hidden_placeholder_lines": cut["hidden_placeholder_lines"],
                # `is_free` = this section reached the reader whole. It used to
                # mean "sits in the free LAYER" under the leading-sections rule;
                # with one shared budget there are no layers, only what the
                # allowance reached. `is_truncated` still drives the render.
                "is_free": not cut["is_truncated"],
            }
        )

    # THE TWO STRIPS, in the §5.4 order: «الأنظمة المذكورة» first, «اقرأ تاليًا»
    # after it. The exclusion D13 asks for is a NO-OP on this page and is
    # deliberately not wired: the cited list holds أنظمة while «اقرأ تاليًا» is
    # same-type (D2), so it holds أحكام — the two can never collide, and
    # `get_related_next` already filters `target_type = 'judgment'` in the query.
    #
    # ⚠ EXPECT NO STRIP ON MOST JUDGMENT PAGES, and that is the intended outcome
    # (§3.6). 7,483 of the 10,000 slugged أحكام sit in المحكمة التجارية, where the
    # court scarcity weight is 0.0002 and entity is worth ~0, so nothing clears
    # the floor. Better a missing strip than six arbitrary commercial rulings.
    cited, cited_total = _judgment_cited_regulations(supabase, row)
    related_next = get_related_next(supabase, "judgment", str(content_id))

    # Withheld for GATED أحكام only — see the note in get_regulation_doc. Keyed
    # on ``gate_effective``, matching what `get_circular_doc` has always done: a
    # ruling `gate_decision` downgraded to open has no crosswalk left to protect,
    # because this same payload already ships its entire text. Every judgment
    # starts 'gated' (``seo_gate_defaults``), so the downgrade is the only way
    # this branch fires today.
    official_sources: list[dict[str, str]] = []
    if gate_effective == "open":
        official_sources = judgment_provenance(
            row, _judgment_entity_name(row)
        ).official_sources

    return {
        "slug": slug,
        "title": judgment_display_title(row),
        "subject": judgment_subject(row),
        "court": (row.get("court") or "").strip(),
        "court_level": row.get("court_level"),
        "court_level_label": court_level_label(row.get("court_level")),
        "city": row.get("city"),
        "case_number": row.get("case_number"),
        "judgment_number": row.get("judgment_number"),
        "date_hijri": row.get("date_hijri"),
        "date_gregorian": _iso_date(row.get("date_gregorian")),
        "hijri_year": hijri_year(row.get("date_hijri")),
        "appeal_result": row.get("appeal_result"),
        "domains": [d for d in (row.get("legal_domains") or []) if d],
        "metadata": _judgment_metadata(row),
        "summary_md": row.get("short_summary"),
        # Does a «ملخص ريحان» EXIST for this ruling — never the summary itself.
        # It is gated content, served only by ``get_full_judgment``; this boolean
        # is what lets the page decide whether to render the reveal button at all,
        # so an unlock is never spendable on a ruling that has no summary. It
        # leaks nothing: 99.9% of rulings answer true.
        "has_summary": _rayhan_summary(row) is not None,
        "sections": sections,
        # «الأنظمة المذكورة» — one card per cited نظام, published only, <= 7.
        # ``cited_total`` is how many published أنظمة this ruling cites, before
        # the cap (which never binds on the current corpus).
        "cited_regulations": cited,
        "cited_total": cited_total,
        # «اقرأ تاليًا» — same-type (أحكام) neighbours, <= 7, published only.
        "related_next": related_next,
        "official_sources": official_sources,
        "gate_effective": gate_effective,
        # May a crawler have this ruling — `seo_item_meta.indexable` (migration
        # 130), NOT a gate decision. The page turns this into its `robots` meta,
        # and the SAME flag decides whether the sitemap lists the URL. Publishing
        # it here rather than re-deriving it in the frontend is what keeps those
        # two answers from drifting into "Submitted URL marked noindex".
        #
        # ⚠ ORTHOGONAL TO `gate_effective`. An indexable ruling is still gated:
        # Googlebot gets exactly the truncated body an anonymous human gets,
        # which is the whole point of the paywall JSON-LD. Do not collapse these
        # two fields into one — "crawlable" and "free" are different questions.
        "indexable": bool(row.get("_indexable")),
        "hidden_section_count": hidden_section_count,
        # The honest exposure numbers. `hidden_section_count` counts sections and
        # goes to 0 on a short document that is giving everything away — which is
        # exactly how the old regulation gate hid its own leak. These count BYTES.
        "withheld_chars": withheld_chars,
        "withheld_pct": (
            round(100.0 * withheld_chars / total_chars, 1) if total_chars else 0.0
        ),
    }


# ==========================================================================
# «اقرأ تاليًا» + «الأنظمة المذكورة» — THE RELATED-ITEM STRIPS
# (.claude/plans/read_next_related_items.md §5)
#
# Two card strips at the bottom of every public library object page. Both are
# UNGATED and carry no per-user bytes — not a preference, a constraint: all four
# detail pages are ISR-baked and serve ONE html artifact to anon, free and paid
# alike, so a per-tier strip is not expressible. What they publish is titles,
# links and the snippet the hub cards already show anonymously (D1). The gate,
# the character budgets and the enumeration meter are untouched by any of this.
#
#   «الأنظمة المذكورة»  — a factual citation list, resolved LIVE from the corpus.
#                         حكم→نظام (`cases.referenced_regulations`) and
#                         نظام→نظام (`cross_references_v2`). ABSENT on تعاميم and
#                         خدمات: neither corpus has citation data at all (D14),
#                         and the field is omitted from those response models
#                         rather than shipped empty.
#   «اقرأ تاليًا»       — SAME-TYPE ONLY (D2), read from the precomputed
#                         `public.related_items` edge store (migration 143).
#
# THREE PROPERTIES OF THE READ ARE DECISIONS, NOT DETAILS:
#
#   * THE PUBLISH FILTER LIVES HERE, NOT IN THE TABLE (D5). The graph is
#     computed over the FULL corpus and every edge above the floor is stored,
#     ranked, with no top-N per source (D6) — so publishing one نظام lights it up
#     in every neighbour's strip within the 24h ISR window, with NO recompute.
#     The cost is that the top rows of a source's edge list may all be
#     unpublished, which is why the read over-fetches (`_RELATED_SCAN_LIMIT`)
#     and only then cuts to 7.
#   * DEDUP ACROSS THE TWO STRIPS (D13). الأنظمة المذكورة renders first and wins;
#     its target ids are excluded from اقرأ تاليًا, which backfills. This only
#     ever bites on نظام pages — everywhere else the two strips hold disjoint
#     types — and it is the reason §5.4 fixes the CALL ORDER.
#   * FAIL SOFT, ALWAYS. A lookup error costs the strip, never the page; an
#     empty strip is `[]`, never an error. That also means this code does not
#     500 when migration 143 has not been applied yet — it logs and returns
#     nothing. Apply 143 to prod BEFORE pushing the backend all the same, or
#     every doc render writes a warning.
#
# ⚠ NO RELATION-TYPE CHIP AND NO RANK. `related_items.reason` is audit-only and
# is never selected here (D10 — 585 of 746 relation rows are single-method
# guesses, good enough to RANK on, not good enough to ASSERT). There is no stored
# `rank` column either: rank is meaningless before the publish filter runs, so
# the order is `score desc` at read time and nothing else.
# ==========================================================================

_RELATED_ITEMS_TABLE = "related_items"

# The four corpora the graph covers — `related_items.source_type` /
# `target_type`. Anything else asks for a strip that cannot exist.
_RELATED_CORPORA: frozenset[str] = frozenset(
    {"regulation", "compliance", "circular", "judgment"}
)

# Cards per strip (D7). Both strips share it: 3 in view, horizontal RTL
# side-scroll, so 7 is a little over two screens of scroller.
# The judgment wing's own copy of this number is ``JUDGMENT_CITED_FREE_LIMIT``,
# declared with that wing far above — it has to be a literal there because this
# line has not run yet at import time. Change both together.
RELATED_NEXT_LIMIT = 7
REGULATION_CITED_LIMIT = 7

# How deep to walk a source's edge list looking for PUBLISHED targets. The plan's
# §5.2 query does the publish join in SQL and takes 40; PostgREST cannot express
# that join (`related_items` has no FK to the sidecar), so the filter runs in
# Python over a wider window instead. Average degree is ~2 and the largest
# corpora are the least connected, so this almost never binds — but when a
# source's best neighbours are all unpublished, this is the number that decides
# whether the strip backfills or comes back empty.
_RELATED_SCAN_LIMIT = 200

# At most this many of the 7 may be BONUS-ONLY (`base = 0`) — a pair that shares
# only an entity or a sector, with no curated relation and no topic match behind
# it. Without the guard a source with one real neighbour renders it alongside six
# "same ministry" coincidences and the strip reads as noise.
_RELATED_BONUS_ONLY_MAX = 2

# …and the guard is OFF for أحكام, where `base = 0` is not a weak signal but the
# only possible value: judgments have no base axis at all (§3.3), court carries
# everything through the bonus. Applying the guard there would cap every judgment
# strip at 2 cards for no reason.
_RELATED_GUARD_EXEMPT: frozenset[str] = frozenset({"judgment"})


def _reg_hub_items_by_ids(
    supabase: SupabaseClient, reg_ids: Sequence[Any]
) -> dict[str, dict[str, Any]]:
    """``{regulation id: reg hub card}`` for the PUBLISHED subset of ``reg_ids``.

    Reads ``library_regulations_ranked`` — the same published-only view the
    /regulations hub pages — through ``_reg_hub_item``, so a strip card and a hub
    card are the same bytes. An unpublished نظام simply has no entry: that single
    drop is BOTH D5's publish filter and D9's "unresolved citations are skipped".

    Batched and chunked at ``_ID_IN_CHUNK`` (150) — a long PostgREST ``in.()``
    blows the URL length into a 400. Fail-soft: a failed chunk costs its cards.
    """
    ids = list(dict.fromkeys(str(i) for i in reg_ids if i))
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), _ID_IN_CHUNK):
        chunk = ids[i : i + _ID_IN_CHUNK]
        try:
            res = (
                supabase.table(_REG_HUB_TABLE)
                .select(_REG_HUB_SELECT)
                .in_("id", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("related regulation hydration failed: %s", e)
            continue
        for r in res.data or []:
            if r.get("slug"):
                out[str(r.get("id"))] = _reg_hub_item(r)
    return out


def _judgment_hub_items_by_ids(
    supabase: SupabaseClient, case_ids: Sequence[Any]
) -> dict[str, dict[str, Any]]:
    """``{case id: judgment hub card}`` for the PUBLISHED subset of ``case_ids``.

    ``library_judgments_ranked`` — published-only, carries the slug and the whole
    title chain ``judgment_display_title`` walks. Same chunking + fail-soft
    contract as ``_reg_hub_items_by_ids``.
    """
    ids = list(dict.fromkeys(str(i) for i in case_ids if i))
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), _ID_IN_CHUNK):
        chunk = ids[i : i + _ID_IN_CHUNK]
        try:
            res = (
                supabase.table(_JUDGMENT_HUB_TABLE)
                .select(_JUDGMENT_HUB_SELECT)
                .in_("id", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("related judgment hydration failed: %s", e)
            continue
        for r in res.data or []:
            if r.get("slug"):
                out[str(r.get("id"))] = _judgment_hub_item(r)
    return out


def _compliance_hub_items_by_ids(
    supabase: SupabaseClient,
    guide_ids: Sequence[Any],
    slugs: Optional[dict[str, str]] = None,
) -> dict[str, dict[str, Any]]:
    """``{guide id: compliance hub card}`` for the PUBLISHED subset of ``guide_ids``.

    ``library_compliance_v`` carries no slug column, so publication is decided by
    the sidecar. ``slugs`` lets a caller that already resolved the map (the
    related-items read does) hand it in rather than pay for it twice.
    """
    ids = list(dict.fromkeys(str(i) for i in guide_ids if i))
    out: dict[str, dict[str, Any]] = {}
    if not ids:
        return out
    slug_map = slugs if slugs is not None else _slug_map(supabase, "compliance", ids)
    for i in range(0, len(ids), _ID_IN_CHUNK):
        chunk = ids[i : i + _ID_IN_CHUNK]
        try:
            res = (
                supabase.table(_COMPLIANCE_HUB_TABLE)
                .select(_COMPLIANCE_HUB_SELECT)
                .in_("id", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("related compliance hydration failed: %s", e)
            continue
        for r in res.data or []:
            slug = slug_map.get(str(r.get("id")))
            if slug:
                out[str(r.get("id"))] = _compliance_hub_item(r, slug)
    return out


def _circular_hub_items_by_ids(
    supabase: SupabaseClient,
    circ_ids: Sequence[Any],
    slugs: Optional[dict[str, str]] = None,
) -> dict[str, dict[str, Any]]:
    """``{circular id: circular hub card}`` for the PUBLISHED subset of ``circ_ids``.

    Reads the ``circulars`` corpus (this wing has no ranked view), so the sidecar
    decides publication exactly as ``list_circulars_hub`` does, and the issuing
    authority's name comes from ONE batched ``entities`` lookup over the page.
    """
    ids = list(dict.fromkeys(str(i) for i in circ_ids if i))
    out: dict[str, dict[str, Any]] = {}
    if not ids:
        return out
    slug_map = slugs if slugs is not None else _slug_map(supabase, "circular", ids)
    rows: list[dict[str, Any]] = []
    for i in range(0, len(ids), _ID_IN_CHUNK):
        chunk = ids[i : i + _ID_IN_CHUNK]
        try:
            res = (
                supabase.table("circulars")
                .select(_CIRCULAR_HUB_SELECT)
                .in_("id", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("related circular hydration failed: %s", e)
            continue
        rows.extend(res.data or [])
    names = _entity_name_map(supabase, [r.get("entity_id") for r in rows])
    for r in rows:
        slug = slug_map.get(str(r.get("id")))
        if slug:
            out[str(r.get("id"))] = _circular_hub_item(
                r, slug, names.get(str(r.get("entity_id")))
            )
    return out


def _related_hub_items(
    supabase: SupabaseClient,
    content_type: str,
    ids: Sequence[Any],
    slugs: Optional[dict[str, str]] = None,
) -> dict[str, dict[str, Any]]:
    """Hydrate ``ids`` of one corpus into that corpus's hub cards.

    The dispatch that makes «اقرأ تاليًا» corpus-agnostic: the reader never knows
    which wing it is on, it just hands the ids back here. Unknown corpus → ``{}``
    (no strip), never an exception.
    """
    if content_type == "regulation":
        return _reg_hub_items_by_ids(supabase, ids)
    if content_type == "judgment":
        return _judgment_hub_items_by_ids(supabase, ids)
    if content_type == "compliance":
        return _compliance_hub_items_by_ids(supabase, ids, slugs)
    if content_type == "circular":
        return _circular_hub_items_by_ids(supabase, ids, slugs)
    return {}


def get_related_next(
    supabase: SupabaseClient,
    content_type: str,
    content_id: Any,
    exclude_ids: Optional[Sequence[Any]] = None,
) -> list[dict[str, Any]]:
    """The «اقرأ تاليًا» strip: <= 7 SAME-TYPE published neighbours, best first.

    ``content_id`` is the ``seo_item_meta.content_id`` (``uuid::text``) of the
    page being rendered — the same key ``related_items.source_id`` holds.
    ``exclude_ids`` are the ids «الأنظمة المذكورة» already rendered above this
    strip (D13); on a نظام page that dedup is load-bearing, elsewhere the two
    strips hold different corpora and it is a no-op.

    Returns a list of that corpus's HUB CARD dicts — ``RegHubItem`` /
    ``ComplianceHubItem`` / ``CircularHubItem`` / ``JudgmentHubItem`` shaped —
    so the frontend feeds them straight into the existing cards. **Empty list on
    anything at all: no edges, nothing published, an unknown corpus, a missing
    table, a DB error.** This function does not raise.

    THE READ, in four steps:

      1. ``related_items`` ordered by ``score desc``, capped at
         ``_RELATED_SCAN_LIMIT``. ``target_type = source_type`` is asserted in
         the query, not assumed — D2 makes the graph same-type by construction
         and a cross-type row would hydrate against the wrong corpus.
      2. THE PUBLISH FILTER (D5) — one batched sidecar lookup over the window,
         dropping every target with no ``slug``. It lives here and not in the
         table so that publishing an item lights it up everywhere with no
         recompute of the graph.
      3. THE BONUS-ONLY GUARD — at most ``_RELATED_BONUS_ONLY_MAX`` survivors may
         carry ``base = 0``; the rest are skipped and the strip backfills from
         further down the ranking. OFF for أحكام (see ``_RELATED_GUARD_EXEMPT``).
      4. Cut to 7 and hydrate through the hub-card builders in one batched
         lookup, preserving score order.
    """
    ct = (content_type or "").strip()
    cid = str(content_id or "").strip()
    if ct not in _RELATED_CORPORA or not cid:
        return []

    excluded = {str(x) for x in (exclude_ids or []) if x}
    excluded.add(cid)  # belt-and-braces against a self-edge in the store

    try:
        qb = (
            supabase.table(_RELATED_ITEMS_TABLE)
            # `reason` is audit-only and deliberately not read (D10).
            .select("target_id, score, base")
            .eq("source_type", ct)
            .eq("source_id", cid)
            .eq("target_type", ct)
            .order("score", desc=True)
            .limit(_RELATED_SCAN_LIMIT)
        )
        # Only push the exclusion down when it is small enough to be safe in a
        # query string — it is <= 7 ids in practice (the cited-أنظمة strip), and
        # step 3 below re-applies it in Python regardless.
        small = sorted(excluded - {cid})
        if small and len(small) <= _ID_IN_CHUNK:
            qb = qb.not_.in_("target_id", small)
        rows = qb.execute().data or []
    except Exception as e:  # noqa: BLE001
        # Includes "relation related_items does not exist" — migration 143 not
        # applied yet. A missing strip, never a missing page.
        logger.warning("related-items lookup failed (%s/%s): %s", ct, cid, e)
        return []

    ranked: list[tuple[str, float]] = []
    seen: set[str] = set()
    for r in rows:
        tid = str(r.get("target_id") or "").strip()
        if not tid or tid in excluded or tid in seen:
            continue
        seen.add(tid)
        try:
            base = float(r.get("base") or 0.0)
        except (TypeError, ValueError):
            base = 0.0
        ranked.append((tid, base))
    if not ranked:
        return []

    # THE PUBLISH FILTER (D5) — cheap: content_id + slug only, chunked.
    slugs = _slug_map(supabase, ct, [t for t, _ in ranked])

    guard = ct not in _RELATED_GUARD_EXEMPT
    picked: list[str] = []
    bonus_only = 0
    for tid, base in ranked:
        if not slugs.get(tid):
            continue
        if guard and base <= 0.0:
            if bonus_only >= _RELATED_BONUS_ONLY_MAX:
                continue
            bonus_only += 1
        picked.append(tid)
        if len(picked) >= RELATED_NEXT_LIMIT:
            break
    if not picked:
        return []

    try:
        cards = _related_hub_items(supabase, ct, picked, slugs)
    except Exception as e:  # noqa: BLE001
        logger.warning("related-items hydration failed (%s/%s): %s", ct, cid, e)
        return []
    return [cards[t] for t in picked if t in cards]


def _regulation_cited_regulations(
    supabase: SupabaseClient, reg_id: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    """«الأنظمة المذكورة» on a نظام page — ``(reg hub cards, their ids)``.

    Resolved LIVE from ``public.cross_references_v2`` (``source_type='reg_chunk'``)
    rather than precomputed: it is a factual citation list, not a similarity
    guess (D3), and it changes only when the corpus is re-ingested.

    The rows are at مادة granularity — one per «تنص المادة (12) من نظام…» inside a
    chunk — so they are collapsed to DISTINCT ``target_regulation_id``:
    ONE CARD PER نظام, NO مادة CARDS (D8). The section is الأنظمة المذكورة.

    Unpublished targets vanish in hydration (D9/D5), first-seen order is
    preserved, and the list is capped at ``REGULATION_CITED_LIMIT``. Expect 0–7:
    580 أنظمة have any outbound citation at all, avg 1.24 distinct targets, max 7.

    The ids ride back alongside the cards because «اقرأ تاليًا» on this same page
    is ALSO أنظمة and must not repeat them (D13, §5.4).
    """
    rid = str(reg_id or "").strip()
    if not rid:
        return [], []

    try:
        res = (
            supabase.table("cross_references_v2")
            .select("target_regulation_id")
            .eq("source_type", "reg_chunk")
            .eq("source_regulation_id", rid)
            .not_.is_("target_regulation_id", "null")
            .limit(1000)
            .execute()
        )
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        logger.warning("regulation cited-regulations lookup failed (%s): %s", rid, e)
        return [], []

    ordered: list[str] = []
    seen: set[str] = set()
    for r in rows:
        tid = str(r.get("target_regulation_id") or "").strip()
        # A نظام citing its own مواد is not a cross-reference to show.
        if not tid or tid == rid or tid in seen:
            continue
        seen.add(tid)
        ordered.append(tid)
    if not ordered:
        return [], []

    cards = _reg_hub_items_by_ids(supabase, ordered)
    kept = [t for t in ordered if t in cards][:REGULATION_CITED_LIMIT]
    return [cards[t] for t in kept], kept


# ==========================================================================
# AUTHED FULL-CONTENT (the signup promise)
#
# Public library pages are ISR-cached and shared across all viewers, so they can
# NEVER vary by auth — the anon endpoints above always ship the gate-truncated
# payload (to humans AND Googlebot alike). A client-side enhancer running in the
# authenticated browser calls THESE functions (via the metered reveal route) and
# swaps the truncated body for the full one on an explicit «اعرض النص كاملاً»
# action.
#
# These functions do the EXACT OPPOSITE of the gated readers: they apply NO
# truncation and NO resolve_gate — they return the complete bytes.
#
# ⚠ THEY ARE NOT THE AUTHORIZATION BOUNDARY. Entitlement is enforced by the
# route, which calls ``resolve_access()`` (Layer B) BEFORE reaching any function
# here and returns a 402 refusal instead of content when the caller may not
# unlock the item. Never call a ``get_full_*`` from a path that has not already
# resolved access — PART 9 trap 5.
#
# The ONE gate that still holds even for an entitled Max subscriber is the FORMS
# liability gate: get_full_form serves ONLY review_status='approved' AND
# is_published rows — an unapproved form is never handed out, at any tier.
#
# QUOTA: library reveals ARE metered as of the access-tiers work
# (.claude/plans/access_tiers_gating.md). Unlocks are permanent and idempotent,
# charged once per user per item against a per-period allowance; the ledger is
# ``library_unlocks``. The older note here claiming library reads are an unmetered
# free-account carrot was deleted — it is now the opposite of the policy.
# 'service' has no full-content function: it is policy-never-gated, so there was
# never anything to "unlock" and it is never charged. It has no public payload
# either — a service is a citation title plus the issuing entity's link.
# 'compliance' has no full-content function for the OPPOSITE reason: the service
# guide is served whole, to everyone, by the public wing. There is no withheld
# half for an authed reveal to hand over.
#
# ⚠ get_full_regulation returns {id, title, text} sections and NO ``sharh_md`` —
# شرح is reachable only one-مادة-at-a-time via get_full_article. This is a moat
# invariant, not an oversight: bundling شرح into the continuous regulation payload
# would collapse the AI layer's unlock count ~15× (plan §1.2). Pinned by
# test_full_regulation_never_includes_sharh.
#
# ⚠ AND get_full_article gates the شرح ON TOP of the route's entitlement check.
# It is the ONE reader here that is not purely "the route already decided": the
# نص and the شرح have DIFFERENT gates (§1.3 — the نص may be open-tier and free,
# the شرح never is), so one ``resolve_access`` verdict cannot answer for both.
# ``include_sharh`` carries the second answer, and it defaults to False.
#
# All functions are READ-ONLY (no counters — usage lives on ``library_items``,
# never here) and return ``None`` for an unknown key so the route maps it to a
# 404 with an Arabic message.
# ==========================================================================


def official_sources_for_item(
    supabase: SupabaseClient, content_type: str, content_id: str
) -> list[dict[str, str]]:
    """The «المصادر الرسمية» block for a GATED item. SYNC (run via ``run_db``).

    User decision 2026-07-28: official sources are part of what an unlock buys,
    reversing the plan's §1.2 "the official source URL is always shown, gated or
    not". The rationale for gating is that the block is not a generic link — it
    is a per-item deep link carrying the source system's own identifier (the BOE
    law UUID, the MoJ judgment id), so publishing it for the whole corpus hands
    out a ready-made slug → official-ID crosswalk.

    The anon/ISR doc payloads therefore emit ``official_sources: []`` whenever the
    item is gated (see ``get_regulation_doc`` / ``get_judgment_doc`` /
    ``get_circular_doc``), and the real block is served ONLY from the metered
    reveal, alongside the content the unlock paid for.

    An OPEN item is the other half of that rule (2026-08-01): it publishes its
    whole body AND its official sources in the anon payload, and — having nothing
    gated — never reaches a reveal. So the two renderers still cover DISJOINT
    sets and «المصادر الرسمية» cannot appear twice on one page.

    Returns ``[]`` for anything with no official source of its own:
      * ``article`` — a مادة page has never shown one; its parent نظام carries it.
      * ``form``    — ``FormDetail`` has no ``official_sources``.
      * ``service`` — never gated, so its sources are never withheld.
    """
    ct = (content_type or "").strip()
    cid = str(content_id or "").strip()
    if not cid or ct not in ("regulation", "judgment", "circular"):
        return []

    # No tier check here, by design: the ANON payload withholds official sources
    # for EVERY item of these three wings, open-tier included, so this endpoint
    # is the only renderer and the two sides cannot both fire. See the
    # `official_sources` note in ``get_regulation_doc``.
    out: list[dict[str, str]] = []
    try:
        if ct == "regulation":
            res = (
                supabase.table("regulations_v2")
                .select("landing_url")
                .eq("id", cid)
                .limit(1)
                .execute()
            )
            row = (res.data or [{}])[0]
            if row.get("landing_url"):
                out.append({"title": "الموقع الرسمي", "href": row["landing_url"]})

        elif ct == "judgment":
            # ``source`` is here for the 9,860 rulings that have NO ``details_url``:
            # 4,322 قرارات published as their own PDF and 5,538 lifted out of a bound
            # مجلد. Before this, every one of them revealed an empty block — the unlock
            # bought content but no way to check it against the publisher.
            #
            # The volume links are the crosswalk in its strongest form: one مجلد PDF is
            # ~50 full rulings, all of them gated. That is an argument for keeping them
            # HERE, behind the unlock, not for exempting them from D-CROSSWALK.
            res = (
                supabase.table("cases")
                .select("details_url, source, entities(entity_name)")
                .eq("id", cid)
                .limit(1)
                .execute()
            )
            row = (res.data or [{}])[0]
            out.extend(
                judgment_provenance(row, _judgment_entity_name(row)).official_sources
            )

        else:  # circular
            res = (
                supabase.table("circulars")
                .select("source")
                .eq("id", cid)
                .limit(1)
                .execute()
            )
            _, out = _normalize_circular_source((res.data or [{}])[0].get("source"))
    except Exception as e:  # noqa: BLE001
        # Never break a paid reveal over the attribution block — the content the
        # user just unlocked matters more than the link to it.
        logger.warning(
            "official_sources_for_item failed (%s/%s): %s", content_type, content_id, e
        )
        return []

    return out


def _regulation_id_for_slug(supabase: SupabaseClient, slug: str) -> Optional[str]:
    """Resolve a regulation ``slug`` → its ``content_id`` via the sidecar, or
    ``None``. Shared by the full-content regulation + article readers."""
    slug = (slug or "").strip()
    if not slug:
        return None
    meta = (
        supabase.table("seo_item_meta")
        .select("content_id")
        .eq("content_type", "regulation")
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    rows = meta.data or []
    if not rows:
        return None
    return rows[0].get("content_id") or None


def get_full_regulation(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """FULL continuous-document payload for /library/full/regulation/{slug} (AUTHED).

    ARTICLES-FIRST: when the regulation has ``seo_articles`` rows (now sourced from
    ``articles_v2``) AND that index passes ``use_article_surface`` — existence
    alone is NOT the condition — it returns EVERY مادة in article-number order,
    untruncated — ``{"sections": [{"id": 'art-{no}', "title": article_label,
    "text": article_text | owning-chunk content}, ...]}`` — except that a run of
    مواد sharing one multi-مادة fallback chunk collapses into a single section
    (see ``_article_sections``) instead of repeating that chunk once per مادة.

    CHUNK FALLBACK — a regulation with NO ``seo_articles`` rows (article-less /
    chunk-only) **or** one whose index has too many holes to be trusted — returns
    EVERY chunk in reading order (the legacy shape). The second case is
    ``17900_reg_128_p2`` (اللائحة التنفيذية لنظام العمل ج2): 68 rows for a
    232-مادة لائحة, i.e. a reveal that would have omitted 164 مواد in silence.

    ⚠ This decision MUST match ``get_regulation_doc``'s — one helper, both call
    sites, identical arguments. A reader who spent an unlock and lands on a
    structurally different document than the crawler saw is a broken purchase.

    No gating, no resolve_gate — the whole document (the account-only
    continuous-reading feature). ``None`` when the slug is unknown (route → 404
    «النظام غير موجود»). Read-only, no counters.
    """
    try:
        content_id = _regulation_id_for_slug(supabase, slug)
        if not content_id:
            return None

        articles = _seo_articles_for_regulation(supabase, str(content_id))
        # Not `if articles:` — same helper, same args as `get_regulation_doc`, so
        # the paid reveal and the public page can never pick different sources.
        if use_article_surface(supabase, str(content_id), articles):
            fallback_ids = [
                a.get("chunk_id")
                for a in articles
                if a.get("extraction_status") != "extracted" or not a.get("article_text")
            ]
            chunk_rows = _chunk_row_map(supabase, fallback_ids)
            # Same builder as the open anon render, so the reveal a reader paid for
            # and the public page agree section-for-section — including the merge
            # that stops a multi-مادة fallback chunk from repeating itself once per
            # مادة it covers. gate='open' → nothing is truncated here.
            sections = _article_sections(
                articles,
                chunk_rows,
                gate="open",
                free_chars=600,
                merge_chunk_runs=True,
            )
            return {"sections": sections}

        # CHUNK FALLBACK — every chunk in reading order (legacy continuous doc).
        res = _ordered_chunk_query(
            supabase, str(content_id), "id, title, position, content"
        ).execute()
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading full regulation (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب النظام",
        )

    sections = [
        {
            "id": str(r.get("id")),
            "title": r.get("title"),
            "text": r.get("content") or "",
        }
        for r in rows
    ]
    return {"sections": sections}


def get_full_article(
    supabase: SupabaseClient,
    reg_slug: str,
    article_slug: str,
    *,
    include_sharh: bool = False,
) -> Optional[dict[str, Any]]:
    """FULL مادة payload for /library/full/article (ENTITLED callers).

    Resolves ``reg_slug`` → regulation_id, then the ``seo_articles`` row by
    ``(regulation_id, slug=article_slug)``, and returns the COMPLETE, untruncated
    body plus — only when ``include_sharh`` — the FULL cached شرح::

        {"text": <full article/chunk body>, "sharh_md": <full شرح|null>}

    Body = the extracted ``article_text`` when ``extraction_status='extracted'``,
    else the whole owning chunk's ``content`` (extraction fallback). No
    gating/truncation on the نص: reaching this function at all means the route's
    ``resolve_access`` already said the body may ship. ``None`` when the
    regulation slug or the article slug is unknown (route → 404
    «المادة غير موجودة»). Read-only.

    ``include_sharh`` DEFAULTS TO FALSE on purpose. The شرح is §1.3 ALWAYS-GATED,
    so a caller that has not thought about entitlement must get the نص and
    nothing more. The one live caller passes ``AccessDecision.is_entitled`` — NOT
    ``may_unlock``: an ``'open'`` verdict grants the free public-domain نص and
    buys nothing, and letting the شرح ride on it gave the whole corpus away once
    already (H-5, 2026-08-07). When False the ``seo_sharh`` row is never even
    READ, so the gated bytes do not enter the process, let alone the payload.
    """
    reg_slug = (reg_slug or "").strip()
    article_slug = (article_slug or "").strip()
    if not reg_slug or not article_slug:
        return None

    try:
        regulation_id = _regulation_id_for_slug(supabase, reg_slug)
        if not regulation_id:
            return None

        art_res = (
            supabase.table("seo_articles")
            .select("article_no, chunk_id, article_text, extraction_status")
            .eq("regulation_id", str(regulation_id))
            .eq("slug", article_slug)
            .limit(1)
            .execute()
        )
        art_rows = art_res.data or []
        if not art_rows:
            return None
        art = art_rows[0]

        body = art.get("article_text")
        if art.get("extraction_status") != "extracted" or not body:
            body = ""
            chunk_id = art.get("chunk_id")
            if chunk_id:
                ch_res = (
                    supabase.table("chunks_v2")
                    .select("content")
                    .eq("id", chunk_id)
                    .limit(1)
                    .execute()
                )
                ch_rows = ch_res.data or []
                if ch_rows:
                    body = ch_rows[0].get("content") or ""
        else:
            # Extracted single-مادة text → strip its duplicate heading + footnote
            # noise for DISPLAY (chunk fallback above is multi-article — kept).
            body = _clean_article_display_text(body)

        # The شرح is fetched ONLY for an entitled caller — an unentitled one does
        # not get a truncated شرح, it gets no شرح query at all.
        sharh_md = None
        if include_sharh:
            article_no = int(art.get("article_no") or 0)
            sh_res = (
                supabase.table("seo_sharh")
                .select("sharh_md")
                .eq("regulation_id", str(regulation_id))
                .eq("article_no", article_no)
                .limit(1)
                .execute()
            )
            sh_rows = sh_res.data or []
            sharh_md = (sh_rows[0].get("sharh_md") if sh_rows else None) or None
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "Error loading full article (%s/%s): %s", reg_slug, article_slug, e
        )
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب المادة",
        )

    return {"text": body or "", "sharh_md": sharh_md}


def get_full_circular(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """FULL circular payload for /library/full/circular/{slug} (AUTHED).

    Resolves ``slug`` → content_id via the sidecar and returns the complete body:
    ``{"text": <full content>}``. No gating/truncation (the whole تعميم, unlocked).
    ``None`` when the slug is unknown (route → 404 «التعميم غير موجود»). Read-only.
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    try:
        meta = (
            supabase.table("seo_item_meta")
            .select("content_id")
            .eq("content_type", "circular")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        meta_rows = meta.data or []
        if not meta_rows:
            return None
        content_id = meta_rows[0].get("content_id")
        if not content_id:
            return None

        circ_res = (
            supabase.table("circulars")
            .select("content")
            .eq("id", content_id)
            .limit(1)
            .execute()
        )
        circ_rows = circ_res.data or []
        if not circ_rows:
            return None
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading full circular (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب التعميم",
        )

    return {"text": circ_rows[0].get("content") or ""}


def get_full_judgment(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """FULL judgment payload for /library/full/judgment/{slug} (AUTHED).

    Returns ``{"sections": [{"id", "title", "text"}, ...], "summary_md": str|None}``:

      * ``sections`` — the FULL ruling text (``content``) parsed by the SAME
        ``_parse_judgment_body`` the anon page uses, so ids (``s1``, ``s2``…),
        titles and order match position-for-position, with NO truncation. That id
        parity is the whole point: the client-side enhancer matches each returned
        section to the DOM node it is upgrading, so a gated teaser is replaced in
        place by the full text. Parsing (rather than returning the raw blob) is
        what keeps the two payloads in lockstep — deriving the anon sections one
        way and the authed reveal another would desynchronise them the first time
        a heading changed.
      * ``summary_md`` — «ملخص ريحان» (``cases.summary`` via ``_rayhan_summary``),
        the structured AI summary of the ruling. It appears in NO anon payload:
        ``get_judgment_doc`` publishes only the boolean ``has_summary``, so the
        one way to read it is this metered reveal. It rides the SAME unlock as the
        body by construction — one call, one charge, both surfaces — which is why
        the page's «ملخص ريحان» button and its «اعرض النص كاملاً» panel share one
        reveal instead of billing twice for one ruling.

    No gating, no ``resolve_gate``: auth is the boundary (the route's
    ``get_current_user``). ``None`` when the slug is unknown (route → 404 «الحكم
    غير موجود»). Read-only, no counters.
    """
    row = _judgment_row_for_slug(supabase, slug, "id, content, summary")
    if row is None:
        return None
    return {
        "sections": _parse_judgment_body(row.get("content") or ""),
        "summary_md": _rayhan_summary(row),
    }


def get_full_form(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """FULL form body for /library/full/form/{slug} (AUTHED).

    Returns ``{"body_md": <full template body>}`` — the gated template body now
    unlocked for the signed-in user. The LIABILITY HARD GATE still holds even
    authed: the SELECT filters ``review_status='approved' AND is_published``, so a
    draft / unpublished / unknown slug all return ``None`` (route → 404 «النموذج غير
    موجود»). No content gating/truncation on an approved form. Read-only.
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    try:
        res = (
            supabase.table("forms")
            .select("body_md")
            .eq("slug", slug)
            .eq("review_status", "approved")
            .eq("is_published", True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading full form (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب النموذج",
        )

    return {"body_md": rows[0].get("body_md") or ""}
