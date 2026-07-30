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
from typing import Any, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import LunaHTTPException, ErrorCode
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
# the full شرح is served only to authed callers via /library/full/article.
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
_CONTENT_TYPES = ("regulation", "article", "judgment", "circular", "service", "form")

# In-process TTL cache for seo_gate_defaults. The policy table has one row per
# content_type and changes rarely (operator edit), so a ~5-minute cache spares
# every gate resolution a round-trip. Module-level; safe under the sync
# service-function model (functions run in worker threads via run_db, and a torn
# read here at worst returns a slightly-stale-but-consistent dict).
_GATE_DEFAULTS_TTL_SECONDS = 300.0
_gate_defaults_cache: dict[str, Any] = {"value": None, "expires_at": 0.0}

# --- Stage-1 "sample mode" pagination (hub listers) -----------------------
# During the stage-1 rollout only a small SAMPLE of each wing is slugged
# (``seo_item_meta.slug NOT NULL``) — e.g. 100 of 3,373 regulations. The hubs
# paginate the FILTERED CORPUS and drop unslugged rows via ``_slug_map``, which
# is correct in steady state (every corpus row slugged) but during the sample
# returns EMPTY pages: the corpus's first pages hold none of the 100 published
# rows (caught 2026-07-23: /regulations?page=1 → 0 items, total_pages=375).
#
# ``_published_ids`` detects the sample: when a wing's published-id count is
# ``<= SAMPLE_MODE_MAX_IDS`` the hub listers paginate over the PUBLISHED ids
# (fetched from the sidecar) instead of the corpus, so every page is full and
# ``total_pages`` is EXACT. Above the ceiling the wing is in full-corpus steady
# state and the listers keep their legacy corpus-pagination path untouched.
SAMPLE_MODE_MAX_IDS = 300

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
    "hub_page_allowed",
    # Phase 2 — content endpoints (/regulations + /compliance)
    "HUB_PAGE_SIZE",
    "REG_STATUS_MAP",
    "map_reg_status",
    "DOC_TYPE_BUCKET_LABELS",
    "map_doc_type_bucket",
    "list_regulations_hub",
    "regulations_hub_total_pages",
    "get_regulation_doc",
    "list_compliance_hub",
    "compliance_hub_total_pages",
    "get_compliance_service",
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
    "JUDGMENT_FREE_LEADING_SECTIONS",
    "JUDGMENT_CITED_FREE_LIMIT",
    "JUDGMENT_FREE_CHARS",
    "list_judgments_hub",
    "judgments_hub_total_pages",
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

    Emits one URL per sidecar row of ``content_type`` that has a slug —
    i.e. exactly the pages the wing can actually serve. ``loc`` =
    ``{base}/{path_prefix}/{percent-encoded slug}`` (Arabic slugs are
    percent-encoded for maximally-compatible ``<loc>`` values), ``lastmod`` =
    the sidecar ``updated_at``. Same ``(urls, total_pages)`` contract as
    ``sitemap_blog_urls``. Read-only, no side effects.
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
          ``content_type='service'`` which fails OPEN (compliance pages are
          policy-never-gated).

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

    # (e) ultimate fallback: fail-closed, except compliance which fails open.
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

from dataclasses import dataclass                  # noqa: E402
from datetime import datetime                      # noqa: E402

from shared import quota as _quota                 # noqa: E402
from shared.db.run import run_db                   # noqa: E402

# §1.2.1 weighted cost. One unlock must not mean both "a paragraph" and "a
# 716-article statute" — but the MEDIAN نظام (18 مواد) must still cost 1, so the
# common case is unchanged.
UNLOCK_COST_MIN = 1
UNLOCK_COST_MAX = 8
ARTICLES_PER_UNLOCK = 25          # regulation with seo_articles rows
CHARS_PER_UNLOCK = 25_000         # chunk-only regulation fallback

# Content types that are never gated and therefore never charged (§1.3): a
# compliance service page is policy-open, so it produces no ledger row at all.
NEVER_CHARGED_TYPES = ("service",)

# The one column set Layer B reads off the ledger.
_UNLOCK_COLS = "unlock_id, content_type, content_id, period_key, cost, unlocked_at"

__all__ += [
    # Layer B — entitlement
    "UNLOCK_COST_MIN",
    "UNLOCK_COST_MAX",
    "ARTICLES_PER_UNLOCK",
    "CHARS_PER_UNLOCK",
    "NEVER_CHARGED_TYPES",
    "AccessDecision",
    "unlock_cost",
    "resolve_access",
    "stored_library_count",
    "parent_regulation_of_article",
]


def _clamp_cost(value: float) -> int:
    return max(UNLOCK_COST_MIN, min(UNLOCK_COST_MAX, int(value)))


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


