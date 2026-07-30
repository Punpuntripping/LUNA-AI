"""Public SEO library routes (Phase 0 — sitemap feed) — mounted under ``/api/v1``.

The public library publishes the Saudi-legal corpus as a programmatic reference
site (see ``.claude/plans/seo_public_library.md``). Phase 0 ships only the
**sitemap feed** that backs the frontend's XML sitemap routes.

    GET /public/library/sitemap/{section}   — PUBLIC (no auth). Paged
                                               ``{loc, lastmod}`` feed per section.

Like the ``/public/blog/*`` routes, the public GET intentionally has NO
``Depends(get_current_user)`` — auth is per-endpoint in this codebase (no global
auth middleware), so omitting the dep is what makes the endpoint
anonymous-accessible. Every response carries ``Cache-Control: public,
max-age=3600`` and performs NO per-read side effects (no view-counter writes).

Sections:
  - ``blog``   — published + public + non-deleted ``blog_posts`` (newest first),
                 paged 5,000 URLs/page.
  - ``static`` — a hardcoded set of public marketing / legal routes (one page).

An unknown section is a 404 with an Arabic message.

NAVIGATION HARDENING (``.claude/plans/cloudflare_navigation_hardening.md``), all
of it in this file because only the origin knows what a legal request looks like:

  - §2.1  hub filters are validated before any DB work, and the anon CTA wall no
          longer reports (or computes) the true corpus size.
  - §2.2  the hubs meter how many DISTINCT items they have yielded to a signed-in
          caller (``library_budget_service``) — the only bound left on a paid
          tier, whose page depth is unbounded by design. ANONYMOUS CALLERS ARE
          NEVER METERED HERE and must not be: they all arrive through the ISR
          renderer as one caller (the module header there explains why).
  - §2.3  the same meter feeds the yield-to-open detector — reach without a
          single document open, logged, never enforced.
  - §3.2b the sitemap feed above can be closed to the public internet with
          ``LIBRARY_SITEMAP_INTERNAL_ONLY`` — OFF by default, and it must stay off
          until §3.2 puts the frontend's server→server calls on the private
          network.
  - §3.7  verified search crawlers browse the hubs past the anonymous depth cap.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import time
from typing import Callable, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_current_user_optional, get_supabase
from backend.app.errors import ErrorCode, LunaHTTPException, library_refusal_response
from backend.app.middleware.rate_limit import trust_cf_headers
from backend.app.middleware.route_limits import library_rate_limit
from backend.app.services import (
    case_service,
    library_budget_service as library_budget,
    library_service,
)
from shared.auth.jwt import AuthUser
from shared.config import get_settings
from shared.db.run import run_db
from shared.quota import library_state
from shared.seo.judgment_naming import COURT_LEVEL_LABELS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["library"])

# ``library_rate_limit`` is the shared 20/min reveal budget: ``/library/full``
# and the workspace reference-source endpoint deliberately draw on ONE bucket per
# verified caller, so alternating between them does not buy 40/min (D13.2).
#
# It is a callable INSTANCE, which is why ``route_limits.py`` must never carry
# ``from __future__ import annotations`` — see the header comment there. With PEP
# 563 in force FastAPI cannot resolve an instance's annotations and silently
# reclassifies ``request`` as a query parameter, 422-ing every guarded route.
# Anon-cacheable for an hour (matches the ISR hub cadence). The XML sitemap
# routes are re-fetched, not real-time.
_SITEMAP_CACHE_CONTROL = "public, max-age=3600"

# Library wings exposed as sitemap sections: section name → (seo_item_meta
# content_type, public URL path prefix). Driven by the sidecar, so a section
# only ever lists slugs its wing can actually serve.
_LIBRARY_SITEMAP_SECTIONS = {
    "regulations": ("regulation", "regulations"),
    "compliance": ("service", "compliance"),
    "circulars": ("circular", "circulars"),
}
# ``forms`` and ``articles`` are NOT in the sidecar-driven map above: form slugs
# live on the forms table (approved+published only) and مادة URLs are a nested
# reg-slug/article-slug path — both handled by their own service functions in the
# sitemap route below.
#
# TODO(pdpl): ``judgments`` is DELIBERATELY ABSENT until the PDPL anonymization
# audit passes (plan § Phase 5 — "sitemap waves: judgments (passed-audit only)").
# Judgment pages are SERVED (the /judgments routes below), but nothing must hand a
# crawler an enumerable list of them: this map is the only thing that makes
# ``GET /public/library/sitemap/judgments`` answer, and it would answer to ANY
# anonymous caller — a one-request dump of every published judgment URL, which is
# exactly what the audit is meant to gate. After the audit, enabling the section
# is this one line here:
#     "judgments": ("judgment", "judgments"),
# plus adding "judgments" to the frontend's SITEMAP_SECTIONS + the
# ``app/sitemaps/[section]/route.ts`` switch (the frontend is what Google reads).

# Content endpoints (hubs + doc/service pages) share the same anon hour-cache.
_LIBRARY_CACHE_CONTROL = "public, max-age=3600"

# The per-user header. ANY response whose bytes depend on WHO asked must carry
# this instead of the hour-cache above.
_PRIVATE_CACHE_CONTROL = "private, no-store"


# ============================================
# TIER RESOLUTION + THE CACHE RULE (access-tiers plan §4.5 · D11 · D12)
#
# ⚠ THE CORRECTNESS PROPERTY OF THE WHOLE DESIGN (PART 9 trap 2). The hub
# endpoints now vary their body by the caller's tier, which turns the shared
# hour-cache into a leak the moment an authed response lands in it: a
# subscriber's page-9 body would be replayed to the next anonymous visitor (and
# to Googlebot) for up to an hour. So:
#
#     user present  -> Cache-Control: private, no-store   (never shared)
#     anonymous     -> Cache-Control: public, max-age=3600 + Vary: Authorization
#
# The rule keys off "was a user resolved", NOT off "did the tier change the
# body" — a body that happens to be tier-identical today must not be allowed to
# become tier-varying tomorrow without the header following it. ``Vary`` is
# belt-and-braces for any intermediary that ignores the rule above.
# ============================================

# Wire format needs an int, so the unbounded (paid) cap is reported as this
# sentinel rather than null — the frontend types the field as a number.
_UNBOUNDED_HUB_MAX_PAGE = 9_999

_HUB_MAX_PAGE_BY_TIER = {
    "anon": library_service.ANON_HUB_MAX_PAGE,   # 1
    "free": library_service.FREE_HUB_MAX_PAGE,   # 3
    "paid": _UNBOUNDED_HUB_MAX_PAGE,
}


async def _hub_caller(
    supabase: SupabaseClient, current_user: Optional[AuthUser]
) -> tuple[str, Optional[str]]:
    """``(tier, user_id)`` for a hub request — ONE resolution, two consumers.

    The tier is the caller's browse-depth class: ``'anon'`` | ``'free'`` |
    ``'paid'`` (D12). No user → ``anon``. Otherwise the EFFECTIVE plan decides:
    ``free`` (or a locked account, which browses like a free one — harmless,
    since hub cards are all never-gated metadata) → ``free``; anything else →
    ``paid``.

    Deliberately forgiving on failure, and only in the ``free`` direction: an
    authenticated caller whose profile row or quota RPC read blows up gets the
    FREE cap, never the paid one. The blast radius of that choice is three pages
    of a directory listing carrying zero gated bytes — there is nothing here to
    fail closed *about*, and 500-ing a public hub page because a quota RPC
    hiccuped would be a far worse failure.

    ``user_id`` is the §2.2 item-budget key (``users.user_id``, never an
    ``auth_id`` — that is the id space ``library_items`` and ``library_unlocks``
    join on, and §2.3 needs the join). It is ``None`` for an anonymous caller,
    and also for the vanishing case of a token-valid caller with no ``users``
    row: such a caller is unmetered, which costs nothing, because the same
    failure hands them the FREE tier and its 3-page cap. Traversal needs the paid
    tier, and the paid tier needs a resolvable user.
    """
    if current_user is None:
        return "anon", None

    try:
        user_id = await run_db(case_service.get_user_id, supabase, current_user.auth_id)
    except Exception as e:  # noqa: BLE001
        # Includes the 401 (no users row) / 403 (deletion grace) LunaHTTPExceptions
        # case_service raises — neither should eject a visitor from a PUBLIC page.
        logger.debug("Hub tier: could not resolve user_id (%s) — using 'free'", e)
        return "free", None

    try:
        state = await library_state(supabase, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("Hub tier: library_state failed (%s) — using 'free'", e)
        return "free", user_id

    if state.locked or (state.effective_plan_id or "free") == "free":
        return "free", user_id
    return "paid", user_id


async def _hub_tier(
    supabase: SupabaseClient, current_user: Optional[AuthUser]
) -> str:
    """``_hub_caller``'s tier alone — kept for callers that do not meter."""
    tier, _user_id = await _hub_caller(supabase, current_user)
    return tier


def _apply_hub_cache_headers(
    response: Response,
    current_user: Optional[AuthUser],
    *,
    crawler_bypass: bool = False,
) -> None:
    """Set the hub ``Cache-Control`` per the rule above. Call on EVERY hub path
    (the CTA-wall early return included) — a hub response that leaves without a
    header inherits whatever a proxy decides, which is the same leak.

    ``crawler_bypass`` is the §3.7 exemption having ACTUALLY changed the answer
    (a verified crawler served a page past the anon cap). That body is anonymous
    but must never be shared-cached: the edge cache rule keys on the URL, so a
    crawler's page-9 body parked in it would be replayed to every anonymous
    human who asks for page 9 for the next hour — silently undoing the depth cap
    the exemption was only ever meant to lift FOR THE CRAWLER. Same leak shape as
    the authed one above, different trigger.
    """
    if current_user is not None or crawler_bypass:
        response.headers["Cache-Control"] = _PRIVATE_CACHE_CONTROL
        return
    response.headers["Cache-Control"] = _LIBRARY_CACHE_CONTROL
    response.headers["Vary"] = "Authorization"


def _hub_caps(tier: str) -> dict[str, int]:
    """``{max_page, max_anon_page}`` for a tier — splatted into every hub
    response model. ``max_anon_page`` is the DEPRECATED alias carrying the same
    value for one release (D12) so the frontend does not break mid-build; it no
    longer means "the anon cap", it means "this caller's cap"."""
    cap = _HUB_MAX_PAGE_BY_TIER.get(tier, library_service.ANON_HUB_MAX_PAGE)
    return {"max_page": cap, "max_anon_page": cap}


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean from the environment; anything unrecognised is ``default``.

    Read FRESH on every call (never cached in ``Settings``, which is
    ``lru_cache``d) so a cutover is an env-var flip plus a restart-free redeploy,
    and so tests can toggle it with ``monkeypatch.setenv``. Truthy vocabulary is
    deliberately identical to ``rate_limit._env_bool`` / ``ask_service._envbool``
    so operators only ever learn one set of words.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ============================================