def unlock_cost(
    supabase: SupabaseClient, content_type: str, content_id: str
) -> int:
    """The weighted charge for unlocking one item (§1.2.1 / D4). SYNC.

    ``article | judgment | circular | form`` → **1**.
    ``regulation`` → ``clamp(ceil(n_articles / 25), 1, 8)`` where ``n_articles``
    is the number of ``seo_articles`` rows for the regulation. A chunk-only
    regulation (no ``seo_articles`` rows) is weighted by body length instead:
    ``clamp(ceil(total_chars / 25000), 1, 8)``.

    Why a regulation is weighted at all: ``/library/full/regulation/{slug}``
    returns EVERY مادة untruncated for one unlock, so a flat cost would let a
    rational extractor charge only at the regulation level and take the whole
    statutory corpus 25× cheaper than the per-مادة price.

    Fail-safe direction is UP, not down: any lookup failure falls back to the
    minimum (1) rather than blocking the user — the real extraction bounds are
    the per-period rate and the route-scoped rate limit, not this number.
    """
    ct = (content_type or "").strip()
    if ct != "regulation":
        return UNLOCK_COST_MIN

    n_articles = 0
    try:
        res = (
            supabase.table("seo_articles")
            .select("article_no", count="exact")
            .eq("regulation_id", str(content_id))
            .limit(1)
            .execute()
        )
        n_articles = int(getattr(res, "count", None) or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("unlock_cost: seo_articles count failed (%s): %s", content_id, e)

    if n_articles > 0:
        return _clamp_cost(math.ceil(n_articles / ARTICLES_PER_UNLOCK))

    # Chunk-only regulation → weight by total body length.
    total_chars = 0
    try:
        offset, page = 0, 1000
        while True:
            res = (
                supabase.table("chunks_v2")
                .select("content")
                .eq("regulation_id", str(content_id))
                .range(offset, offset + page - 1)
                .execute()
            )
            batch = res.data or []
            total_chars += sum(len(r.get("content") or "") for r in batch)
            if len(batch) < page:
                break
            offset += page
    except Exception as e:  # noqa: BLE001
        logger.warning("unlock_cost: chunk length scan failed (%s): %s", content_id, e)

    if total_chars <= 0:
        return UNLOCK_COST_MIN
    return _clamp_cost(math.ceil(total_chars / CHARS_PER_UNLOCK))


@dataclass
class AccessDecision:
    """The Layer B verdict for one (user, item) at one instant.

    ``reason`` ∈ ``open`` · ``already_unlocked`` · ``granted`` · ``anonymous`` ·
    ``locked`` · ``quota_exhausted`` · ``frozen_library`` · ``unresolvable``.
    The refusal reasons map 1:1 onto the D14 402 payload built by
    ``backend.app.errors.library_refusal_response``.

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
) -> AccessDecision:
    """May this user unlock this item right now? — the Layer B entry point.

    ``user_id`` is a **users.user_id** (NOT an auth id); route callers map it via
    ``case_service.get_user_id``. ``surface`` is analytics only ('library' |
    'reference') and MUST NEVER affect the charge, or the reference panel becomes
    a bypass again.

    Decision order (this IS the §1.2 predicate; do not reorder):
      1. ``service`` → open, free, no ledger row (policy-never-gated).
      2. no user     → ``anonymous`` (anon allowance is 0).
      3. item gate is ``'open'`` → free, no ledger row.
      4. existing row → paid OR same period ⇒ ``already_unlocked``; else
         ``frozen_library`` + the shelf count for the upgrade CTA.
      5. مادة whose parent نظام is already unlocked ⇒ ``already_unlocked`` (D5).
         The reverse does NOT hold: unlocking one مادة does not unlock the نظام.
      6. locked account → ``locked``.
      7. quota available → INSERT (idempotent) ⇒ ``granted`` / ``charged=True``.
      8. otherwise → ``quota_exhausted`` with used/limit/resets_at.

    NEVER memoize this, and never call it from a cacheable path (D11).
    """
    ct = (content_type or "").strip()
    cid = str(content_id or "")

    # 1. Compliance services are never gated, never charged, never a ledger row.
    if ct in NEVER_CHARGED_TYPES:
        return AccessDecision(may_unlock=True, charged=False, reason="open")

    # 2. Anonymous: 0 unlocks per period, by policy.
    if not user_id:
        return AccessDecision(may_unlock=False, charged=False, reason="anonymous")

    # 3. Layer A — an OPEN item costs nothing and writes nothing.
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
# PHASE 2 — CONTENT ENDPOINTS (/regulations docs + /compliance)
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

    Pages the ``seo_item_meta`` sidecar for every ``(content_type=X, slug NOT
    NULL)`` row's ``content_id`` in 1,000-row range chunks — PostgREST clamps any
    response to max-rows=1000 (documented trap at the top of this module), so a
    single unbounded select would silently truncate — until the sidecar is
    exhausted (or the sample ceiling is passed, see below).

    Returns the id list ONLY while the wing is in "sample mode": published count
    ``<= SAMPLE_MODE_MAX_IDS`` (300). Above that the wing is in full-corpus steady
    state and this returns ``None`` so the hub listers keep their legacy
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


# Chunk size for the ``id IN (...)`` corpus fetch in sample mode. Same reason
# ``_slug_map`` chunks its sidecar lookup: PostgREST encodes ``in.(...)`` in the
# query string and hundreds of uuids blow the server's URL-length limit into a
# 400. The published set is <= SAMPLE_MODE_MAX_IDS (300), so this is at most 2
# round-trips per wing per page.
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


# --- /regulations hub -----------------------------------------------------


def _apply_reg_filters(qb, entity, doc_type, sector, q):
    """Apply the regulations hub filters to a query builder (chainable).

    ``entity`` matches ``entity_id`` when it is a UUID, else ``entity_ref``;
    ``doc_type`` = ``doc_type_bucket``; ``sector`` = array-contains on
    ``sectors``; ``q`` = ilike on ``clean_title``. Empty/blank filters are no-ops.
    """
    entity = (entity or "").strip()
    doc_type = (doc_type or "").strip()
    sector = (sector or "").strip()
    q = (q or "").strip()
    if entity:
        if _is_uuid(entity):
            qb = qb.eq("entity_id", entity)
        else:
            qb = qb.eq("entity_ref", entity)
    if doc_type:
        qb = qb.eq("doc_type_bucket", doc_type)
    if sector:
        qb = qb.contains("sectors", [sector])
    if q:
        qb = qb.ilike("clean_title", f"%{q}%")
    return qb


def _reg_count(supabase, entity, doc_type, sector, q, *, in_force_only=False) -> int:
    qb = supabase.table("regulations_v2").select("id", count="exact")
    qb = _apply_reg_filters(qb, entity, doc_type, sector, q)
    if in_force_only:
        qb = qb.eq("status_class", "in_force")
    return int((qb.limit(1).execute().count) or 0)


# Column set the /regulations hub reads (shared by the legacy + sample paths).
_REG_HUB_SELECT = (
    "id, reg_ref, clean_title, title, entity_name, status_class, "
    "doc_type_bucket, summary, sectors"
)


def _reg_hub_sort_key(r: dict[str, Any]) -> tuple[int, str, str]:
    """Python ordering for a sample-mode /regulations page: in-force partition
    first, then ``COALESCE(clean_title, title)``, then ``id`` — the same contract
    the legacy two-partition DB pagination expresses."""
    in_force = 0 if r.get("status_class") == "in_force" else 1
    title = (r.get("clean_title") or r.get("title") or "")
    return (in_force, title, str(r.get("id") or ""))


def regulations_hub_total_pages(
    supabase: SupabaseClient,
    entity: Optional[str] = None,
    doc_type: Optional[str] = None,
    sector: Optional[str] = None,
    q: Optional[str] = None,
) -> int:
    """Total hub pages for the filtered regulations set (for the anon-cap body).

    In SAMPLE MODE (``_published_ids`` returns a list) this counts only the
    filtered PUBLISHED rows — an EXACT page count. In full-corpus steady state
    (``_published_ids`` → None) it counts the filtered CORPUS rows (9/page): every
    reg is slugged then (``build_seo_slugs``), so counting all vs. slugged rows is
    identical.
    """
    try:
        pub_ids = _published_ids(supabase, "regulation")
        if pub_ids is not None:
            rows = _fetch_corpus_by_ids(
                supabase,
                "regulations_v2",
                "id",
                pub_ids,
                lambda qb: _apply_reg_filters(qb, entity, doc_type, sector, q),
            )
            total = len(rows)
        else:
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

    Ordering contract = **in-force first, then title**. There is no DB column
    that expresses that priority, so pagination is done over two partitions —
    ``status_class = 'in_force'`` (title-ordered) followed by everything else
    (title-ordered) — and the requested window is sliced across the boundary
    with DB ``range`` queries (never fetching more than 9 detail rows). Slugs are
    resolved with ONE batched sidecar lookup; only rows that HAVE a slug are
    returned (a hub lists only published items).

    Returns ``{"items": [...], "page": page, "total_pages": N}``. The anon
    depth-cap is enforced by the route (this function always returns real data).

    SAMPLE MODE (stage-1 rollout): when ``_published_ids`` returns a list, the
    page is cut from the PUBLISHED set — all matching published rows are fetched
    by id, sorted in Python by the SAME contract (in-force first, then title, then
    id), and the 9-item window is sliced — so a page never comes back empty just
    because the corpus's first pages hold no published rows. In full-corpus steady
    state (``_published_ids`` → None) the legacy two-partition path below runs
    unchanged.
    """
    page = max(1, int(page or 1))
    ps = HUB_PAGE_SIZE
    offset = (page - 1) * ps

    pub_ids = _published_ids(supabase, "regulation")
    if pub_ids is not None:
        # SAMPLE MODE — paginate the published set in Python (set is <= 300).
        all_rows = _fetch_corpus_by_ids(
            supabase,
            "regulations_v2",
            _REG_HUB_SELECT,
            pub_ids,
            lambda qb: _apply_reg_filters(qb, entity, doc_type, sector, q),
        )
        all_rows.sort(key=_reg_hub_sort_key)
        total = len(all_rows)
        raw_rows: list[dict[str, Any]] = all_rows[offset : offset + ps]
    else:
        # LEGACY (full-corpus steady state) — unchanged two-partition pagination.
        select_cols = _REG_HUB_SELECT
        try:
            total = _reg_count(supabase, entity, doc_type, sector, q)
            count_a = _reg_count(supabase, entity, doc_type, sector, q, in_force_only=True)

            raw_rows = []
            if offset < count_a:
                # Window starts inside the in-force partition.
                take_a = min(ps, count_a - offset)
                qa = supabase.table("regulations_v2").select(select_cols)
                qa = _apply_reg_filters(qa, entity, doc_type, sector, q).eq(
                    "status_class", "in_force"
                )
                qa = qa.order("clean_title").order("id").range(offset, offset + take_a - 1)
                raw_rows.extend(qa.execute().data or [])
                remaining = ps - take_a
                if remaining > 0:
                    # Straddle: continue into the "rest" partition from its head.
                    qb = supabase.table("regulations_v2").select(select_cols)
                    qb = _apply_reg_filters(qb, entity, doc_type, sector, q).neq(
                        "status_class", "in_force"
                    )
                    qb = qb.order("clean_title").order("id").range(0, remaining - 1)
                    raw_rows.extend(qb.execute().data or [])
            else:
                # Window is entirely inside the "rest" partition.
                b_offset = offset - count_a
                qb = supabase.table("regulations_v2").select(select_cols)
                qb = _apply_reg_filters(qb, entity, doc_type, sector, q).neq(
                    "status_class", "in_force"
                )
                qb = qb.order("clean_title").order("id").range(b_offset, b_offset + ps - 1)
                raw_rows.extend(qb.execute().data or [])
        except Exception as e:  # noqa: BLE001
            logger.exception("Error listing regulations hub: %s", e)
            raise _hub_error()

    total_pages = max(1, math.ceil(total / ps)) if total else 1

    slugs = _slug_map(supabase, "regulation", [r.get("id") for r in raw_rows])
    items: list[dict[str, Any]] = []
    for r in raw_rows:
        slug = slugs.get(str(r.get("id")))
        if not slug:
            continue
        items.append(
            {
                "slug": slug,
                "title": (r.get("clean_title") or r.get("title") or "").strip(),
                "entity_name": r.get("entity_name"),
                "status": map_reg_status(r.get("status_class")),
                "doc_type": map_doc_type_bucket(r.get("doc_type_bucket")),
                "summary_snippet": _text_snippet(r.get("summary"), 160),
                "sectors": r.get("sectors") or [],
            }
        )

    return {"items": items, "page": page, "total_pages": total_pages}


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


def _unwrap_pdf_url(value: Any) -> Optional[str]:
    """Normalize ``pdf_url`` — 946 corpus rows store it as a JSON-encoded array
    (e.g. ``["https://….pdf"]``); return the first URL, or the plain string."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.startswith("["):
        try:
            arr = json.loads(s)
        except ValueError:
            return None
        first = next((x for x in arr if isinstance(x, str) and x.strip()), None)
        return first.strip() if first else None
    return s


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


def _chunk_content_map(
    supabase: SupabaseClient, chunk_ids: list[Any]
) -> dict[str, str]:
    """Batch-resolve ``{chunk_id: content}`` from ``chunks_v2`` for fallback bodies.

    An article whose ``extraction_status != 'extracted'`` renders its owning chunk
    as the body — this fetches those chunk bodies in one (chunked) ``IN`` lookup.
    Dedupes ids and chunks the ``in.(...)`` at 150 (PostgREST URL-length trap).
    Fail-soft: a blip yields ``{}`` (those sections render empty rather than 500).
    """
    ids = list(dict.fromkeys(str(c) for c in chunk_ids if c))
    if not ids:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(ids), 150):
        chunk = ids[i : i + 150]
        try:
            res = (
                supabase.table("chunks_v2")
                .select("id, content")
                .in_("id", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("chunk content map lookup failed: %s", e)
            continue
        for r in res.data or []:
            cid = r.get("id")
            if cid is not None:
                out[str(cid)] = r.get("content") or ""
    return out


def get_regulation_doc(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """Full /regulations/{slug} document payload, or ``None`` when the slug is
    unknown (the route turns ``None`` into a 404 «النظام غير موجود»).

    Resolves ``slug → content_id`` via the sidecar, loads the reg row, resolves the
    gate ONCE, and builds the reading surface from the BEST source:

      ARTICLES-FIRST (the regulation has ``seo_articles`` rows — now sourced from
      ``articles_v2``):
        - ``toc``: EVERY مادة — ``{id: slug, title: article_label,
          position: article_no}`` — always free.
        - ``visible_sections``: the first 3 مواد (by article_no), each body run
          through ``truncate_for_gate(text, gate, free_chars=600)`` — text = the
          مادة's ``article_text`` (fallback: its owning chunk's content). id =
          ``'art-{no}'``. Gated bytes never leave the server.
        - ``hidden_section_count`` = total مواد − 3.

      CHUNK FALLBACK (a regulation with NO ``seo_articles`` rows — article-less /
      chunk-only): the original chunk-based ``toc`` (id=chunk id) + first-3-chunk
      ``visible_sections`` — untouched.

    ``article_index`` lists ONLY the PUBLISHED مواد (opt-in; empty by default) and
    is additive to either path.
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

    if articles:
        # ARTICLES-FIRST — toc + first-3 preview built from the مواد index.
        toc = [
            {
                "id": str(a.get("slug") or ""),
                "title": a.get("article_label"),
                "position": int(a.get("article_no") or 0),
            }
            for a in articles
        ]
        first3 = articles[:3]
        # Only fallback (non-'extracted') مواد need their owning chunk body.
        fallback_ids = [
            a.get("chunk_id")
            for a in first3
            if a.get("extraction_status") != "extracted" or not a.get("article_text")
        ]
        chunk_body = _chunk_content_map(supabase, fallback_ids)

        visible_sections: list[dict[str, Any]] = []
        for a in first3:
            no = int(a.get("article_no") or 0)
            if a.get("extraction_status") == "extracted" and a.get("article_text"):
                # Extracted single-مادة text → strip its duplicate heading + footnote
                # noise for DISPLAY (chunk fallback below is multi-article — kept).
                body = _clean_article_display_text(a.get("article_text") or "")
            else:
                body = chunk_body.get(str(a.get("chunk_id")), "")
            cut = truncate_for_gate(body, gate, free_chars=600)
            visible_sections.append(
                {
                    "id": f"art-{no}",
                    "title": a.get("article_label"),
                    "text": cut["visible_text"],
                    "is_truncated": cut["is_truncated"],
                    "hidden_placeholder_lines": cut["hidden_placeholder_lines"],
                }
            )
        hidden_section_count = max(0, len(articles) - 3)
    else:
        # CHUNK FALLBACK — the legacy chunk-based toc + first-3-chunk preview.
        try:
            toc_res = (
                supabase.table("chunks_v2")
                .select("id, title, position")
                .eq("regulation_id", content_id)
                .order("position")
                .execute()
            )
            toc_rows = toc_res.data or []

            vis_res = (
                supabase.table("chunks_v2")
                .select("id, title, position, content")
                .eq("regulation_id", content_id)
                .order("position")
                .limit(3)
                .execute()
            )
            vis_rows = vis_res.data or []
        except Exception as e:  # noqa: BLE001
            logger.exception("Error loading regulation doc chunks (%s): %s", slug, e)
            raise LunaHTTPException(
                status_code=500,
                code=ErrorCode.INTERNAL_ERROR,
                detail="حدث خطأ أثناء جلب النظام",
            )

        toc = [
            {
                "id": str(r.get("id")),
                "title": r.get("title"),
                "position": int(r.get("position") or 0),
            }
            for r in toc_rows
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
                }
            )
        hidden_section_count = max(0, len(toc_rows) - 3)

    official_sources: list[dict[str, str]] = []
    if reg.get("landing_url"):
        official_sources.append({"title": "الموقع الرسمي", "href": reg["landing_url"]})
    pdf_href = _unwrap_pdf_url(reg.get("pdf_url"))
    if pdf_href:
        official_sources.append({"title": "PDF الرسمي", "href": pdf_href})
    # ⚠ ALWAYS WITHHELD — open-tier included (user decision 2026-07-28, reversing
    # the plan's §1.2 "always shown").
    #
    # The block is a per-item deep link carrying the source system's own id (the
    # BOE law UUID; an opaque encrypted NCAR document id), so across the corpus it
    # is a slug → official-ID crosswalk. That is just as true of an OPEN-TIER
    # نظام — more so, since those are the flagship indexed pages — so the tier
    # does NOT earn an exemption. An earlier pass withheld only when
    # ``gate == "gated"``, which left the 54 open-tier أنظمة publishing their
    # crosswalk to anonymous crawlers.
    #
    # Served instead by ``official_sources_for_item`` through the authed reveal,
    # which is therefore the ONLY renderer — so «المصادر الرسمية» can never appear
    # twice on one page.
    #
    # Not viewer-dependent: this is Layer A (a property of the wing, not the
    # caller), so the ISR payload stays cacheable.
    official_sources = []

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
    }