# VERIFIED SEARCH CRAWLERS — the depth-cap exemption
# (cloudflare_navigation_hardening.md §3.7)
#
# Anonymous callers are capped at page 1. Googlebot is anonymous, so it is capped
# too — and once §3.2b closes the sitemap feed to the public internet, a capped
# crawler has NO discovery path left at all. This is the safety net: a VERIFIED
# search crawler browses the hubs to any depth. The deep pages it reaches stay
# ``noindex, follow`` (a frontend concern), so this buys crawl reach, not index
# bloat, and the body is byte-identical to what a human would get at that depth —
# no cloaking.
#
# ⚠ WHAT SIGNAL, AND WHAT IT IS WORTH. Two, in trust order:
#
#   1. ``X-Verified-Bot`` — set at the edge from ``cf.client.bot`` by a Transform
#      Rule, exactly like the ``X-Edge-Secret`` rule §3.4 already ships. This is
#      the REAL signal (Cloudflare verifies crawlers by reverse DNS + published
#      IP ranges), and it is honoured ONLY when ``TRUST_CF_HEADERS`` is on —
#      i.e. only once every record is orange-clouded. Before that the header is
#      forgeable by anyone, so it is ignored outright. ONE trust boundary for the
#      whole backend: the same flag already gates ``CF-Connecting-IP``.
#
#   2. ``User-Agent`` — the fallback that works TODAY, and it is SPOOFABLE. It is
#      accepted anyway because the blast radius is small and bounded: deep hub
#      pages carry titles + snippets that are already published in the sitemap
#      and already crawlable, while every gated BYTE stays behind the unlock
#      ledger, which this does not touch. Someone spoofing Googlebot to page
#      through a directory of public titles has bought themselves the sitemap.
#
# ⚠ AND THE PART THAT ACTUALLY LIMITS IT TODAY: the public library is rendered by
# the Next ISR server, whose fetcher (``frontend/lib/library/api.ts``) forwards NO
# request headers at all — no UA, no cookies. So on the real crawl path (Googlebot
# → Next page → server-side fetch → here) NEITHER signal survives the hop, and
# this exemption cannot fire. It works today only for a caller hitting the API
# directly. Making it work on the crawl path is the FRONTEND half of §3.7: the
# renderer must forward the crawler signal (its own ``X-Verified-Bot``, derived
# from the incoming request) on the hub fetches it makes.
# ============================================

# Lowercased User-Agent substrings of the crawlers allowed past the cap: the five
# engines WAF rule 2 lets through to the sitemap, plus the two AI *search* agents
# §3.12 sets to "allow" (being cited is a discovery channel), plus Search
# Console's live-test fetcher — PART 4's first verification step is a GSC URL
# inspection, which must see what Googlebot sees.
#
# ⚠ NEVER add an SEO-tool crawler here (AhrefsBot, SemrushBot, DotBot, MJ12bot,
# …). WAF rule 0 blocks them FIRST precisely because Cloudflare's Verified Bots
# list contains them; one of these tokens here would hand a paginated dump of the
# corpus to the companies that resell URL inventories.
_VERIFIED_CRAWLER_UA_TOKENS = (
    "googlebot",
    "google-inspectiontool",
    "bingbot",
    "duckduckbot",
    "yandexbot",
    "baiduspider",
    "oai-searchbot",
    "perplexitybot",
)

# Set by the edge Transform Rule on ``cf.client.bot``; trusted only when
# TRUST_CF_HEADERS is on (see the block comment above).
VERIFIED_BOT_HEADER = "x-verified-bot"

_TRUTHY_HEADER_VALUES = {"1", "true", "yes", "on"}


def is_verified_crawler(request: Request) -> bool:
    """Whether this request may skip the hub depth cap (§3.7).

    ``X-Verified-Bot`` from the edge wins when ``TRUST_CF_HEADERS`` is on;
    otherwise the User-Agent allowlist decides. Never raises — an exemption
    check must not be able to 500 a public page.
    """
    if trust_cf_headers():
        # EVERY copy must be truthy, not just the first. `Headers.get()` returns
        # only the FIRST matching line, so a client that pre-sends
        # `X-Verified-Bot: 1` and has the edge append `0` would have its own copy
        # read and the cap lifted — the leftmost-header trap §3.5 exists to kill,
        # reproduced on this header. Today the Transform Rule uses `set` (which
        # overwrites) so that is not reachable through Cloudflare, but the trust
        # basis of §3.7 must not depend on a dashboard setting nobody re-checks.
        # Mirrors `crawler-signal.ts` (`readVerifiedBotSignal`), which already
        # enforces all-copies-truthy: one contract, both layers.
        claims = request.headers.getlist(VERIFIED_BOT_HEADER)
        # A single comma-folded line ("1, 0") is one header carrying two values —
        # split so a folded forgery is caught the same as an appended one.
        values = [
            part.strip().lower()
            for raw in claims
            for part in raw.split(",")
            if part.strip()
        ]
        if values and all(v in _TRUTHY_HEADER_VALUES for v in values):
            return True
        # Behind a trusted edge the header is AUTHORITATIVE in both directions:
        # Cloudflare saw the request and did not call it a verified bot, so a UA
        # claiming otherwise is a forgery and gets no second chance.
        return False

    ua = (request.headers.get("user-agent") or "").lower()
    if not ua:
        return False
    return any(token in ua for token in _VERIFIED_CRAWLER_UA_TOKENS)


def _hub_page_visible(
    request: Request,
    response: Response,
    *,
    page: int,
    tier: str,
    current_user: Optional[AuthUser],
) -> bool:
    """Depth-cap decision + the ``Cache-Control`` header, in one call.

    Returns True when the hub may serve real items. Sets the cache header on the
    way through, because the two decisions are coupled: a crawler served past the
    cap must not leave a shareable body behind (see ``_apply_hub_cache_headers``).
    Call this on EVERY hub handler before touching the DB.
    """
    allowed = library_service.hub_page_allowed(page, tier)
    bypass = False
    if not allowed and tier == "anon" and is_verified_crawler(request):
        allowed = True
        bypass = True
        logger.info(
            "Hub depth cap waived for a verified crawler (page=%s ua=%r)",
            page, (request.headers.get("user-agent") or "")[:120],
        )
    _apply_hub_cache_headers(response, current_user, crawler_bypass=bypass)
    return allowed


# ============================================
# THE CTA-WALL PAGE COUNT — no corpus-size oracle for anon
# (cloudflare_navigation_hardening.md §2.1)
#
# A cap_reached response used to run a COUNT over the filtered corpus and hand
# back the true page total. With a free-text ``q`` that is a counting oracle: ask
# for page 2 with any filter and read off exactly how many rows match it, 9 at a
# time, without ever being served a single item.
#
# THE LINE IS *FILTERED* vs *UNFILTERED*, not anon vs authed (revised 2026-07-30):
#
#   · FILTERED (any q / entity / sector / doc_type / provider / court_level /
#     domain / category) → anon gets the flat ceiling and the count is not even
#     issued. This is the oracle, and it stays shut: the answer MOVES with the
#     filter, so one request per probe reads the corpus a slice at a time.
#   · UNFILTERED → everyone, anon included, gets the real total. The size of the
#     whole corpus is not a secret and never was: it is in the header nav copy
#     («أكثر من 3,000 نظام ولائحة»), in the hub blurbs, and in the sitemap. One
#     fixed number per section leaks nothing a probe could steer, and hiding it
#     cost us the thing the hub is FOR — a paginator that reads «1 2 3 … 169»
#     shows the scale of the library; one that dead-ends at «2» makes a
#     30,000-judgment corpus look like eighteen.
#
# Authenticated callers keep the real number throughout: they have an identity,
# they are metered by the per-user item budget (§2.2), and their CTA wall is an
# UPGRADE prompt whose copy is sized from it.
# ============================================

# "Your cap, plus one" — enough to say "there is more beyond this", never how
# much. Used for FILTERED anon requests only; ``min()`` against the real total is
# deliberately NOT applied there, because computing it is the oracle.
_ANON_WALL_TOTAL_PAGES = library_service.ANON_HUB_MAX_PAGE + 1

# An unfiltered section total is one number that moves only when the corpus does,
# so it is memoised per section. That keeps the property §2.1 originally bought —
# no COUNT on the most-hit anon path — while still reporting the real figure.
# Filtered counts are never memoised: anon never receives one, and an authed
# caller's is already behind the item budget.
_TOTAL_PAGES_TTL_SECONDS = 300
_total_pages_memo: dict[str, tuple[float, int]] = {}


async def _unfiltered_total_pages(
    counter: Callable[..., int], supabase: SupabaseClient, section: str
) -> int:
    """Memoised real page count for a section with NO filters applied.

    Keyed by the SECTION, not by the counter object: the counter is swapped for a
    stub under test, and every stub is an anonymous lambda, so keying on the
    function would collide all five sections onto one entry.
    """
    cached = _total_pages_memo.get(section)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _TOTAL_PAGES_TTL_SECONDS:
        return cached[1]
    total = await run_db(counter, supabase)
    _total_pages_memo[section] = (now, total)
    return total


async def _wall_total_pages(
    tier: str,
    counter: Callable[..., int],
    supabase: SupabaseClient,
    *args,
    section: str,
    filtered: bool,
    **kwargs,
) -> int:
    """``total_pages`` for a cap_reached body — see the block comment above.

    Anon + a filter is the only case that gets the flat ceiling; the count query
    is skipped entirely there.
    """
    if tier == "anon":
        if filtered:
            return _ANON_WALL_TOTAL_PAGES
        return await _unfiltered_total_pages(counter, supabase, section)
    return await run_db(counter, supabase, *args, **kwargs)


def _visible_total_pages(tier: str, real_total: int, *, filtered: bool) -> int:
    """``total_pages`` for a SERVED (non-wall) body.

    Clamping the wall body alone would not close the oracle: page 1 is served,
    and it carries the exact filtered total at the same granularity, so one
    request per filter value still reads the corpus size. Validated live
    2026-07-28 — `q='نظام'` → 4, `'قرار'` → 5, `'zzzqqq'` → 1, moving with the
    result set. So a FILTERED anon page 1 is clamped here too.

    Unfiltered, the real total is returned to everyone — that is the number the
    paginator needs in order to show the last page.
    """
    if tier != "anon" or not filtered:
        return real_total
    return min(int(real_total or 1), _ANON_WALL_TOTAL_PAGES)


# ============================================
# THE PER-USER ITEM BUDGET — bounding corpus REACH
# (cloudflare_navigation_hardening.md §2.2 · §2.3)
#
# §2.1 bounds how many distinct page 1s exist and the depth cap bounds how deep
# anon/free go. Neither touches a PAID caller, whose depth is unbounded on
# purpose — they are the last tier that can still traverse, and the one tier that
# has an identity to charge. So the hubs count the DISTINCT items they yield per
# user (default 500/hour, rolling) and refuse past it with the project's standard
# 429. Everything about the counting lives in ``library_budget_service``; these
# two helpers are the wiring.
#
# ⚠ ANONYMOUS IS NEVER METERED HERE and no amount of tightening will change that:
# anon library traffic arrives through the Next ISR renderer, so an anon key
# would meter the renderer, not the visitor. That layer belongs to the edge.
#
# Ordering inside a handler is load-bearing:
#   1. §2.1 filter validation   (a rejected filter costs nothing)
#   2. tier + user resolution
#   3. depth cap / CTA wall     (a walled response yields no items → not metered)
#   4. ENFORCE the budget       (before the query — a refusal must not cost a
#                                DB round-trip)
#   5. the query
#   6. CHARGE the yielded ids   (after — never charge for items not served)
# ============================================


async def _charge_hub_yield(
    request: Request,
    supabase: SupabaseClient,
    user_id: Optional[str],
    section: str,
    items: list[dict],
) -> None:
    """Record the ids this hub response yielded (§2.2). Never raises, never
    changes the body — ``charge_items`` swallows its own failures."""
    await library_budget.charge_items(
        request,
        user_id,
        library_budget.item_keys(section, items),
        supabase=supabase,
    )


# ============================================
# HUB FILTER VALIDATION — closing the enumeration hole
# (cloudflare_navigation_hardening.md §2.1)
#
# The depth cap bounds how DEEP an anonymous caller goes; it says nothing about
# how many distinct page 1s they can ask for. Every distinct filter value is a
# fresh page 1, so ~125 two-character ``q`` values walk the whole regulations
# corpus without ever requesting page 2. That is the last OPEN enumeration path,
# and it is closed here rather than at the edge because only the origin knows
# what a legal filter value even is.
#
# Two rules, applied before any DB work:
#
#   * FREE-TEXT filters (``ilike '%…%'``) need >= 3 characters. Two characters
#     partition an Arabic corpus efficiently; three overlap heavily and return
#     9 items a time. Blank/absent stays a NO-OP — an unfiltered hub is the
#     normal case and must not 400.
#   * CLOSED-VOCABULARY filters are checked against the real vocabulary. Junk no
#     longer reaches PostgREST at all, and each rejected value is one fewer
#     cache key at the edge.
#
# Sector / domain filters are deliberately NOT bounded here: they are exact
# array-contains matches over a small facet vocabulary (a wrong value returns
# nothing), so validating them would remove junk without removing reach.
# ============================================

_MIN_SEARCH_CHARS = 3

MSG_SEARCH_TOO_SHORT = "اكتب ٣ أحرف على الأقل للبحث"
MSG_INVALID_ENTITY = "جهة غير معروفة"
MSG_INVALID_DOC_TYPE = "نوع وثيقة غير معروف"
MSG_INVALID_COURT_LEVEL = "درجة محكمة غير معروفة"
MSG_INVALID_CATEGORY = "تصنيف غير معروف"

# THE REAL VOCABULARIES — every one of these is imported from the module that
# owns it, never retyped, so a new pipeline bucket or court level cannot become a
# 400 by being added in one place and forgotten here.
#
# ``doc_type`` filters the RAW ``regulations_v2.doc_type_bucket`` (the labels map
# is display-only — see ``_apply_reg_filters``), and the label map's keys are the
# complete live bucket set: verified 2026-07-28 against all 3,373 corpus rows,
# 21 distinct values, zero nulls, zero outside the map.
_DOC_TYPE_VOCAB = frozenset(library_service.DOC_TYPE_BUCKET_LABELS)
# ``cases.court_level`` — 3 values, verified against the whole 30.5k corpus.
_COURT_LEVEL_VOCAB = frozenset(COURT_LEVEL_LABELS)
# ``forms.category`` — the wing's own tuple (the forms table is the only writer).
_FORM_CATEGORY_VOCAB = frozenset(library_service.FORM_CATEGORIES)

# ``regulations_v2.entity_ref`` is a numeric source token ("17900", "5000"): 132
# distinct values in the live corpus, ALL digits, longest 6 (verified
# 2026-07-28). There is no static entity list anywhere in the codebase, so the
# TOKEN SPACE is the vocabulary — and it is a real bound, because ``entity`` is an
# exact ``eq`` match: a value outside this shape cannot match a row, it can only
# mint a cache key. 8 digits of headroom for the ingest pipeline.
_ENTITY_REF_RE = re.compile(r"\A[0-9]{1,8}\Z")
_UUID_RE = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE
)


def _reject(message: str) -> LunaHTTPException:
    """A 400 in the standard envelope, Arabic, never shared-cached.

    ``no-store`` matters: a filter rejection is a property of the REQUEST, and one
    of these parked in the edge cache under a hub URL would serve the 400 to
    everybody who asks for that filter afterwards.
    """
    return LunaHTTPException(
        status_code=400,
        code=ErrorCode.VALIDATION_ERROR,
        detail=message,
        headers={"Cache-Control": _PRIVATE_CACHE_CONTROL},
    )