# --- /compliance hub ------------------------------------------------------


def _apply_service_filters(qb, provider, sector, q):
    """Apply the compliance hub filters (chainable). ``provider`` = ilike on
    ``provider_name``; ``sector`` = array-contains; ``q`` = ilike on
    ``service_name_ar``. Blank filters are no-ops."""
    provider = (provider or "").strip()
    sector = (sector or "").strip()
    q = (q or "").strip()
    if provider:
        qb = qb.ilike("provider_name", f"%{provider}%")
    if sector:
        qb = qb.contains("sectors", [sector])
    if q:
        qb = qb.ilike("service_name_ar", f"%{q}%")
    return qb


# Column set the /compliance hub reads (shared by the legacy + sample paths).
_SERVICE_HUB_SELECT = (
    "id, service_name_ar, provider_name, is_most_used, sectors, "
    "intro_description"
)


def _service_hub_sort_key(r: dict[str, Any]) -> tuple[int, str]:
    """Python ordering for a sample-mode /compliance page: ``is_most_used`` desc
    (most-used first), then ``service_name_ar`` — the same contract the legacy DB
    ``.order(is_most_used desc).order(service_name_ar)`` expresses."""
    most_used = 0 if r.get("is_most_used") else 1
    return (most_used, r.get("service_name_ar") or "")