def _clean(value: Optional[str]) -> Optional[str]:
    """Trim a filter value; blank (or absent) becomes ``None`` = no filter."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _search_text(value: Optional[str]) -> Optional[str]:
    """Validate a free-text (``ilike``) filter: absent, or >= 3 characters."""
    value = _clean(value)
    if value is not None and len(value) < _MIN_SEARCH_CHARS:
        raise _reject(MSG_SEARCH_TOO_SHORT)
    return value


def _vocab_value(
    value: Optional[str], vocab: frozenset[str], message: str
) -> Optional[str]:
    """Validate a closed-vocabulary filter: absent, or a member of ``vocab``."""
    value = _clean(value)
    if value is not None and value not in vocab:
        raise _reject(message)
    return value


def _entity_token(value: Optional[str]) -> Optional[str]:
    """Validate the /regulations ``entity`` filter: an ``entity_id`` UUID or an
    ``entity_ref`` numeric token — the two things ``_apply_reg_filters`` can
    actually match. Anything else is refused rather than turned into a query."""
    value = _clean(value)
    if value is None:
        return None
    if _UUID_RE.match(value) or _ENTITY_REF_RE.match(value):
        return value
    raise _reject(MSG_INVALID_ENTITY)


def _entity_name_or_id(value: Optional[str]) -> Optional[str]:
    """Validate the /circulars ``entity`` filter.

    That wing has no denormalized entity column, so a non-UUID value is resolved
    ``ilike`` against ``entities.entity_name`` — i.e. it is a free-text search in
    everything but name («ا» matches most of the authority list) and takes the
    same >= 3 rule. A UUID is an exact id and passes untouched.
    """
    value = _clean(value)
    if value is None or _UUID_RE.match(value):
        return value
    if len(value) < _MIN_SEARCH_CHARS:
        raise _reject(MSG_SEARCH_TOO_SHORT)
    return value


# ============================================
# RESPONSE MODELS
# ============================================


class SitemapUrl(BaseModel):
    """One entry in a sitemap feed. ``lastmod`` is ISO-8601 or null (static
    pages carry no per-row timestamp)."""

    loc: str
    lastmod: Optional[str] = None


class SitemapResponse(BaseModel):
    """Paged sitemap feed for one section. Frontend XML routes turn each page
    into a ``<urlset>`` and page over ``total_pages``."""

    urls: list[SitemapUrl] = Field(default_factory=list)
    page: int
    total_pages: int


# --- Regulations hub -------------------------------------------------------


class RegHubItem(BaseModel):
    """One card on the /regulations hub grid."""

    slug: str
    title: str
    entity_name: Optional[str] = None
    status: str  # 'active' | 'amended' | 'repealed' | 'draft'
    doc_type: Optional[str] = None
    summary_snippet: str = ""
    sectors: list[str] = Field(default_factory=list)


class RegHubResponse(BaseModel):
    """A page of the /regulations hub. ``cap_reached`` is true past the caller's
    depth cap (items empty; the frontend renders the «سجّل مجاناً» wall). The
    SAME body is served to Googlebot — no cloaking.

    ``max_page`` is THIS CALLER's cap (anon 1 · free 3 · paid unbounded, reported
    as a large sentinel) — it is what the frontend sizes the CTA wall from, so it
    must never be hardcoded to the anon constant again. ``max_anon_page`` is a
    DEPRECATED alias carrying the same value for one release (D12); it is kept
    only so the frontend does not break mid-build. Both are always present so the
    shape is uniform between capped and normal pages."""

    items: list[RegHubItem] = Field(default_factory=list)
    page: int
    total_pages: int
    cap_reached: bool = False
    max_page: int = library_service.ANON_HUB_MAX_PAGE
    max_anon_page: int = library_service.ANON_HUB_MAX_PAGE


# --- Regulation document page ---------------------------------------------


class MetadataEntry(BaseModel):
    label: str
    value: str


class TocEntry(BaseModel):
    id: str
    title: Optional[str] = None
    position: int


class VisibleSection(BaseModel):
    id: str
    title: Optional[str] = None
    text: str
    is_truncated: bool
    hidden_placeholder_lines: int


class OfficialSource(BaseModel):
    title: str
    href: str


class ArticleIndexEntry(BaseModel):
    """One مادة link in the doc-page TOC (from the derived ``seo_articles``
    index). Empty list until the index is built for the regulation."""

    article_no: int
    article_label: str
    slug: str


class RegulationDocResponse(BaseModel):
    """Full /regulations/{slug} payload. ``toc`` lists ALL chunks (always free);
    ``visible_sections`` is the first 3 chunks, truncated when ``gate='gated'``.
    ``status`` is the mapped label; ``draft_notice`` flags non-enacted (مشروع
    نظام) regulations for the frontend warning. ``article_index`` links each مادة
    page (additive; empty until the seo_articles index is built)."""

    slug: str
    title: str
    status: str
    status_raw: Optional[str] = None
    metadata: list[MetadataEntry] = Field(default_factory=list)
    summary_md: Optional[str] = None
    gate: str  # 'open' | 'gated'
    toc: list[TocEntry] = Field(default_factory=list)
    article_index: list[ArticleIndexEntry] = Field(default_factory=list)
    visible_sections: list[VisibleSection] = Field(default_factory=list)
    hidden_section_count: int
    official_sources: list[OfficialSource] = Field(default_factory=list)
    draft_notice: bool = False


# --- Article (مادة) page --------------------------------------------------


class ArticleRegulationRef(BaseModel):
    """The parent-regulation summary embedded in a مادة payload."""

    slug: str
    title: str
    status: str  # 'active' | 'amended' | 'repealed' | 'draft'


class ArticleNavEntry(BaseModel):
    """A prev/next مادة link (within the same regulation)."""

    slug: str
    article_label: str


class SharhTeaser(BaseModel):
    """The anon شرح teaser on a مادة page — gate #3. The FULL sharh_md is NEVER in
    this payload; the full شرح is a gated account feature served by
    ``GET /library/full/article`` to authed callers only.

    ``has_sharh`` is true only when a ``seo_sharh`` row is cached for this مادة (no
    LLM call is triggered on read — pregeneration is offline via
    ``scripts/generate_sharh.py``). ``teaser`` is the first ~170 chars of the شرح,
    whitespace-cut; ``hidden_placeholder_lines`` sizes the signup-gated placeholder
    bars for the hidden remainder."""

    has_sharh: bool = False
    teaser: Optional[str] = None
    hidden_placeholder_lines: int = 0


class RegulationArticleResponse(BaseModel):
    """Full /regulations/{slug}/articles/{article_slug} payload.

    ``text`` is the gate-truncated body: the extracted مادة text when available,
    else the whole owning chunk (``is_fallback_body=True``). When ``gate='gated'``
    and the body exceeds the free budget, ``is_truncated=True`` and
    ``hidden_placeholder_lines`` sizes the placeholder bars — the hidden bytes are
    NOT in this payload (server-side gate). ``sharh`` is the شرح TEASER (gate #3 —
    full شرح never shipped here). ``prev``/``next`` navigate by ``article_no``;
    either may be null at an end."""

    slug: str
    article_no: int
    article_label: str
    regulation: ArticleRegulationRef
    gate: str  # 'open' | 'gated'
    is_fallback_body: bool
    context_title: Optional[str] = None
    text: str
    is_truncated: bool
    hidden_placeholder_lines: int
    sharh: SharhTeaser = Field(default_factory=SharhTeaser)
    prev: Optional[ArticleNavEntry] = None
    next: Optional[ArticleNavEntry] = None


# --- Compliance hub + service page ----------------------------------------


class ComplianceHubItem(BaseModel):
    slug: str
    title: str
    provider_name: Optional[str] = None
    is_most_used: bool = False
    sectors: list[str] = Field(default_factory=list)
    intro_snippet: str = ""


class ComplianceHubResponse(BaseModel):
    """Same envelope as ``RegHubResponse`` — see it for ``max_page`` /
    ``max_anon_page`` (the deprecated alias)."""

    items: list[ComplianceHubItem] = Field(default_factory=list)
    page: int
    total_pages: int
    cap_reached: bool = False
    max_page: int = library_service.ANON_HUB_MAX_PAGE
    max_anon_page: int = library_service.ANON_HUB_MAX_PAGE


class ComplianceServiceResponse(BaseModel):
    """Full /compliance/{slug} payload — all free (services are never gated)."""

    slug: str
    title: str
    provider_name: Optional[str] = None
    intro_title: Optional[str] = None
    intro_description: Optional[str] = None
    requirements: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    youtube_url: Optional[str] = None
    official_url: Optional[str] = None
    pdf_link: Optional[str] = None
    sectors: list[str] = Field(default_factory=list)


# --- Circulars hub + document page ----------------------------------------


class CircularHubItem(BaseModel):
    """One card on the /circulars hub grid. ``source_label`` is the circular's
    provenance label (``'entity'`` / ``'scraped'`` in the current corpus — a URL
    would surface on the doc page's ``official_sources`` instead). ``body_snippet``
    is the first ~160 chars of the (always-free) content; ``body_length`` is the
    full character count so the frontend can hint at reading length."""

    slug: str
    title: str
    entity_name: Optional[str] = None
    source_label: Optional[str] = None
    body_snippet: str = ""
    body_length: int = 0


class CircularHubResponse(BaseModel):
    """A page of the /circulars hub. Shape is IDENTICAL to the regulations hub:
    ``cap_reached`` is true past the caller's depth cap (items empty; frontend
    renders the «سجّل مجاناً» wall — same body served to Googlebot, no cloaking),
    and ``max_page`` (+ its deprecated ``max_anon_page`` alias) is always
    present for a uniform shape."""

    items: list[CircularHubItem] = Field(default_factory=list)
    page: int
    total_pages: int
    cap_reached: bool = False
    max_page: int = library_service.ANON_HUB_MAX_PAGE
    max_anon_page: int = library_service.ANON_HUB_MAX_PAGE


class CircularDocResponse(BaseModel):
    """Full /circulars/{slug} payload.

    ``metadata`` = الجهة المصدرة (entity name) + المرجع (circ_ref). ``source`` is
    normalized: a provenance LABEL surfaces in ``source_label`` while a URL (none
    in the current corpus) would surface in ``official_sources`` — the metadata
    card is never polluted with the provenance token. ``gate_effective`` is the
    post-``effective_circular_gate`` value: a short (<=800-char) circular renders
    fully ``'open'``. ``text`` is the gate-truncated body — when ``gate_effective
    ='gated'`` and the body exceeds the free budget, ``is_truncated=True`` and the
    hidden bytes are NOT in this payload (server-side gate); ``hidden_placeholder_
    lines`` sizes the placeholder bars. ``body_length`` is the full character
    count."""

    slug: str
    title: str
    entity_name: Optional[str] = None
    source_label: Optional[str] = None
    official_sources: list[OfficialSource] = Field(default_factory=list)
    metadata: list[MetadataEntry] = Field(default_factory=list)
    gate_effective: str  # 'open' | 'gated'
    text: str
    is_truncated: bool
    hidden_placeholder_lines: int
    body_length: int


# --- Judgments hub + document page (Phase 5) ------------------------------


class JudgmentHubItem(BaseModel):
    """One card on the /judgments hub grid.

    ``title`` is the DERIVED display title (subject + court + Hijri year) — the
    ``cases`` corpus has no title column, so both this and the page H1 come from
    ``shared/seo/judgment_naming``. ``court_level_label`` is the Arabic rendering
    of the raw ``court_level`` (ابتدائي / استئناف / المحكمة العليا); both are sent
    so the frontend can filter on the raw value and print the label. ``snippet``
    is the first ~160 chars of the bullet-stripped ``short_summary`` — the
    always-free lead, never a gated section. ``date_gregorian`` is an ISO date
    string or null (11.4k judgments carry only a Hijri date)."""

    slug: str
    title: str
    court: str = ""
    court_level: Optional[str] = None
    court_level_label: Optional[str] = None
    city: Optional[str] = None
    date_hijri: Optional[str] = None
    date_gregorian: Optional[str] = None
    domains: list[str] = Field(default_factory=list)
    snippet: str = ""


class JudgmentHubResponse(BaseModel):
    """A page of the /judgments hub (newest first, dateless judgments last).

    Same envelope as every other hub: ``cap_reached`` is true past the caller's
    depth cap (items empty; the frontend renders the «سجّل مجاناً» wall — the SAME
    body is served to Googlebot, no cloaking), and ``max_page`` (+ its deprecated
    ``max_anon_page`` alias) is always present so the shape is uniform between
    capped and normal pages."""

    items: list[JudgmentHubItem] = Field(default_factory=list)
    page: int
    total_pages: int
    cap_reached: bool = False
    max_page: int = library_service.ANON_HUB_MAX_PAGE
    max_anon_page: int = library_service.ANON_HUB_MAX_PAGE


class JudgmentSection(BaseModel):
    """One rendered section of a judgment (الوقائع، الأسباب والتسبيب، المنطوق…).

    ``id`` is the source column name and is STABLE across the anon payload and the
    authed full-content payload, so the client-side enhancer can swap a truncated
    section for its full text in place. ``is_free`` says which layer the section
    belongs to: the free layer (الوقائع / المنطوق / منطوق حكم الاستئناف) says WHAT
    happened and WHAT was decided and is never truncated; the gated layer is the
    legal argumentation. When ``is_truncated`` is true the hidden bytes are NOT in
    this payload — the gate is server-side, not CSS — and
    ``hidden_placeholder_lines`` sizes the placeholder bars."""

    id: str
    title: str
    text: str
    is_truncated: bool
    hidden_placeholder_lines: int
    is_free: bool


class JudgmentCitedRegulation(BaseModel):
    """One entry of the cited-regulations mesh on a judgment page.

    Regulation NAME + article NUMBER only — never a line of the regulation's
    content, which is why the list is not gated (it is the internal-linking mesh
    into /regulations and the مادة pages). ``reg_slug`` / ``article_slug`` are null
    when the citation could not be matched to a PUBLISHED page; the مادة URL is
    nested, so ``article_slug`` is only ever set alongside ``reg_slug``."""

    title: str
    article_no: Optional[str] = None
    reg_slug: Optional[str] = None
    article_slug: Optional[str] = None


class JudgmentDocResponse(BaseModel):
    """Full /judgments/{slug} payload.

    ``subject`` is the derived H1 (what the dispute is ABOUT) and ``title`` is
    that subject plus court + Hijri year (the ``<title>`` base and card title).
    ``summary_md`` (``short_summary``) is ALWAYS free. ``sections`` is the ordered
    section model — empty source columns are skipped entirely, so a first-instance
    judgment simply has no استئناف sections. ``gate_effective`` is the resolved
    gate ('open' | 'gated') and ``hidden_section_count`` counts the sections
    ACTUALLY truncated (a gated section shorter than the free budget is not
    hidden). ``cited_total`` is the deduped citation count before any free-cap."""

    slug: str
    title: str
    subject: str
    court: str = ""
    court_level: Optional[str] = None
    court_level_label: Optional[str] = None
    city: Optional[str] = None
    case_number: Optional[str] = None
    judgment_number: Optional[str] = None
    date_hijri: Optional[str] = None
    date_gregorian: Optional[str] = None
    hijri_year: Optional[str] = None
    appeal_result: Optional[str] = None
    domains: list[str] = Field(default_factory=list)
    metadata: list[MetadataEntry] = Field(default_factory=list)
    summary_md: Optional[str] = None
    sections: list[JudgmentSection] = Field(default_factory=list)
    cited_regulations: list[JudgmentCitedRegulation] = Field(default_factory=list)
    cited_total: int = 0
    official_sources: list[OfficialSource] = Field(default_factory=list)
    gate_effective: str  # 'open' | 'gated'
    hidden_section_count: int = 0


# --- Forms hub + detail + writer handoff (نماذج, Phase 3) ------------------


class FormHubItem(BaseModel):
    """One card on the /forms hub grid. ``use_case_snippet`` is the first ~160
    chars of the (always-free) متى تستخدمه text — the template body never appears
    on a hub card."""

    slug: str
    title: str
    category: Optional[str] = None
    use_case_snippet: str = ""


class FormHubResponse(BaseModel):
    """A page of the /forms hub — PUBLISHED forms only (the liability hard gate:
    ``review_status='approved' AND is_published``; empty today, correct). Same
    envelope as the other hubs: ``cap_reached`` true past the caller's depth cap
    (items empty; «سجّل مجاناً» wall — same body to Googlebot, no cloaking),
    ``max_page`` (+ its deprecated ``max_anon_page`` alias) always present."""

    items: list[FormHubItem] = Field(default_factory=list)
    page: int
    total_pages: int
    cap_reached: bool = False
    max_page: int = library_service.ANON_HUB_MAX_PAGE
    max_anon_page: int = library_service.ANON_HUB_MAX_PAGE


class FormBodyPreview(BaseModel):
    """The gate-truncated preview of a form's template body. The FULL ``body_md``
    is NEVER in the anon payload — only this preview. When the body is gated and
    exceeds the free budget, ``is_truncated=True`` and ``hidden_placeholder_lines``
    sizes the placeholder bars for the frontend."""

    text: str
    is_truncated: bool
    hidden_placeholder_lines: int


class FormLegalBasisEntry(BaseModel):
    """One الأساس النظامي citation — a display LABEL only (no ids), e.g.
    «المادة 74 من نظام العمل»."""

    label: str


class FormDetailResponse(BaseModel):
    """Full /forms/{slug} payload — PUBLISHED forms only (404 otherwise).

    ``use_case_md`` (متى تستخدمه) + ``intro_md`` (شرح) are the FREE SEO layer;
    ``body_preview`` is the gate-truncated template body (full ``body_md`` never
    shipped to anon). ``legal_basis`` links into the المواد; ``has_docx`` flags a
    gated downloadable (served via the download proxy, not here)."""

    slug: str
    title: str
    category: Optional[str] = None
    use_case_md: Optional[str] = None
    intro_md: Optional[str] = None
    body_preview: FormBodyPreview
    legal_basis: list[FormLegalBasisEntry] = Field(default_factory=list)
    has_docx: bool = False


class OpenInWriterResponse(BaseModel):
    """Result of the forms→writer handoff: the id + title of the freshly-copied
    قوالبي template (``user_templates`` row) the user can now edit in the writer."""

    template_id: str
    title: str


# --- Authed full-content (the signup promise) -----------------------------


class LibraryFullSection(BaseModel):
    """One full section (chunk) of a regulation in the continuous-doc payload."""

    id: str
    title: Optional[str] = None
    text: str


class LibraryFullResponse(BaseModel):
    """Full-content payload for ``GET /library/full/{content_type}/{key}`` (AUTHED).

    Private content — the account carrot that makes the signup promise real. The
    field set varies by ``content_type`` (only the relevant ones are populated;
    the rest stay null); ``content_type`` + ``key`` echo the request so the client
    enhancer can match the response to the DOM node it is upgrading:
      - ``regulation`` → ``sections`` (EVERY chunk, full, in order).
      - ``article``    → ``text`` (full مادة/chunk body) + ``sharh_md`` (full شرح or null).
      - ``judgment``   → ``sections`` (EVERY non-empty judgment section, full, in
        order — ``id``s match the anon page's sections so the enhancer swaps them
        in place).
      - ``circular``   → ``text`` (full body).
      - ``form``       → ``body_md`` (full template body; approved+published only).

    ``official_sources`` rides along for every type that has one (regulation /
    judgment / circular). Per the user decision of 2026-07-28 the «المصادر
    الرسمية» block is part of what an unlock buys — the anon doc payloads emit an
    empty list for a gated item — so this is the ONLY place it is served. See
    ``library_service.official_sources_for_item`` for why.

    No truncation — the complete bytes. Entitlement is the boundary, enforced by
    the route before any of this is built."""

    content_type: str
    key: str
    sections: Optional[list[LibraryFullSection]] = None
    text: Optional[str] = None
    sharh_md: Optional[str] = None
    body_md: Optional[str] = None
    official_sources: list[OfficialSource] = Field(default_factory=list)


# ============================================
# THE SITEMAP FEED GATE — internal callers only
# (cloudflare_navigation_hardening.md §3.2b)
#
# 5,000 URLs per page, no auth, no cost: the single largest bulk-enumeration
# surface in the product. It exists for exactly ONE caller — the Next.js XML
# sitemap routes, which turn each page into a ``<urlset>``. Nobody else has a
# legitimate reason to read it, and Google reads the FRONTEND's XML, never this.
#
# ⚠ DEFAULT-PERMISSIVE ON PURPOSE, AND IT MUST STAY THAT WAY UNTIL §3.2 LANDS.
# The frontend still reaches the backend over the PUBLIC internet
# (``NEXT_PUBLIC_API_URL`` → ``api.rayhanai.com``), so an internal-only rule
# enforced today would 404 every sitemap section and break sitemap generation
# immediately. The gate therefore ships OFF and is flipped by an env var:
#
#     1. §3.2 — point the server→server base URL at ``*.railway.internal``
#     2. verify the frontend's ``/sitemaps/{section}`` routes still render
#     3. set ``LIBRARY_SITEMAP_INTERNAL_ONLY=true``
#     4. re-check the GSC Sitemaps report (a 404 surfaces there as a fetch error)
#
# Flipping it BEFORE step 1 takes the sitemaps down; that is the whole ordering
# constraint. Until then the WAF rule 2 challenge (§3.9) is the only control on
# this path, which is why §3.2b exists at all.
# ============================================

SITEMAP_INTERNAL_ONLY_ENV = "LIBRARY_SITEMAP_INTERNAL_ONLY"

# Railway private networking resolves service-to-service traffic on
# ``<service>.railway.internal``; the Host header survives the hop, so this is the
# positive signal. Local dev (``localhost:8000``) is covered by the peer-IP branch.
_INTERNAL_HOST_SUFFIX = ".railway.internal"


def _is_internal_caller(request: Request) -> bool:
    """Whether this request arrived over the private network rather than the
    public internet.

    Fail-CLOSED, in this order:

      1. Any public-edge hop marker (``X-Forwarded-For`` / ``CF-Connecting-IP``)
         → NOT internal, whatever else the request claims. ⚠ THIS CHECK IS
         LOAD-BEARING AND MUST STAY FIRST: on Railway the public edge proxy
         itself dials the container from a PRIVATE address, so a gate that only
         asked "is the peer private?" would call the entire public internet
         internal and be a silent no-op. Putting it ahead of the Host test also
         means a forged ``Host: x.railway.internal`` from outside cannot help.
      2. ``Host`` ends in ``.railway.internal`` → internal. This is the signal
         §3.2 actually creates: private-network calls are addressed to the
         service's internal hostname and the header survives the hop.
      3. Otherwise the socket peer must be private / loopback / link-local —
         IPv6 ULA for Railway's private network, ``127.0.0.1`` for local dev.

    Private networking is a direct container-to-container connection with no
    proxy in between, so a legitimate internal call carries no hop marker.
    Never raises.
    """
    # `is not None`, NOT truthiness. A present-but-EMPTY `X-Forwarded-For:` is
    # still a hop marker; reading it as absent would let the forged-Host branch
    # below win for a genuinely public peer. The whole fail-closed property here
    # rests on "a hop marker is always present", so presence is what we test.
    if (
        request.headers.get("x-forwarded-for") is not None
        or request.headers.get("cf-connecting-ip") is not None
    ):
        return False

    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip().lower()
    # Strip an IPv6 literal's brackets before the suffix test ("[::1]:8000").
    host = host.strip("[]")
    if host.endswith(_INTERNAL_HOST_SUFFIX):
        return True

    peer = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


# ============================================
# PUBLIC — no auth dependency
# ============================================


@router.get(
    "/public/library/sitemap/{section}",
    response_model=SitemapResponse,
)
async def get_library_sitemap(
    section: str,
    request: Request,
    response: Response,
    page: int = Query(1, description="1-based page index; 5000 URLs per page."),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Sitemap feed for one library ``section`` — the frontend XML routes' backend.

    ``blog`` reads the public blog gallery; ``static`` returns the hardcoded
    marketing/legal routes. Unknown sections 404 (Arabic). Read-only: no
    counters are bumped. Sets ``Cache-Control: public, max-age=3600``.

    ⚠ NOT a general-purpose public endpoint, despite the ``/public/`` prefix — it
    is a 5,000-URL-per-page bulk feed and the largest enumeration surface here.
    Setting ``LIBRARY_SITEMAP_INTERNAL_ONLY=true`` restricts it to callers on the
    private network; it is OFF by default and must stay off until §3.2 moves the
    frontend's server→server calls onto ``*.railway.internal``, or every sitemap
    section 404s at once. See the block comment above for the cutover order.

    A refused caller gets the SAME 404 «القسم غير موجود» an unknown section gets:
    an enumeration surface should not confirm its own existence.
    """
    if _env_bool(SITEMAP_INTERNAL_ONLY_ENV) and not _is_internal_caller(request):
        logger.warning(
            "Sitemap feed refused for a public caller (section=%r host=%r peer=%r)",
            section,
            request.headers.get("host"),
            request.client.host if request.client else None,
        )
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="القسم غير موجود",
        )

    settings = get_settings()
    base_url = settings.PUBLIC_WEB_URL

    if section == "static":
        rows = library_service.sitemap_static_urls(base_url)
        urls = [SitemapUrl(**r) for r in rows]
        total_pages = 1
        page_out = 1
    elif section == "blog":
        page_out = max(1, int(page or 1))
        rows, total_pages = await run_db(
            library_service.sitemap_blog_urls, supabase, base_url, page_out
        )
        urls = [SitemapUrl(**r) for r in rows]
    elif section == "articles":
        # مادة pages: nested reg-slug/article-slug path → seo_articles JOINed with
        # the regulation slug map (own service function, not the flat sidecar feed).
        page_out = max(1, int(page or 1))
        rows, total_pages = await run_db(
            library_service.sitemap_article_urls, supabase, base_url, page_out
        )
        urls = [SitemapUrl(**r) for r in rows]
    elif section == "forms":
        # نماذج: slugs live on the forms table (not the sidecar); PUBLISHED forms
        # only (review_status='approved' AND is_published) — own service function.
        page_out = max(1, int(page or 1))
        rows, total_pages = await run_db(
            library_service.sitemap_forms_urls, supabase, base_url, page_out
        )
        urls = [SitemapUrl(**r) for r in rows]
    elif section in _LIBRARY_SITEMAP_SECTIONS:
        content_type, path_prefix = _LIBRARY_SITEMAP_SECTIONS[section]
        page_out = max(1, int(page or 1))
        rows, total_pages = await run_db(
            library_service.sitemap_library_urls,
            supabase,
            base_url,
            content_type,
            path_prefix,
            page_out,
        )
        urls = [SitemapUrl(**r) for r in rows]
    else:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="القسم غير موجود",
        )

    response.headers["Cache-Control"] = _SITEMAP_CACHE_CONTROL
    return SitemapResponse(urls=urls, page=page_out, total_pages=total_pages)


# ============================================
# CONTENT ENDPOINTS — /regulations + /compliance (Phase 2)
#
# Optional-auth (``get_current_user_optional`` — a public page must never get a
# 401), read-only (no counters). The browse-depth cap is enforced here via
# ``_hub_page_visible`` (``library_service.hub_page_allowed`` plus the §3.7
# verified-crawler exemption) — past the cap the endpoint returns a 200 CTA-wall
# body (cap_reached=true, items=[]), the SAME response for humans and for a
# non-crawler bot (no cloaking), never a 4xx.
#
# Every handler runs the §2.1 filter validation FIRST — before tier resolution,
# before any query — so a rejected filter costs one string comparison and never
# becomes a fresh page 1. That is the ONLY thing bounding how many distinct page
# 1s an anonymous caller can ask for; the depth cap bounds only how deep.
#
# Caching is TIER-DEPENDENT: see ``_apply_hub_cache_headers`` above. An authed
# hub response must never reach the shared hour-cache — and neither must a
# crawler's cap-exempt one.
# ============================================


@router.get("/public/library/regulations", response_model=RegHubResponse)
async def list_regulations(
    request: Request,
    response: Response,
    page: int = Query(1, description="1-based page index; 9 items per page."),
    entity: Optional[str] = Query(None, description="entity_ref or entity_id"),
    doc_type: Optional[str] = Query(None, description="doc_type_bucket"),
    sector: Optional[str] = Query(None, description="matches sectors[] (contains)"),
    q: Optional[str] = Query(None, description="ilike on clean_title (>= 3 chars)"),
    current_user: Optional[AuthUser] = Depends(get_current_user_optional),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """/regulations hub list (9 cards/page). Anon-cacheable, authed no-store.

    Filters are validated first (§2.1): ``q`` needs >= 3 chars, ``entity`` must be
    an entity UUID or a numeric ``entity_ref``, ``doc_type`` must be a live
    ``doc_type_bucket``. Junk is a 400 (Arabic), never a fresh page 1.

    A signed-in caller's yielded items are metered (§2.2) — 429 past the
    per-user budget."""
    entity = _entity_token(entity)
    doc_type = _vocab_value(doc_type, _DOC_TYPE_VOCAB, MSG_INVALID_DOC_TYPE)
    sector = _clean(sector)
    q = _search_text(q)
    filtered = bool(entity or doc_type or sector or q)

    tier, user_id = await _hub_caller(supabase, current_user)
    page = max(1, int(page or 1))

    if not _hub_page_visible(
        request, response, page=page, tier=tier, current_user=current_user
    ):
        total_pages = await _wall_total_pages(
            tier,
            library_service.regulations_hub_total_pages,
            supabase,
            entity,
            doc_type,
            sector,
            q,
            section="regulations",
            filtered=filtered,
        )
        return RegHubResponse(
            items=[], page=page, total_pages=total_pages, cap_reached=True,
            **_hub_caps(tier),
        )

    await library_budget.enforce_item_budget(request, user_id)

    data = await run_db(
        library_service.list_regulations_hub,
        supabase,
        page=page,
        entity=entity,
        doc_type=doc_type,
        sector=sector,
        q=q,
    )
    await _charge_hub_yield(request, supabase, user_id, "regulations", data["items"])
    return RegHubResponse(
        items=[RegHubItem(**it) for it in data["items"]],
        page=data["page"],
        total_pages=_visible_total_pages(tier, data["total_pages"], filtered=filtered),
        cap_reached=False,
        **_hub_caps(tier),
    )


@router.get(
    "/public/library/regulations/{slug}", response_model=RegulationDocResponse
)
async def get_regulation(
    slug: str,
    response: Response,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Anonymous, cacheable /regulations/{slug} document payload."""
    doc = await run_db(library_service.get_regulation_doc, supabase, slug)
    if doc is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="النظام غير موجود",
        )
    response.headers["Cache-Control"] = _LIBRARY_CACHE_CONTROL
    return RegulationDocResponse(**doc)


@router.get(
    "/public/library/regulations/{slug}/articles/{article_slug}",
    response_model=RegulationArticleResponse,
)
async def get_regulation_article(
    slug: str,
    article_slug: str,
    response: Response,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Anonymous, cacheable /regulations/{slug}/articles/{article_slug} مادة payload.

    Body is gate-truncated server-side (extracted مادة text, or the whole owning
    chunk when extraction fell back). 404 «المادة غير موجودة» when the regulation
    slug or the article slug is unknown."""
    art = await run_db(
        library_service.get_regulation_article, supabase, slug, article_slug
    )
    if art is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="المادة غير موجودة",
        )
    response.headers["Cache-Control"] = _LIBRARY_CACHE_CONTROL
    return RegulationArticleResponse(**art)


@router.get("/public/library/compliance", response_model=ComplianceHubResponse)
async def list_compliance(
    request: Request,
    response: Response,
    page: int = Query(1, description="1-based page index; 9 items per page."),
    provider: Optional[str] = Query(
        None, description="ilike on provider_name (>= 3 chars)"
    ),
    sector: Optional[str] = Query(None, description="matches sectors[] (contains)"),
    q: Optional[str] = Query(None, description="ilike on service_name_ar (>= 3 chars)"),
    current_user: Optional[AuthUser] = Depends(get_current_user_optional),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """/compliance hub list (9 cards/page). Anon-cacheable, authed no-store.

    ``provider`` is an ``ilike`` on ``provider_name``, i.e. a second free-text
    search, so it takes the same >= 3-char rule as ``q`` (§2.1). A signed-in
    caller's yielded items are metered (§2.2)."""
    provider = _search_text(provider)
    sector = _clean(sector)
    q = _search_text(q)
    filtered = bool(provider or sector or q)

    tier, user_id = await _hub_caller(supabase, current_user)
    page = max(1, int(page or 1))

    if not _hub_page_visible(
        request, response, page=page, tier=tier, current_user=current_user
    ):
        total_pages = await _wall_total_pages(
            tier,
            library_service.compliance_hub_total_pages,
            supabase,
            provider,
            sector,
            q,
            section="compliance",
            filtered=filtered,
        )
        return ComplianceHubResponse(
            items=[], page=page, total_pages=total_pages, cap_reached=True,
            **_hub_caps(tier),
        )

    await library_budget.enforce_item_budget(request, user_id)

    data = await run_db(
        library_service.list_compliance_hub,
        supabase,
        page=page,
        provider=provider,
        sector=sector,
        q=q,
    )
    await _charge_hub_yield(request, supabase, user_id, "compliance", data["items"])
    return ComplianceHubResponse(
        items=[ComplianceHubItem(**it) for it in data["items"]],
        page=data["page"],
        total_pages=_visible_total_pages(tier, data["total_pages"], filtered=filtered),
        cap_reached=False,
        **_hub_caps(tier),
    )


@router.get(
    "/public/library/compliance/{slug}", response_model=ComplianceServiceResponse
)
async def get_compliance(
    slug: str,
    response: Response,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Anonymous, cacheable /compliance/{slug} service payload (all free)."""
    svc = await run_db(library_service.get_compliance_service, supabase, slug)
    if svc is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="الخدمة غير موجودة",
        )
    response.headers["Cache-Control"] = _LIBRARY_CACHE_CONTROL
    return ComplianceServiceResponse(**svc)


# --- /circulars (Phase 5) -------------------------------------------------


@router.get("/public/library/circulars", response_model=CircularHubResponse)
async def list_circulars(
    request: Request,
    response: Response,
    page: int = Query(1, description="1-based page index; 9 items per page."),
    entity: Optional[str] = Query(
        None,
        description=(
            "issuing-authority name (ilike on entities.entity_name, >= 3 chars) "
            "or entity UUID"
        ),
    ),
    q: Optional[str] = Query(None, description="ilike on title (>= 3 chars)"),
    current_user: Optional[AuthUser] = Depends(get_current_user_optional),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """/circulars hub list (9 cards/page, title-ordered). Authed → no-store.

    ``entity`` is a UUID or an authority-name substring (>= 3 chars — it resolves
    through an ``ilike``, so it is free text); ``q`` >= 3 chars (§2.1). A
    signed-in caller's yielded items are metered (§2.2)."""
    entity = _entity_name_or_id(entity)
    q = _search_text(q)
    filtered = bool(entity or q)

    tier, user_id = await _hub_caller(supabase, current_user)
    page = max(1, int(page or 1))

    if not _hub_page_visible(
        request, response, page=page, tier=tier, current_user=current_user
    ):
        total_pages = await _wall_total_pages(
            tier,
            library_service.circulars_hub_total_pages,
            supabase,
            entity,
            q,
            section="circulars",
            filtered=filtered,
        )
        return CircularHubResponse(
            items=[], page=page, total_pages=total_pages, cap_reached=True,
            **_hub_caps(tier),
        )

    await library_budget.enforce_item_budget(request, user_id)

    data = await run_db(
        library_service.list_circulars_hub,
        supabase,
        page=page,
        entity=entity,
        q=q,
    )
    await _charge_hub_yield(request, supabase, user_id, "circulars", data["items"])
    return CircularHubResponse(
        items=[CircularHubItem(**it) for it in data["items"]],
        page=data["page"],
        total_pages=_visible_total_pages(tier, data["total_pages"], filtered=filtered),
        cap_reached=False,
        **_hub_caps(tier),
    )


@router.get(
    "/public/library/circulars/{slug}", response_model=CircularDocResponse
)
async def get_circular(
    slug: str,
    response: Response,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Anonymous, cacheable /circulars/{slug} document payload.

    Metadata + summary snippet are free; the body is gated only when it is long
    (``effective_circular_gate`` renders a <=800-char تعميم fully open). 404
    «التعميم غير موجود» when the slug is unknown."""
    doc = await run_db(library_service.get_circular_doc, supabase, slug)
    if doc is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="التعميم غير موجود",
        )
    response.headers["Cache-Control"] = _LIBRARY_CACHE_CONTROL
    return CircularDocResponse(**doc)


# --- /judgments (Phase 5) -------------------------------------------------


@router.get("/public/library/judgments", response_model=JudgmentHubResponse)
async def list_judgments(
    request: Request,
    response: Response,
    page: int = Query(1, description="1-based page index; 9 items per page."),
    court_level: Optional[str] = Query(
        None, description="exact match: first_instance | appeal | supreme"
    ),
    domain: Optional[str] = Query(None, description="matches legal_domains[] (contains)"),
    q: Optional[str] = Query(None, description="ilike on short_summary (>= 3 chars)"),
    current_user: Optional[AuthUser] = Depends(get_current_user_optional),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """/judgments hub list (9 cards/page, newest first). Authed → no-store.

    Dateless judgments sort LAST (not first, which is what Postgres would do by
    default on a DESC order). Only published (slugged) judgments are listed.
    ``court_level`` is checked against the ``COURT_LEVEL_LABELS`` vocabulary and
    ``q`` needs >= 3 chars (§2.1). A signed-in caller's yielded items are metered
    (§2.2)."""
    court_level = _vocab_value(court_level, _COURT_LEVEL_VOCAB, MSG_INVALID_COURT_LEVEL)
    domain = _clean(domain)
    q = _search_text(q)
    filtered = bool(court_level or domain or q)

    tier, user_id = await _hub_caller(supabase, current_user)
    page = max(1, int(page or 1))

    if not _hub_page_visible(
        request, response, page=page, tier=tier, current_user=current_user
    ):
        total_pages = await _wall_total_pages(
            tier,
            library_service.judgments_hub_total_pages,
            supabase,
            court_level=court_level,
            domain=domain,
            q=q,
            section="judgments",
            filtered=filtered,
        )
        return JudgmentHubResponse(
            items=[], page=page, total_pages=total_pages, cap_reached=True,
            **_hub_caps(tier),
        )

    await library_budget.enforce_item_budget(request, user_id)

    data = await run_db(
        library_service.list_judgments_hub,
        supabase,
        page=page,
        court_level=court_level,
        domain=domain,
        q=q,
    )
    await _charge_hub_yield(request, supabase, user_id, "judgments", data["items"])
    return JudgmentHubResponse(
        items=[JudgmentHubItem(**it) for it in data["items"]],
        page=data["page"],
        total_pages=_visible_total_pages(tier, data["total_pages"], filtered=filtered),
        cap_reached=False,
        **_hub_caps(tier),
    )


@router.get(
    "/public/library/judgments/{slug}", response_model=JudgmentDocResponse
)
async def get_judgment(
    slug: str,
    response: Response,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Anonymous, cacheable /judgments/{slug} document payload.

    Metadata, the ``short_summary`` lead, الوقائع and المنطوق are always free; the
    argumentation sections are gate-truncated SERVER-SIDE (the hidden bytes never
    reach this response). 404 «الحكم غير موجود» when the slug is unknown."""
    doc = await run_db(library_service.get_judgment_doc, supabase, slug)
    if doc is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="الحكم غير موجود",
        )
    response.headers["Cache-Control"] = _LIBRARY_CACHE_CONTROL
    return JudgmentDocResponse(**doc)


# --- /forms (نماذج — Phase 3) ---------------------------------------------
#
# Anon hub + detail, hour-cached, read-only — PUBLISHED forms only
# (review_status='approved' AND is_published; empty today, correct). The
# writer-handoff POST below is the ONE authed forms endpoint.


@router.get("/public/library/forms", response_model=FormHubResponse)
async def list_forms(
    request: Request,
    response: Response,
    page: int = Query(1, description="1-based page index; 9 items per page."),
    category: Optional[str] = Query(None, description="exact match on category"),
    q: Optional[str] = Query(None, description="ilike on title_ar (>= 3 chars)"),
    current_user: Optional[AuthUser] = Depends(get_current_user_optional),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """/forms hub list (9 cards/page). PUBLISHED forms only — empty until a human
    reviewer approves + publishes a drafted form. Authed → no-store.

    ``category`` is checked against ``library_service.FORM_CATEGORIES`` and ``q``
    needs >= 3 chars (§2.1). A signed-in caller's yielded items are metered
    (§2.2)."""
    category = _vocab_value(category, _FORM_CATEGORY_VOCAB, MSG_INVALID_CATEGORY)
    q = _search_text(q)
    filtered = bool(category or q)

    tier, user_id = await _hub_caller(supabase, current_user)
    page = max(1, int(page or 1))

    if not _hub_page_visible(
        request, response, page=page, tier=tier, current_user=current_user
    ):
        total_pages = await _wall_total_pages(
            tier,
            library_service.forms_hub_total_pages,
            supabase,
            category,
            q,
            section="forms",
            filtered=filtered,
        )
        return FormHubResponse(
            items=[], page=page, total_pages=total_pages, cap_reached=True,
            **_hub_caps(tier),
        )

    await library_budget.enforce_item_budget(request, user_id)

    data = await run_db(
        library_service.list_forms_hub,
        supabase,
        page=page,
        category=category,
        q=q,
    )
    await _charge_hub_yield(request, supabase, user_id, "forms", data["items"])
    return FormHubResponse(
        items=[FormHubItem(**it) for it in data["items"]],
        page=data["page"],
        total_pages=_visible_total_pages(tier, data["total_pages"], filtered=filtered),
        cap_reached=False,
        **_hub_caps(tier),
    )


@router.get("/public/library/forms/{slug}", response_model=FormDetailResponse)
async def get_form(
    slug: str,
    response: Response,
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Anonymous, cacheable /forms/{slug} payload — PUBLISHED forms only.

    use_case/intro are free; the template body is gate-truncated (full body never
    shipped to anon). 404 «النموذج غير موجود» when the slug is unknown OR the form
    is not yet approved+published (a draft is indistinguishable from missing to an
    anon client — the liability requirement)."""
    detail = await run_db(library_service.get_form_detail, supabase, slug)
    if detail is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="النموذج غير موجود",
        )
    response.headers["Cache-Control"] = _LIBRARY_CACHE_CONTROL
    return FormDetailResponse(**detail)


# ============================================
# AUTHED — forms → writer handoff
# ============================================


@router.post("/forms/{slug}/open-in-writer", response_model=OpenInWriterResponse)
async def open_form_in_writer(
    slug: str,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
    _rate_limit=Depends(library_rate_limit),
):
    """Copy a PUBLISHED form into the caller's قوالبي (the «افتح هذا النموذج في
    ريحان» conversion CTA). AUTHED. Reuses the per-user templates service
    (``user_templates``) so the full template lands ready to edit in the writer.

    ⚠ THIS IS A REVEAL SURFACE AND IS METERED. It copies ``forms.body_md``
    verbatim into ``user_templates``, i.e. exactly the bytes
    ``/library/full/form/{slug}`` charges one unlock for, and §1.3 puts form
    template bodies in the ALWAYS-GATED class. Without the entitlement check
    below, any authed account — including one that is quota-exhausted, frozen or
    plan-less — could obtain every published form's full template for free by
    POSTing here and reading the copy back out of قوالبي, making every published
    form a free mirror of a metered item.

    Order matters: the LIABILITY gate (approved+published) lives inside
    ``open_form_in_writer`` and must survive every tier, so entitlement is
    checked first and the copy still refuses an unapproved form afterwards.

    Hard gate: 403 «هذا النموذج غير متاح بعد» when the form is not
    approved+published; 404 «النموذج غير موجود» for an unknown slug. NOT cached —
    it mutates (creates a template row) and is per-user."""
    # Same resolver the reveal uses, so the ledger, the shelf and this handoff
    # all agree on what "this form" is. It carries the liability gate, so an
    # unapproved form resolves to None → 404 and is never charged.
    target = await run_db(_resolve_full_target, supabase, "form", slug)
    if not target:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="النموذج غير موجود",
        )

    user_id = await run_db(case_service.get_user_id, supabase, current_user.auth_id)
    decision = await library_service.resolve_access(
        supabase, user_id, "form", target[0], surface="library"
    )
    if not decision.may_unlock:
        return library_refusal_response(decision)

    result = await run_db(
        library_service.open_form_in_writer,
        supabase,
        current_user.auth_id,
        slug,
    )
    return OpenInWriterResponse(**result)


# ============================================
# THE REVEAL — full-content, METERED (the unlock)
#
# Public library pages are ISR-cached + shared, so they can't vary by auth — the
# anon endpoints always ship the gate-truncated payload. A client-side enhancer
# running in the authenticated browser calls THIS endpoint with the user's bearer
# token and swaps in the full content after the «اعرض النص كاملاً» click (the
# reveal, NOT page mount — §5.1).
#
# ⚠ POLICY REVERSAL, 2026-07-27. This block used to say library reads were a
# deliberately unmetered free-account feature. That is now the OPPOSITE of the
# policy: this endpoint is the metered reveal, and it is one of only two places
# (the other being the workspace reference-source endpoint) where a
# ``library_unlocks`` row is ever written. See
# ``.claude/plans/access_tiers_gating.md`` §1.2 / §1.2.1 / §4.4 and
# ``.claude/plans/access_tiers_gating_DECISIONS.md`` D5 / D14 / D16.1 / D16.2.
#
# THREE boundaries stack here, in this order, and each is independent:
#   1. RESOLUTION  — slug → (content_type, content_id). An unknown key is a 404
#      BEFORE any entitlement work, so nobody is ever charged for a 404. For
#      ``form`` the resolver carries the LIABILITY hard gate
#      (review_status='approved' AND is_published), which survives every tier —
#      a Max subscriber still cannot see an unapproved form (PART 9 trap 6).
#   2. ENTITLEMENT — ``resolve_access`` (Layer B). Refusal ⇒ 402 with the D14
#      body and NO content bytes. Anonymous is refused HERE with
#      ``reason='anonymous'``, not by a 401 from the auth dependency: this
#      endpoint is reached from PUBLIC pages, and a 401 would trip the
#      frontend's global redirect-to-login and eject a browsing visitor (D14).
#   3. CONTENT     — the ``get_full_*`` readers, unchanged (no truncation).
#
# Private content ⇒ Cache-Control: private, no-store, on the 200 and on the 402
# alike (never shared/ISR-cached).
# ============================================

# content_type ∈ regulation|article|judgment|circular|form. 'service'
# (compliance) is excluded — it is policy-never-gated, so its anon payload is
# already complete (nothing to unlock); an unknown/excluded type is a 404.
_FULL_CONTENT_TYPES = ("regulation", "article", "judgment", "circular", "form")

_FULL_CACHE_CONTROL = "private, no-store"

# The 404 message per content type (Arabic), shared by the resolver and the
# content fetch so an unknown slug reads the same whichever step catches it.
_FULL_NOT_FOUND_AR = {
    "regulation": "النظام غير موجود",
    "article": "المادة غير موجودة",
    "judgment": "الحكم غير موجود",
    "circular": "التعميم غير موجود",
    "form": "النموذج غير موجود",
}


def _sidecar_content_id(
    supabase: SupabaseClient, content_type: str, slug: str
) -> Optional[str]:
    """``seo_item_meta`` slug → ``content_id`` for one wing. SYNC (run via run_db).

    The generic form of ``library_service._regulation_id_for_slug``; it lives
    here rather than there only because ``library_service`` ownership is split
    across agents this wave. The query shape is IDENTICAL to the one each
    ``get_full_*`` reader uses to find the same row — that parity is load-bearing:
    if entitlement resolved an item by a different query than the content fetch,
    a user could be charged for item A and served item B.
    """
    slug = (slug or "").strip()
    if not slug:
        return None
    try:
        res = (
            supabase.table("seo_item_meta")
            .select("content_id")
            .eq("content_type", content_type)
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Sidecar slug lookup failed (%s/%s): %s", content_type, slug, e)
        return None
    rows = res.data or []
    if not rows:
        return None
    return rows[0].get("content_id") or None


def _resolve_full_target(
    supabase: SupabaseClient, content_type: str, key: str
) -> Optional[tuple[str, Optional[str]]]:
    """Resolve a reveal ``key`` → ``(content_id, parent_regulation_id)``, or None.

    SYNC (run via ``run_db``). ``content_id`` is the ``seo_item_meta`` sidecar id
    — the SAME id space ``library_unlocks`` and ``library_items`` are keyed on,
    so the ledger, the shelf and the gate all agree on what "this item" is.

    Per type:
      * ``regulation`` — sidecar slug → regulation id.
      * ``article``    — ``"{reg_slug}/{article_slug}"`` → ``"{reg_id}#{no}"``,
        and the parent reg id comes back alongside so ``resolve_access`` can
        apply D5 (a unlocked نظام covers its مواد) without re-parsing the key.
      * ``judgment`` / ``circular`` — sidecar slug → corpus id.
      * ``form``      — the forms table itself (forms have NO sidecar rows), and
        the SELECT carries the liability gate. An unapproved form therefore
        resolves to None → 404, indistinguishable from missing, and no unlock is
        ever charged for something the user could not have been shown.
    """
    key = (key or "").strip().strip("/")
    if not key:
        return None

    if content_type == "regulation":
        rid = _sidecar_content_id(supabase, "regulation", key)
        return (str(rid), None) if rid else None

    if content_type == "article":
        parts = key.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            # An article key must be exactly "{reg_slug}/{article_slug}".
            return None
        rid = _sidecar_content_id(supabase, "regulation", parts[0])
        if not rid:
            return None
        try:
            res = (
                supabase.table("seo_articles")
                .select("article_no")
                .eq("regulation_id", str(rid))
                .eq("slug", parts[1])
                .limit(1)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Article slug lookup failed (%s): %s", key, e)
            return None
        rows = res.data or []
        if not rows:
            return None
        article_no = int(rows[0].get("article_no") or 0)
        return (f"{rid}#{article_no}", str(rid))

    if content_type in ("judgment", "circular"):
        cid = _sidecar_content_id(supabase, content_type, key)
        return (str(cid), None) if cid else None

    if content_type == "form":
        try:
            res = (
                supabase.table("forms")
                .select("id")
                .eq("slug", key)
                .eq("review_status", "approved")
                .eq("is_published", True)
                .limit(1)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Form slug lookup failed (%s): %s", key, e)
            return None
        rows = res.data or []
        if not rows or not rows[0].get("id"):
            return None
        return (str(rows[0]["id"]), None)

    return None


async def _record_library_use(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> None:
    """Shelf the item in «مكتبتي» and bump ``use_count``.

    WHO RECORDS WHAT (user decision 2026-07-28 — everything in مكتبتي is ungated):

      * viewing a **gated** page      → nothing at all, no shelf, no charge.
        This is what protects the free summary layer: a signed-in user skimming
        ten judgment summaries must not spend ten unlocks (§5.1).
      * viewing an **open** item      → ``LibraryUseBeacon`` shelves it, free.
        Services, open-tier أنظمة and short تعاميم are never gated, so shelving
        them costs nothing and the الخدمات tab fills.
      * «اعرض النص كاملاً» (this route) → unlock + shelf, HERE.
      * «عرض المصدر»                   → unlock + shelf, in the reference endpoint.
      * «حفظ»                          → unlock + shelf, in ``/library/mine/save``.

    Nothing double-counts, because the beacon and this route cover disjoint sets:
    the beacon fires only for open items, this fires only after a gated item was
    unlocked.

    (History, so the next reader does not re-derive it: this briefly did NOT
    record, under an earlier model where the beacon fired for gated and open
    items alike. That model made every gated page view a shelf write; the current
    one makes it a no-op.)

    The shelf write must never break a content read, so this swallows everything.
    """
    try:
        from backend.app.services import library_items_service
    except ImportError:  # pragma: no cover - only until B2 lands this wave
        logger.debug("library_items_service not present yet — skipping shelf write")
        return
    try:
        await library_items_service.record_use(
            supabase, user_id, content_type, content_id
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "record_use failed (%s/%s): %s — content read is unaffected",
            content_type, content_id, e,
        )


@router.get("/library/full/{content_type}/{key:path}", response_model=LibraryFullResponse)
async def get_library_full(
    content_type: str,
    key: str,
    response: Response,
    current_user: Optional[AuthUser] = Depends(get_current_user_optional),
    supabase: SupabaseClient = Depends(get_supabase),
    _rate_limit=Depends(library_rate_limit),
):
    """The METERED reveal of one library item in full (plan §4.4).

    ``key`` is captured as a ``{key:path}`` param so the nested article key works
    without query params (slugs never contain '/', so a single split is safe):
      - ``regulation`` → ``key`` = reg slug.
      - ``article``    → ``key`` = ``"{reg_slug}/{article_slug}"`` (exactly two
        percent-encoded segments; split on the single internal '/').
      - ``judgment``   → ``key`` = judgment slug.
      - ``circular``   → ``key`` = circular slug.
      - ``form``       → ``key`` = form slug (approved+published ONLY — the
        liability gate holds at every tier).

    Returns **200** with the complete bytes when the caller is entitled, or the
    **402** D14 refusal body (``reason`` ∈ anonymous | locked | quota_exhausted |
    frozen_library) with NO content. 404 (Arabic) for an unknown/unsupported
    ``content_type`` or an unknown key. ``Cache-Control: private, no-store`` on
    every path. Rate-limited to 20/min per verified caller, shared with the
    workspace reference-source endpoint (D13.2).
    """
    # Every exit from this handler carries private/no-store, 404s included: a
    # cached 404 would survive the publish that fixes it, and an intermediary has
    # no business storing anything off this path.
    _no_store = {"Cache-Control": _FULL_CACHE_CONTROL}

    if content_type not in _FULL_CONTENT_TYPES:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="المحتوى غير موجود",
            headers=_no_store,
        )

    key = (key or "").strip().strip("/")

    # 1. RESOLVE FIRST. Entitlement is keyed on the sidecar content_id, and a 404
    #    must never cost an unlock.
    target = await run_db(_resolve_full_target, supabase, content_type, key)
    if target is None:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail=_FULL_NOT_FOUND_AR[content_type],
            headers=_no_store,
        )
    content_id, parent_regulation_id = target

    # 2. ENTITLEMENT (Layer B). user_id is a users.user_id — NEVER an auth_id.
    user_id: Optional[str] = None
    if current_user is not None:
        user_id = await run_db(
            case_service.get_user_id, supabase, current_user.auth_id
        )

    decision = await library_service.resolve_access(
        supabase,
        user_id,
        content_type,
        content_id,
        surface="library",
        parent_regulation_id=parent_regulation_id,
    )
    if not decision.may_unlock:
        # NO content is fetched, let alone returned. The refusal carries its own
        # private/no-store header.
        return library_refusal_response(decision)

    # 3. CONTENT — unchanged readers, no truncation.
    if content_type == "regulation":
        data = await run_db(library_service.get_full_regulation, supabase, key)
    elif content_type == "article":
        reg_slug, article_slug = key.split("/", 1)
        data = await run_db(
            library_service.get_full_article, supabase, reg_slug, article_slug
        )
    elif content_type == "judgment":
        data = await run_db(library_service.get_full_judgment, supabase, key)
    elif content_type == "circular":
        data = await run_db(library_service.get_full_circular, supabase, key)
    else:  # "form"
        data = await run_db(library_service.get_full_form, supabase, key)

    if data is None:
        # Resolution succeeded but the corpus row vanished (or the form was
        # unpublished between the two reads) — 404, same Arabic message.
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail=_FULL_NOT_FOUND_AR[content_type],
            headers=_no_store,
        )

    # 4. Shelf the use — HERE, because the beacon no longer fires for a gated
    #    item (user decision 2026-07-28: viewing a gated page does nothing at
    #    all). The reveal is the first moment a gated item may enter مكتبتي, and
    #    it is an ungating action, so this is the only place that can record it.
    #    An OPEN item is shelved by the beacon instead, so nothing double-counts.
    if user_id:
        await _record_library_use(supabase, user_id, content_type, content_id)

    # 5. «المصادر الرسمية» — served ONLY here (user decision 2026-07-28). The
    #    anon doc payload emits an empty list for a gated item, so this is the
    #    one place the block reaches a reader. Keyed on the resolved content_id,
    #    not the slug, so it agrees with the ledger about which item this is.
    official_sources = await run_db(
        library_service.official_sources_for_item, supabase, content_type, content_id
    )

    response.headers["Cache-Control"] = _FULL_CACHE_CONTROL
    return LibraryFullResponse(
        content_type=content_type,
        key=key,
        official_sources=official_sources,
        **data,
    )