def compliance_hub_total_pages(
    supabase: SupabaseClient,
    provider: Optional[str] = None,
    sector: Optional[str] = None,
    q: Optional[str] = None,
) -> int:
    """Total hub pages for the filtered services set (for the anon-cap body).

    In SAMPLE MODE (``_published_ids`` → list) this counts only the filtered
    PUBLISHED services — an EXACT page count. In full-corpus steady state
    (``_published_ids`` → None) it counts filtered CORPUS rows (9/page); every
    service is slugged then, so counting all vs. slugged rows is identical."""
    try:
        pub_ids = _published_ids(supabase, "service")
        if pub_ids is not None:
            rows = _fetch_corpus_by_ids(
                supabase,
                "services",
                "id",
                pub_ids,
                lambda qb: _apply_service_filters(qb, provider, sector, q),
            )
            total = len(rows)
        else:
            qb = supabase.table("services").select("id", count="exact")
            qb = _apply_service_filters(qb, provider, sector, q)
            total = int((qb.limit(1).execute().count) or 0)
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
) -> dict[str, Any]:
    """One page (9 items) of the /compliance hub.

    Ordering = ``is_most_used`` desc, then ``service_name_ar``. Only slugged
    (published) services are returned. Returns ``{"items": [...], "page": page,
    "total_pages": N}``.

    SAMPLE MODE (stage-1 rollout): when ``_published_ids`` returns a list, the
    page is cut from the PUBLISHED set — all matching published rows are fetched
    by id, sorted in Python by the SAME contract, and the 9-item window is sliced
    — so a page never comes back empty. In steady state (``_published_ids`` →
    None) the legacy single-``range`` DB query below runs unchanged.
    """
    page = max(1, int(page or 1))
    ps = HUB_PAGE_SIZE
    offset = (page - 1) * ps

    pub_ids = _published_ids(supabase, "service")
    if pub_ids is not None:
        # SAMPLE MODE — paginate the published set in Python (set is <= 300).
        all_rows = _fetch_corpus_by_ids(
            supabase,
            "services",
            _SERVICE_HUB_SELECT,
            pub_ids,
            lambda qb: _apply_service_filters(qb, provider, sector, q),
        )
        all_rows.sort(key=_service_hub_sort_key)
        total = len(all_rows)
        rows = all_rows[offset : offset + ps]
    else:
        # LEGACY (full-corpus steady state) — unchanged single-range query.
        try:
            qb = supabase.table("services").select(
                _SERVICE_HUB_SELECT,
                count="exact",
            )
            qb = _apply_service_filters(qb, provider, sector, q)
            res = (
                qb.order("is_most_used", desc=True)
                .order("service_name_ar")
                .range(offset, offset + ps - 1)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Error listing compliance hub: %s", e)
            raise _hub_error()

        total = int(res.count or 0)
        rows = res.data or []

    total_pages = max(1, math.ceil(total / ps)) if total else 1

    slugs = _slug_map(supabase, "service", [r.get("id") for r in rows])
    items: list[dict[str, Any]] = []
    for r in rows:
        slug = slugs.get(str(r.get("id")))
        if not slug:
            continue
        items.append(
            {
                "slug": slug,
                "title": r.get("service_name_ar") or "",
                "provider_name": r.get("provider_name"),
                "is_most_used": bool(r.get("is_most_used")),
                "sectors": r.get("sectors") or [],
                "intro_snippet": _text_snippet(r.get("intro_description"), 160),
            }
        )

    return {"items": items, "page": page, "total_pages": total_pages}


# --- /compliance/{slug} service page --------------------------------------


def get_compliance_service(
    supabase: SupabaseClient, slug: str
) -> Optional[dict[str, Any]]:
    """Full /compliance/{slug} payload, or ``None`` when the slug is unknown
    (route → 404 «الخدمة غير موجودة»).

    Compliance pages are policy-never-gated: everything ships free.
    ``resolve_gate`` is still called (it returns 'open' for services) so the ONE
    gate decision point is exercised consistently across every content type.
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    try:
        meta = (
            supabase.table("seo_item_meta")
            .select("content_id")
            .eq("content_type", "service")
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

        sv_res = (
            supabase.table("services")
            .select(
                "id, service_name_ar, provider_name, intro_title, "
                "intro_description, requirements, required_documents, steps, "
                "youtube_url, service_url, url, pdf_link, sectors"
            )
            .eq("id", content_id)
            .limit(1)
            .execute()
        )
        sv_rows = sv_res.data or []
        if not sv_rows:
            return None
        sv = sv_rows[0]
    except Exception as e:  # noqa: BLE001
        logger.exception("Error loading compliance service (%s): %s", slug, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="حدث خطأ أثناء جلب الخدمة",
        )

    # Exercised for consistency; services always resolve to 'open'.
    resolve_gate(supabase, "service", str(content_id))

    return {
        "slug": slug,
        "title": sv.get("service_name_ar") or "",
        "provider_name": sv.get("provider_name"),
        "intro_title": sv.get("intro_title"),
        "intro_description": sv.get("intro_description"),
        "requirements": sv.get("requirements") or [],
        "required_documents": sv.get("required_documents") or [],
        "steps": sv.get("steps") or [],
        "youtube_url": sv.get("youtube_url"),
        "official_url": sv.get("service_url") or sv.get("url"),
        "pdf_link": sv.get("pdf_link"),
        "sectors": sv.get("sectors") or [],
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


def _apply_circular_filters(qb, entity_ids: Optional[list[str]], q: Optional[str]):
    """Apply the circulars hub filters to a query builder (chainable).

    ``entity_ids`` (already resolved by ``_resolve_entity_ids``): ``None`` = no
    entity filter; a list = ``entity_id IN (...)`` — an EMPTY list means the name
    filter matched nothing, so a non-matching sentinel UUID is used to force zero
    rows on the typed ``entity_id`` column. ``q`` = ``ilike`` on ``title``. Blank
    ``q`` is a no-op.
    """
    q = (q or "").strip()
    if entity_ids is not None:
        qb = qb.in_("entity_id", entity_ids if entity_ids else [_NO_MATCH_UUID])
    if q:
        qb = qb.ilike("title", f"%{q}%")
    return qb


def _circular_count(supabase, entity_ids: Optional[list[str]], q: Optional[str]) -> int:
    qb = supabase.table("circulars").select("id", count="exact")
    qb = _apply_circular_filters(qb, entity_ids, q)
    return int((qb.limit(1).execute().count) or 0)


# Column set the /circulars hub reads (shared by the legacy + sample paths).
_CIRCULAR_HUB_SELECT = "id, circ_ref, title, content, source, entity_id"


def _circular_hub_sort_key(r: dict[str, Any]) -> tuple[str, str]:
    """Python ordering for a sample-mode /circulars page: ``title`` then ``id`` —
    the same contract the legacy DB ``.order(title).order(id)`` expresses."""
    return ((r.get("title") or ""), str(r.get("id") or ""))


def circulars_hub_total_pages(
    supabase: SupabaseClient,
    entity: Optional[str] = None,
    q: Optional[str] = None,
) -> int:
    """Total hub pages for the filtered circulars set (for the anon-cap body).

    In SAMPLE MODE (``_published_ids`` → list) this counts only the filtered
    PUBLISHED circulars — an EXACT page count. In full-corpus steady state
    (``_published_ids`` → None) it counts filtered CORPUS rows (9/page); every
    circular is slugged then (``build_seo_slugs``), so counting all vs. slugged
    rows is identical.
    """
    try:
        entity_ids = _resolve_entity_ids(supabase, entity)
        pub_ids = _published_ids(supabase, "circular")
        if pub_ids is not None:
            rows = _fetch_corpus_by_ids(
                supabase,
                "circulars",
                "id",
                pub_ids,
                lambda qb: _apply_circular_filters(qb, entity_ids, q),
            )
            total = len(rows)
        else:
            total = _circular_count(supabase, entity_ids, q)
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
) -> dict[str, Any]:
    """One page (9 items) of the /circulars hub.

    Ordering = ``title`` (ascending), a single partition (no in-force split like
    regs) → one DB ``range`` query + one batched sidecar slug lookup + one batched
    ``entities`` name lookup. Filters: ``entity`` (issuing-authority name, ilike
    via ``entities`` → ``entity_id IN``, or a bare UUID direct) and ``q`` (title
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
    """
    page = max(1, int(page or 1))
    ps = HUB_PAGE_SIZE
    offset = (page - 1) * ps

    entity_ids = _resolve_entity_ids(supabase, entity)
    pub_ids = _published_ids(supabase, "circular")
    if pub_ids is not None:
        # SAMPLE MODE — paginate the published set in Python (set is <= 300).
        all_rows = _fetch_corpus_by_ids(
            supabase,
            "circulars",
            _CIRCULAR_HUB_SELECT,
            pub_ids,
            lambda qb: _apply_circular_filters(qb, entity_ids, q),
        )
        all_rows.sort(key=_circular_hub_sort_key)
        total = len(all_rows)
        rows = all_rows[offset : offset + ps]
    else:
        # LEGACY (full-corpus steady state) — unchanged single-range query.
        try:
            total = _circular_count(supabase, entity_ids, q)
            qb = supabase.table("circulars").select(_CIRCULAR_HUB_SELECT)
            qb = _apply_circular_filters(qb, entity_ids, q)
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
        source_label, _ = _normalize_circular_source(r.get("source"))
        content = r.get("content") or ""
        items.append(
            {
                "slug": slug,
                "title": (r.get("title") or "").strip(),
                "entity_name": names.get(str(r.get("entity_id"))),
                "source_label": source_label,
                "body_snippet": _text_snippet(content, 160),
                "body_length": len(content),
            }
        )

    return {"items": items, "page": page, "total_pages": total_pages}


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
    # ALWAYS WITHHELD, short/open تعاميم included — see get_regulation_doc.
    # A no-op in the current corpus (``circulars.source`` is a provenance label,
    # never a URL) but wired so the rule holds if that ever changes.
    official_sources = []

    metadata: list[dict[str, str]] = []
    if entity_name:
        metadata.append({"label": "الجهة المصدرة", "value": entity_name})
    if circ.get("circ_ref"):
        metadata.append({"label": "المرجع", "value": str(circ["circ_ref"])})

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

# How many cited-regulation mesh links an ANON judgment page shows. ``None`` =
# show all, which is the deliberate default: the list carries only the regulation
# NAME + the article NUMBER (never a line of the regulation's content), and it IS
# the internal-linking mesh — the whole reason this wing exists is to push link
# equity into /regulations and the ~50k مادة pages. Gating it would gate our own
# crawl graph, not the user's value. Set to 3 to restore the plan's free-3 cap.
JUDGMENT_CITED_FREE_LIMIT: Optional[int] = None

# Free-character budget for a GATED judgment section — this wing's own, like
# ARTICLE_FREE_CHARS and FORM_BODY_FREE_CHARS, because the shared
# GATE_FREE_CHARS_DEFAULT (400) is not calibrated for this body.
#
# Sized against the REAL ruling text (mean ~11k chars, median ~7k), not the
# ~475-char summary columns the sections used to be built from: 1,200 chars gives
# an anonymous reader and a crawler a substantial, genuinely rankable passage of
# the court's own words — thin content ranks badly, so an over-tight gate costs
# traffic — while still withholding roughly 85–90% of a typical judgment.
JUDGMENT_FREE_CHARS = 1200

# The first parsed section renders FREE; everything after it is gated. The
# opening of a ruling is its narrative setup (parties, وقائع, المطالبات) — the
# part that carries the search terms and tells a reader whether this judgment is
# about their problem. What follows is the التسبيب and the المنطوق: the reasoning
# and the disposition, which is the value a signup buys. A single-section
# document (no `##` headings — the common case) is gated with the free budget
# above, exactly like a long circular.
JUDGMENT_FREE_LEADING_SECTIONS = 1

# Column set the /judgments hub reads (shared by the legacy + sample paths).
# It carries ALL FOUR title-source columns (short_summary → summary → facts →
# ruling) even though the card only prints a short_summary snippet: the card
# title and the doc-page H1 must be byte-identical, and both come from
# ``judgment_display_title`` walking that same chain. Selecting fewer columns
# would silently give the ~1k summary-less judgments a different title on the hub
# than on their own page.
_JUDGMENT_HUB_SELECT = (
    "id, case_ref, court, court_level, city, case_number, judgment_number, "
    "date_hijri, date_gregorian, legal_domains, short_summary, summary, "
    "facts, ruling"
)

# Column set the /judgments/{slug} doc page reads: metadata + the title chain +
# the mesh source + ``content`` (the real ruling text that becomes the body).
# `facts`/`reasoning`/`ruling`… are deliberately NOT selected: they are summaries
# of the document, and the document itself is what this page publishes.
_JUDGMENT_DOC_SELECT = (
    "id, case_ref, court, court_level, city, case_number, judgment_number, "
    "date_hijri, date_gregorian, appeal_result, legal_domains, short_summary, "
    "summary, details_url, referenced_regulations, content"
)

# Leading bullet / list noise on a summary line. ``short_summary`` is stored as a
# markdown bullet list («- نزاع حول…\n- المحكمة قضت…»), which reads as broken
# punctuation once collapsed into a one-line card snippet.
_JUDGMENT_BULLET_RE = re.compile(r"^[ \t]*[-*•·—–]+[ \t]*", re.MULTILINE)

# الرقم on a cited-regulation ref may be «16/1» (article/paragraph) or use
# Arabic-Indic digits; the مادة sidecar key only ever holds the article integer.
_JUDGMENT_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


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


def _judgment_date_ordinal(value: Any) -> int:
    """``YYYY-MM-DD`` → ``YYYYMMDD`` as an int; 0 when absent/unparseable.

    Sorting keys need a NUMBER, not a string: the hub orders by date DESCENDING,
    and a descending sort inside an otherwise-ascending tuple key is expressed by
    negating the value — which strings cannot do.
    """
    iso = _iso_date(value)
    if not iso:
        return 0
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso)
    if not m:
        return 0
    return int(m.group(1) + m.group(2) + m.group(3))


def _judgment_hub_sort_key(r: dict[str, Any]) -> tuple[int, int, str]:
    """Python ordering for a sample-mode /judgments page: newest first, dateless
    LAST, then ``id``.

    Mirrors the legacy DB path's ``.order('date_gregorian', desc=True,
    nullsfirst=False).order('id')`` EXACTLY. The ``nullsfirst=False`` is
    load-bearing on both sides: Postgres puts NULLs FIRST on a DESC order by
    default, which would open the hub with the 11,419 dateless judgments — this
    exact trap has bitten this codebase before. The first key element is the
    has-a-date partition (0 before 1) and the second is the NEGATED date ordinal
    (descending inside an ascending tuple).
    """
    ordinal = _judgment_date_ordinal(r.get("date_gregorian"))
    return (0 if ordinal else 1, -ordinal, str(r.get("id") or ""))


def _apply_judgment_filters(
    qb, court_level: Optional[str], domain: Optional[str], q: Optional[str]
):
    """Apply the judgments hub filters to a query builder (chainable).

    ``court_level`` = exact match ('first_instance' | 'appeal' | 'supreme');
    ``domain`` = array-contains on ``legal_domains``; ``q`` = ilike on
    ``short_summary`` (the free lead — never on a gated section column, so the
    filter can never be used as an oracle for gated text). Blank filters are
    no-ops.
    """
    court_level = (court_level or "").strip()
    domain = (domain or "").strip()
    q = (q or "").strip()
    if court_level:
        qb = qb.eq("court_level", court_level)
    if domain:
        qb = qb.contains("legal_domains", [domain])
    if q:
        qb = qb.ilike("short_summary", f"%{q}%")
    return qb


def _judgment_count(
    supabase, court_level: Optional[str], domain: Optional[str], q: Optional[str]
) -> int:
    qb = supabase.table("cases").select("id", count="exact")
    qb = _apply_judgment_filters(qb, court_level, domain, q)
    return int((qb.limit(1).execute().count) or 0)


def judgments_hub_total_pages(
    supabase: SupabaseClient,
    *,
    court_level: Optional[str] = None,
    domain: Optional[str] = None,
    q: Optional[str] = None,
) -> int:
    """Total hub pages for the filtered judgments set (for the anon-cap body).

    In SAMPLE MODE (``_published_ids`` → list) this counts only the filtered
    PUBLISHED judgments — an EXACT page count. In full-corpus steady state
    (``_published_ids`` → None) it counts filtered CORPUS rows (9/page); every
    judgment is slugged then (``build_judgment_slugs``), so counting all vs.
    slugged rows is identical.
    """
    try:
        pub_ids = _published_ids(supabase, "judgment")
        if pub_ids is not None:
            rows = _fetch_corpus_by_ids(
                supabase,
                "cases",
                "id",
                pub_ids,
                lambda qb: _apply_judgment_filters(qb, court_level, domain, q),
            )
            total = len(rows)
        else:
            total = _judgment_count(supabase, court_level, domain, q)
    except LunaHTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Error counting judgments hub: %s", e)
        raise _hub_error()
    return max(1, math.ceil(total / HUB_PAGE_SIZE)) if total else 1


def list_judgments_hub(
    supabase: SupabaseClient,
    *,
    page: int = 1,
    court_level: Optional[str] = None,
    domain: Optional[str] = None,
    q: Optional[str] = None,
) -> dict[str, Any]:
    """One page (9 items) of the /judgments hub.

    Ordering = ``date_gregorian`` DESC with dateless judgments LAST, then ``id``
    — newest first, because recency is what a reader scanning a judgments
    directory is actually after. Single partition → one DB ``range`` query + one
    batched sidecar slug lookup. Filters: ``court_level`` (exact), ``domain``
    (an element of ``legal_domains``), ``q`` (ilike on the free ``short_summary``).
    Only slugged (published) rows are returned.

    Card item shape = ``{slug, title, court, court_level, court_level_label, city,
    date_hijri, date_gregorian, domains, snippet}``. ``title`` is
    ``judgment_display_title`` (the derived subject + court + Hijri year) and
    ``snippet`` is the first ~160 chars of the bullet-stripped ``short_summary``
    — the always-free lead, NEVER a gated section. Returns ``{"items": [...],
    "page": page, "total_pages": N}``; the anon depth-cap is enforced by the route.

    SAMPLE MODE (stage-1 rollout): when ``_published_ids`` returns a list, the
    page is cut from the PUBLISHED set — all matching published rows are fetched
    by id, sorted in Python by ``_judgment_hub_sort_key`` (which reproduces the DB
    ordering, NULL handling included), and the 9-item window is sliced — so a page
    never comes back empty. In steady state the legacy ``range`` query runs.
    """
    page = max(1, int(page or 1))
    ps = HUB_PAGE_SIZE
    offset = (page - 1) * ps

    pub_ids = _published_ids(supabase, "judgment")
    if pub_ids is not None:
        # SAMPLE MODE — paginate the published set in Python (set is <= 300).
        all_rows = _fetch_corpus_by_ids(
            supabase,
            "cases",
            _JUDGMENT_HUB_SELECT,
            pub_ids,
            lambda qb: _apply_judgment_filters(qb, court_level, domain, q),
        )
        all_rows.sort(key=_judgment_hub_sort_key)
        total = len(all_rows)
        rows = all_rows[offset : offset + ps]
    else:
        # LEGACY (full-corpus steady state) — single-range query.
        try:
            total = _judgment_count(supabase, court_level, domain, q)
            qb = supabase.table("cases").select(_JUDGMENT_HUB_SELECT)
            qb = _apply_judgment_filters(qb, court_level, domain, q)
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

    slugs = _slug_map(supabase, "judgment", [r.get("id") for r in rows])

    items: list[dict[str, Any]] = []
    for r in rows:
        slug = slugs.get(str(r.get("id")))
        if not slug:
            continue
        items.append(
            {
                "slug": slug,
                "title": judgment_display_title(r),
                "court": (r.get("court") or "").strip(),
                "court_level": r.get("court_level"),
                "court_level_label": court_level_label(r.get("court_level")),
                "city": r.get("city"),
                "date_hijri": r.get("date_hijri"),
                "date_gregorian": _iso_date(r.get("date_gregorian")),
                "domains": [d for d in (r.get("legal_domains") or []) if d],
                "snippet": _text_snippet(_strip_bullets(r.get("short_summary")), 160),
            }
        )

    return {"items": items, "page": page, "total_pages": total_pages}


def _judgment_article_int(article_no: str) -> Optional[int]:
    """Leading article integer of a cited «الرقم» («16/1» → 16), or ``None``.

    A citation may point at a PARAGRAPH («16/1», «185/4» — ~8% of refs): the مادة
    sidecar key only ever holds the article integer, so the link resolves to
    المادة 16 while the DISPLAYED ``article_no`` keeps the precise «16/1» the
    judgment actually cited. Arabic-Indic digits are normalized first.
    """
    s = str(article_no or "").translate(_JUDGMENT_ARABIC_INDIC).strip()
    m = re.match(r"(\d+)", s)
    return int(m.group(1)) if m else None


def _judgment_cited_regulations(
    supabase: SupabaseClient, row: dict[str, Any]
) -> tuple[list[dict[str, Any]], int]:
    """Resolve ``cases.referenced_regulations`` into the internal-linking mesh.

    Returns ``(items, total)`` where each item is ``{title, article_no, reg_slug,
    article_slug}`` and ``total`` is the deduped citation count BEFORE
    ``JUDGMENT_CITED_FREE_LIMIT`` is applied (so the frontend can say «و12 مرجعاً
    آخر» if a cap is ever switched on).

    THE JOIN (verified live): a ref's ``regulation_id`` is the corpus's
    ``reg_ref`` TEXT key («17642_reg_037»), NOT a uuid — it joins to
    ``regulations_v2.reg_ref``; that row's ``id::text`` is the sidecar key for the
    regulation page's slug, and ``'{id}#{article_no}'`` is the sidecar key for the
    مادة page's slug (same key shape ``_regulation_article_index`` publishes).
    ~30% of refs carry NO ``regulation_id`` (لائحة/executive-regulation citations
    the pipeline could not match) — those still appear in the list, with the cited
    name and article number and ``reg_slug=None``: a citation is worth showing
    even when we have no page to link it to.

    ``title`` prefers the RESOLVED regulation's ``clean_title`` over the name as
    cited in the judgment, so the anchor text matches the H1 of the page it links
    to («لائحة نظام المحاكم التجارية» cited → «اللائحة التنفيذية لنظام المحاكم
    التجارية» as the target page titles itself). Unresolved refs keep the cited
    name verbatim.

    Dedupe is by ``(regulation_id, article_no)`` preserving first-seen order (the
    same مادة is commonly cited a dozen times in one judgment); refs with no
    ``regulation_id`` dedupe on their cited NAME instead, so two different
    unmatched نظام names citing المادة 5 do not collapse into one.

    EVERY lookup is batched — one query per lookup table, ``in.()`` chunked at
    ``_ID_IN_CHUNK`` (150). A large ``in.()`` blows PostgREST's URL length into a
    400; this has bitten the hub listers before. Fail-soft throughout: a lookup
    error costs the links, not the page.
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

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        reg_ref = str(ref.get("regulation_id") or "").strip() or None
        cited_title = str(ref.get("النظام") or "").strip()
        article_no = str(ref.get("الرقم") or "").strip()
        if not reg_ref and not cited_title:
            continue
        key = (reg_ref or cited_title, article_no)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {"reg_ref": reg_ref, "cited_title": cited_title, "article_no": article_no}
        )

    total = len(entries)
    if JUDGMENT_CITED_FREE_LIMIT is not None:
        entries = entries[: max(0, JUDGMENT_CITED_FREE_LIMIT)]
    if not entries:
        return [], total

    # 1. reg_ref → regulations_v2 row (id + canonical title), batched + chunked.
    reg_refs = list(dict.fromkeys(e["reg_ref"] for e in entries if e["reg_ref"]))
    reg_by_ref: dict[str, dict[str, Any]] = {}
    for i in range(0, len(reg_refs), _ID_IN_CHUNK):
        chunk = reg_refs[i : i + _ID_IN_CHUNK]
        try:
            res = (
                supabase.table("regulations_v2")
                .select("id, reg_ref, clean_title, title")
                .in_("reg_ref", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("cited-regulation lookup failed: %s", e)
            continue
        for r in res.data or []:
            rr = r.get("reg_ref")
            if rr:
                reg_by_ref[str(rr)] = r

    # 2. regulation page slugs (one batched sidecar lookup, itself chunked).
    reg_slugs = _slug_map(
        supabase, "regulation", [r.get("id") for r in reg_by_ref.values()]
    )

    # 3. مادة page slugs — sidecar key '{regulation uuid}#{article int}'.
    article_keys: list[str] = []
    for e in entries:
        reg = reg_by_ref.get(e["reg_ref"]) if e["reg_ref"] else None
        art_int = _judgment_article_int(e["article_no"])
        if reg and reg.get("id") and art_int is not None:
            article_keys.append(f"{reg['id']}#{art_int}")
    article_slugs = _slug_map(supabase, "article", article_keys)

    items: list[dict[str, Any]] = []
    for e in entries:
        reg = reg_by_ref.get(e["reg_ref"]) if e["reg_ref"] else None
        title = e["cited_title"]
        reg_slug: Optional[str] = None
        article_slug: Optional[str] = None
        if reg:
            title = (reg.get("clean_title") or reg.get("title") or title or "").strip()
            reg_slug = reg_slugs.get(str(reg.get("id")))
            art_int = _judgment_article_int(e["article_no"])
            if art_int is not None:
                article_slug = article_slugs.get(f"{reg.get('id')}#{art_int}")
        # A مادة URL is nested under its regulation's slug
        # (/regulations/{reg_slug}/articles/{article_slug}), so an article slug
        # without a published parent is not a linkable address — drop it.
        if not reg_slug:
            article_slug = None
        items.append(
            {
                "title": title,
                "article_no": e["article_no"] or None,
                "reg_slug": reg_slug,
                "article_slug": article_slug,
            }
        )
    return items, total


def _judgment_metadata(row: dict[str, Any]) -> list[dict[str, str]]:
    """The judgment metadata card — label/value pairs, EMPTY VALUES OMITTED.

    Only ``court`` and ``case_number`` are populated corpus-wide; ``city``,
    ``judgment_number``, ``date_hijri``, ``date_gregorian`` and ``appeal_result``
    are each missing on a large slice of the corpus, so the card is built by
    omission rather than rendering «غير متوفر» rows.
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
    return [{"label": label, "value": value} for label, value in pairs if value]


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
            .select("content_id")
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

    return case_rows[0] if case_rows else None


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
      * ``sections`` = the REAL ruling text (``content``) parsed by
        ``_parse_judgment_body`` into the document's own ``##`` sections, each
        ``{id, title, text, is_truncated, hidden_placeholder_lines, is_free}``.
        The first ``JUDGMENT_FREE_LEADING_SECTIONS`` render whole; the rest go
        through ``truncate_for_gate(..., free_chars=JUDGMENT_FREE_CHARS)`` when
        the gate resolves to ``'gated'`` — the hidden bytes are DROPPED here, not
        hidden client-side. ``id`` is positional (``s1``, ``s2``…) and matches the
        authed ``get_full_judgment`` payload, so the client-side enhancer can
        swap section-for-section.
      * ``cited_regulations`` = the internal-linking mesh (see
        ``_judgment_cited_regulations``); ``cited_total`` is its pre-cap size.
      * ``hidden_section_count`` counts the sections ACTUALLY truncated (a gated
        section shorter than the free budget is not "hidden").

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
    # NEVER let the free-leading rule swallow the whole document: a judgment with
    # one section (no `##` headings, or a single «## نص الحكم» — the majority of
    # the corpus) would otherwise render entirely free while still reporting
    # gate='gated'. Cap the free run so at least one section always faces the gate.
    free_leading = min(JUDGMENT_FREE_LEADING_SECTIONS, max(0, len(parsed_sections) - 1))

    sections: list[dict[str, Any]] = []
    hidden_section_count = 0
    for index, parsed in enumerate(parsed_sections):
        is_free = index < free_leading
        if is_free:
            cut = {
                "visible_text": parsed["text"],
                "is_truncated": False,
                "hidden_placeholder_lines": 0,
            }
        else:
            cut = truncate_for_gate(
                parsed["text"], gate, free_chars=JUDGMENT_FREE_CHARS
            )
        if cut["is_truncated"]:
            hidden_section_count += 1
        sections.append(
            {
                "id": parsed["id"],
                "title": parsed["title"],
                "text": cut["visible_text"],
                "is_truncated": cut["is_truncated"],
                "hidden_placeholder_lines": cut["hidden_placeholder_lines"],
                "is_free": is_free,
            }
        )

    cited, cited_total = _judgment_cited_regulations(supabase, row)

    official_sources: list[dict[str, str]] = []
    details_url = (row.get("details_url") or "").strip()
    if details_url.startswith("http://") or details_url.startswith("https://"):
        official_sources.append(
            {"title": "مصدر الحكم — وزارة العدل", "href": details_url}
        )
    # ALWAYS WITHHELD, open-tier included — see the note in get_regulation_doc.
    official_sources = []

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
        "sections": sections,
        "cited_regulations": cited,
        "cited_total": cited_total,
        "official_sources": official_sources,
        "gate_effective": gate,
        "hidden_section_count": hidden_section_count,
    }


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
# 'service' (compliance) has no full-content function: it is policy-never-gated,
# so its anon payload is already complete — there is nothing to "unlock", and it
# is never charged.
#
# ⚠ get_full_regulation returns {id, title, text} sections and NO ``sharh_md`` —
# شرح is reachable only one-مادة-at-a-time via get_full_article. This is a moat
# invariant, not an oversight: bundling شرح into the continuous regulation payload
# would collapse the AI layer's unlock count ~15× (plan §1.2). Pinned by
# test_full_regulation_never_includes_sharh.
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
                .select("landing_url, pdf_url")
                .eq("id", cid)
                .limit(1)
                .execute()
            )
            row = (res.data or [{}])[0]
            if row.get("landing_url"):
                out.append({"title": "الموقع الرسمي", "href": row["landing_url"]})
            pdf_href = _unwrap_pdf_url(row.get("pdf_url"))
            if pdf_href:
                out.append({"title": "PDF الرسمي", "href": pdf_href})

        elif ct == "judgment":
            res = (
                supabase.table("cases")
                .select("details_url")
                .eq("id", cid)
                .limit(1)
                .execute()
            )
            details_url = ((res.data or [{}])[0].get("details_url") or "").strip()
            if details_url.startswith("http://") or details_url.startswith("https://"):
                out.append(
                    {"title": "مصدر الحكم — وزارة العدل", "href": details_url}
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
    ``articles_v2``) it returns EVERY مادة in article-number order, untruncated —
    ``{"sections": [{"id": 'art-{no}', "title": article_label, "text":
    article_text | owning-chunk content}, ...]}``. A regulation with NO
    ``seo_articles`` rows (article-less / chunk-only) falls back to EVERY chunk in
    reading order (the legacy shape). No gating, no resolve_gate — the whole
    document (the account-only continuous-reading feature). ``None`` when the slug
    is unknown (route → 404 «النظام غير موجود»). Read-only, no counters.
    """
    try:
        content_id = _regulation_id_for_slug(supabase, slug)
        if not content_id:
            return None

        articles = _seo_articles_for_regulation(supabase, str(content_id))
        if articles:
            fallback_ids = [
                a.get("chunk_id")
                for a in articles
                if a.get("extraction_status") != "extracted" or not a.get("article_text")
            ]
            chunk_body = _chunk_content_map(supabase, fallback_ids)
            sections = []
            for a in articles:
                no = int(a.get("article_no") or 0)
                if a.get("extraction_status") == "extracted" and a.get("article_text"):
                    # Extracted single-مادة text → strip duplicate heading + footnote
                    # noise for DISPLAY (chunk fallback below is multi-article — kept).
                    text = _clean_article_display_text(a.get("article_text") or "")
                else:
                    text = chunk_body.get(str(a.get("chunk_id")), "")
                sections.append(
                    {"id": f"art-{no}", "title": a.get("article_label"), "text": text}
                )
            return {"sections": sections}

        # CHUNK FALLBACK — every chunk in reading order (legacy continuous doc).
        res = (
            supabase.table("chunks_v2")
            .select("id, title, position, content")
            .eq("regulation_id", content_id)
            .order("position")
            .execute()
        )
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
    supabase: SupabaseClient, reg_slug: str, article_slug: str
) -> Optional[dict[str, Any]]:
    """FULL مادة payload for /library/full/article (AUTHED).

    Resolves ``reg_slug`` → regulation_id, then the ``seo_articles`` row by
    ``(regulation_id, slug=article_slug)``, and returns the COMPLETE, untruncated
    body plus the FULL cached شرح when present::

        {"text": <full article/chunk body>, "sharh_md": <full شرح|null>}

    Body = the extracted ``article_text`` when ``extraction_status='extracted'``,
    else the whole owning chunk's ``content`` (extraction fallback). ``sharh_md`` is
    the full ``seo_sharh.sharh_md`` (the gated value-add now unlocked) or ``None``
    when no row is cached. No gating/truncation. ``None`` when the regulation slug or
    the article slug is unknown (route → 404 «المادة غير موجودة»). Read-only.
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

    Returns ``{"sections": [{"id", "title", "text"}, ...]}`` — the FULL ruling
    text (``content``) parsed by the SAME ``_parse_judgment_body`` the anon page
    uses, so ids (``s1``, ``s2``…), titles and order match position-for-position,
    with NO truncation. That id parity is the whole point: the client-side
    enhancer matches each returned section to the DOM node it is upgrading, so a
    gated teaser is replaced in place by the full text.

    Parsing (rather than returning the raw blob) is what keeps the two payloads
    in lockstep — deriving the anon sections one way and the authed reveal
    another would desynchronise them the first time a heading changed.

    No gating, no ``resolve_gate``: auth is the boundary (the route's
    ``get_current_user``). ``None`` when the slug is unknown (route → 404 «الحكم
    غير موجود»). Read-only, no counters.
    """
    row = _judgment_row_for_slug(supabase, slug, "id, content")
    if row is None:
        return None
    return {"sections": _parse_judgment_body(row.get("content") or "")}


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
