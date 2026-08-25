"""Shared BM25 navigation search — the ONE ranking path (Wave B).

Plan: ``.claude/plans/bm25_navigation_search.md`` §5.1
Migration: ``shared/db/migrations/111_bm25_search_index.sql``

This module is a thin, honest wrapper around ONE Postgres function,
``public.bm25_search()``. Every navigation surface in the app — the four public
hubs, ``/library``, مكتبتي, مدوناتي, قوالبي — ranks through it. There is
deliberately no second ranking path, no per-surface scoring tweak and no
fallback to ``ILIKE``: two rankers means two behaviours to calibrate in Wave F
and two places for a leak to hide.

WHAT IS **NOT** HERE, AND MUST NOT BE ADDED
-------------------------------------------
**No access-tier logic of any kind.** No ``_find_unlock_row``, no ``seo_tier``
read, no ``resolve_gate``, no ``ts_headline``, no snippet. That is not an
oversight — it is D3 (option 2). ``search_index`` holds ONLY text the anonymous
card and doc page already publish (title + facets + the always-free lead), so a
search response is structurally incapable of revealing gated bytes. A leak stops
being a code path someone has to keep correct and becomes impossible. If you
find yourself reaching for a gate here, the gate belongs on the DOCUMENT
endpoint (where it already is), not on the index.

**No query-side stemming or normalization in Python.** ``bm25_search`` builds the
query vector with ``luna_tsvector`` — the same function that built the documents.
Anything we did to the string here (stripping harakat, folding hamza, dropping
stopwords) would be a second, drifting copy of the Arabic pipeline. We trim
whitespace and bound the length; the rest is SQL's job.

REGISTERED-ONLY (D9)
--------------------
Search requires an account. That is enforced at the ROUTE layer — ``/api/v1/search``
and ``/api/v1/search/mine`` both depend on ``get_current_user``, and the public
hubs drop ``q`` for an anonymous caller before it ever reaches
``library_service``. This module therefore assumes it is only ever called on
behalf of a real user and does no anon handling; do not "helpfully" add an anon
path here, because the caller's identity is the only thing standing between the
index and a crawler with a filter dimension.

All functions are SYNCHRONOUS (sync Supabase client) and are invoked from route
handlers via ``run_db`` / ``asyncio.to_thread`` — the same convention as
``library_service``. All user-facing messages are Arabic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException

logger = logging.getLogger(__name__)


# ==========================================================================
# VOCABULARY
# ==========================================================================

# The corpora the index carries (migration §4 CHECK constraint). Split by
# OWNERSHIP rather than by wing, because the split is what decides how
# ``bm25_search`` is called: ``p_owner IS NULL`` matches PUBLIC rows only, and a
# non-null ``p_owner`` matches THAT owner's rows only. The RPC cannot return both
# in one call — by design, so a private row can never fall out of a public
# search — which is why a mixed request is two calls, merged here.
#
# ⚠ ``compliance`` AND ``service`` ARE TWO DIFFERENT THINGS. Read this before
# touching either, because the names invite exactly the wrong guess:
#
#   · ``compliance`` IS the government-services NAVIGATION corpus (joined
#     2026-08-23, ``.claude/plans/compliance_entity_sections.md`` §6). It carries
#     the 533 SERVICE GUIDES — ريحان's own authored rewrite of each entity's
#     official PDF — keyed by ``service_guides.id``, with the Latin slugs the
#     /compliance wing publishes. Every hit resolves to a real, ungated,
#     fully-published page, which is precisely what the corpus's absence used to
#     make impossible.
#   · ``service`` is the INERT LEGACY ROW-SET: 100 rows keyed by ``services.id``
#     carrying the RETIRED wing's Arabic slugs. It left ``PUBLIC_CORPORA`` on
#     2026-08-03 because the wing it linked into was retired and every hit would
#     have resolved to a 404, and it stays out. It is NOT "the agents' corpus"
#     either — it is kept alive for ONE thing: ``manual_search.py`` maps its
#     ``services`` data_type onto it as rung ③, the exact-title pin behind
#     ``search_topics`` and a full-table ILIKE over all 4,746 services. Retiring
#     those rows is a separate decision (plan §10), not a side effect of this one.
#
# ``public_url`` gained a ``/compliance`` prefix in the same edit; a corpus and
# its prefix must always move together, or a ranked hit becomes an unlinkable one.
PUBLIC_CORPORA: tuple[str, ...] = (
    "regulation", "judgment", "circular", "compliance",
)
PRIVATE_CORPORA: tuple[str, ...] = ("blog", "template")
ALL_CORPORA: tuple[str, ...] = PUBLIC_CORPORA + PRIVATE_CORPORA

# corpus → the hub SECTION name used everywhere else in the backend (the item
# budget's key prefix, the sitemap map, the CTA-wall memo). Getting this wrong
# silently forks the item budget so a search hit and a browse hit on the SAME
# document count as two distinct items (§5.4 says they must be one).
CORPUS_SECTION: dict[str, str] = {
    "regulation": "regulations",
    "judgment": "judgments",
    "circular": "circulars",
    # ⚠ The corpus is ``compliance`` and so is the SECTION — the one place in this
    # map where the two words coincide, which makes it look like a typo waiting to
    # be "fixed". It is not. ``_charge_hub_yield`` keys the /compliance hub's item
    # budget on ``"compliance"``, so this entry is what makes a search hit and a
    # browse hit on the SAME guide charge ONE item. Rename it and the budget forks
    # silently: nothing errors, and a caller quietly gets twice the reach.
    "compliance": "compliance",
}

# corpus → public URL prefix. Same table as ``library_items_service._URL_PREFIX``
# for the overlapping types; blog/template are added because they are searchable
# and that module's shelf never carries them.
_URL_PREFIX: dict[str, str] = {
    "regulation": "/regulations",
    "judgment": "/judgments",
    "circular": "/circulars",
    "compliance": "/compliance",
    "blog": "/blog",
    "template": "/templates",
}
# ⚠ There is still NO prefix for ``service`` and there must not be one: those 100
# rows carry the retired wing's slugs and every URL built from them is a 404. The
# guides live at ``/compliance/{slug}`` and are reached through the ``compliance``
# corpus above.

# Filterable facet keys per corpus — the EXACT keys ``refresh_search_index()``
# writes into ``search_index.facets`` (migration §8). Anything outside this map
# is dropped rather than passed through: ``p_facets`` reaches a ``@>`` containment
# test on a jsonb column, so an unknown key cannot match a row — it can only make
# a query that returns nothing look like a broken search, and (as with the hub
# filter vocabularies) every accepted value is one more distinct request shape.
FACET_KEYS: dict[str, frozenset[str]] = {
    "regulation": frozenset(
        {"entity_name", "doc_type_bucket", "status_class", "reg_ref", "sectors"}
    ),
    "judgment": frozenset(
        {"court", "court_level", "city", "case_number", "legal_domains"}
    ),
    "circular": frozenset({"entity_ref", "doc_type", "circ_ref", "sectors"}),
    # The three keys ``refresh_search_index('compliance')`` writes (plan §6.1).
    # ``provider_name`` is the ENTITY axis expressed as a facet — the same closed
    # 28-value vocabulary ``shared/library/entities.py`` owns — so a cross-wing
    # search can be scoped to one issuing body without a second code path.
    "compliance": frozenset({"provider_name", "service_ref", "sectors"}),
    "blog": frozenset({"subtype", "display_mode", "is_public", "is_published"}),
    "template": frozenset({"created_by"}),
}


# ==========================================================================
# LIMITS
# ==========================================================================

# The public hub contract, unchanged (D8 + navigation-hardening §2.1): free text
# needs >= 3 characters. Two characters partition an Arabic corpus efficiently;
# three overlap heavily. ``public_library`` imports these rather than keeping its
# own copy, so the floor and the message have ONE definition.
MIN_QUERY_CHARS = 3
MSG_SEARCH_TOO_SHORT = "اكتب 3 أحرف على الأقل للبحث"
MSG_SEARCH_FAILED = "حدث خطأ أثناء البحث"

# Queries longer than this are TRUNCATED, not refused. Every extra word is
# another lexeme ANDed into the tsquery (see the RPC's ``tsq`` CTE), so a pasted
# paragraph is both expensive and guaranteed to match nothing; silently bounding
# it is kinder than a 400 on a paste.
MAX_QUERY_CHARS = 160

# Stage-1 candidate cut inside the RPC. The plan (§4.3) is explicit that this is
# a RECALL/LATENCY TRADE-OFF AND MUST BE LOGGED, not silent: a query matching
# 8,000 judgments is BM25-scored over the top 500 by ``ts_rank_cd``, so both the
# ranking and ``total_count`` are ceilings past this point. Wave F calibrates it.
DEFAULT_CANDIDATES = 500

# How many ranked ids a HUB search pulls before the wing's own filters and the
# 9-per-page window are applied in Python. Bounded on purpose, and the bound is
# also the enumeration bound (§5.4): a search is a filter dimension stacked on
# top of page depth, so "rank the top 200 and stop" caps how much of a wing one
# query can walk. 200 ≈ 22 hub pages, which is far past where anyone reformulates.
HUB_SEARCH_LIMIT = 200

# Paging bounds for the dedicated /search endpoints. The offset ceiling is the
# same argument as HUB_SEARCH_LIMIT: deep paging through search results is not a
# product need, it is a traversal technique.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
MAX_RESULTS = 200


# ==========================================================================
# VALIDATION
# ==========================================================================


def _reject(message: str) -> LunaHTTPException:
    """A 400 in the standard envelope, Arabic, never shared-cached (the header
    matters for the hub routes: a rejection is a property of the REQUEST, and one
    parked in the edge cache would be replayed to everyone asking for that
    filter)."""
    return LunaHTTPException(
        status_code=400,
        code=ErrorCode.VALIDATION_ERROR,
        detail=message,
        headers={"Cache-Control": "private, no-store"},
    )


def normalize_query(value: Optional[str]) -> Optional[str]:
    """Validate a search term: ``None`` (absent/blank), or >= 3 characters.

    Blank stays a NO-OP rather than an error — an unfiltered hub is the normal
    case and must not 400. Over-long is truncated (see ``MAX_QUERY_CHARS``).
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) < MIN_QUERY_CHARS:
        raise _reject(MSG_SEARCH_TOO_SHORT)
    return value[:MAX_QUERY_CHARS]


def require_query(value: Optional[str]) -> str:
    """``normalize_query`` for the dedicated /search endpoints, where an absent
    ``q`` is meaningless (a hub without ``q`` is a listing; a search without ``q``
    is nothing). Blank and too-short both refuse with the same Arabic message —
    the caller cannot tell them apart and does not need to."""
    q = normalize_query(value)
    if q is None:
        raise _reject(MSG_SEARCH_TOO_SHORT)
    return q


def clean_corpora(
    requested: Optional[Iterable[str]], allowed: Iterable[str]
) -> list[str]:
    """Intersect the caller's ``corpus`` list with what this endpoint allows.

    Empty/absent means "all of them". Unknown values are DROPPED rather than
    refused: a corpus name is a UI affordance, not a secret, and a stale frontend
    build asking for a wing that was renamed should degrade to searching the rest
    rather than 400ing a search box. Order follows ``allowed`` so the response is
    stable regardless of query-string order.
    """
    allowed_list = list(allowed)
    if not requested:
        return allowed_list
    wanted = {str(c).strip().lower() for c in requested if str(c).strip()}
    if not wanted:
        return allowed_list
    return [c for c in allowed_list if c in wanted]


def clean_facets(
    corpora: Iterable[str], facets: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Keep only facet keys that at least one of ``corpora`` actually indexes.

    A cross-wing search legitimately carries a key only some wings hold (e.g.
    ``court_level`` while أنظمة are in scope): the RPC applies ``facets @>
    p_facets`` uniformly, so such a key correctly excludes the wings that lack
    it. What is dropped here is a key NO wing in scope has, which could only
    return an empty set while looking like a working filter.
    """
    if not facets:
        return {}
    live: set[str] = set()
    for corpus in corpora:
        live |= FACET_KEYS.get(corpus, frozenset())
    return {k: v for k, v in facets.items() if k in live and v is not None}


def public_url(corpus: str, slug: Optional[str]) -> Optional[str]:
    """In-app path for a hit, or ``None`` when it has no published address.

    ``None`` is a normal answer, not a failure — an item whose slug the sidecar
    has not minted yet is unlinkable, and the caller must render no link rather
    than a guessed one (the rule ``library_items_service._public_page_url``
    already states for the shelf).
    """
    prefix = _URL_PREFIX.get(corpus)
    if not prefix or not slug:
        return None
    return f"{prefix}/{slug}"


# ==========================================================================
# THE RPC
# ==========================================================================


@dataclass(frozen=True)
class SearchPage:
    """One ``bm25_search`` answer.

    ``total`` is the RPC's ``total_count``, which counts the CANDIDATE set — so it
    is exact only when fewer than ``candidates`` documents matched. ``total_is_exact``
    carries that distinction to the wire instead of letting a ceiling masquerade
    as a total.
    """

    hits: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    total_is_exact: bool = True
    corpora: list[str] = field(default_factory=list)


def _search_error() -> LunaHTTPException:
    return LunaHTTPException(
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR,
        detail=MSG_SEARCH_FAILED,
    )


def run_bm25(
    supabase: SupabaseClient,
    *,
    corpora: Iterable[str],
    query: str,
    owner_user_id: Optional[str] = None,
    facets: Optional[dict[str, Any]] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    candidates: int = DEFAULT_CANDIDATES,
) -> SearchPage:
    """Call ``public.bm25_search()``. SYNC.

    ``owner_user_id`` is the ownership switch, and it is NOT a convenience: the
    RPC matches ``owner_user_id IS NULL`` when it is null and ``owner_user_id =
    p_owner`` otherwise, so one call is either wholly public or wholly one
    owner's. Never pass a private corpus with ``owner_user_id=None`` expecting a
    filter — you would get nothing, which is the safe direction, but it is the
    caller's job to split the call.

    Raises (Arabic 500) on an RPC failure rather than degrading to zero hits: a
    search box that answers «لا توجد نتائج» when the index is unreachable is
    indistinguishable from one that works, and the reader would rephrase forever.
    ⚠ That includes the case where migration 111 has not been applied yet — the
    backend must be deployed WITH the migration, not before it.
    """
    corpora = [c for c in corpora if c in ALL_CORPORA]
    if not corpora or not query:
        return SearchPage(corpora=list(corpora))

    limit = max(1, int(limit or DEFAULT_PAGE_SIZE))
    offset = max(0, int(offset or 0))
    candidates = max(limit + offset, int(candidates or DEFAULT_CANDIDATES))

    params = {
        "p_corpora": list(corpora),
        "p_query": query,
        "p_owner": str(owner_user_id) if owner_user_id else None,
        "p_facets": facets or {},
        "p_limit": limit,
        "p_offset": offset,
        "p_candidates": candidates,
    }

    try:
        res = supabase.rpc("bm25_search", params).execute()
    except Exception as e:  # noqa: BLE001
        logger.exception("bm25_search failed (corpora=%s): %s", corpora, e)
        raise _search_error()

    rows = res.data or []
    # ``total_count`` is a window function over the scored set, so every row
    # carries the same value — read it once instead of trusting the last row.
    total = int((rows[0].get("total_count") or 0)) if rows else 0
    hits: list[dict[str, Any]] = []
    for r in rows:
        hits.append(
            {
                "corpus": r.get("corpus"),
                "content_id": str(r.get("content_id") or ""),
                "slug": r.get("slug"),
                "title": (r.get("title") or "").strip(),
                "facets": r.get("facets") or {},
                "score": float(r.get("score") or 0.0),
            }
        )

    # The candidate cut MUST be visible (plan §4.3), both on the wire and in the
    # logs — this is the number Wave F calibrates against a real query set, and a
    # silently truncated ranking looks exactly like a ranking that is simply bad.
    exact = total < candidates
    if not exact:
        logger.info(
            "bm25_search hit the candidate ceiling (corpora=%s candidates=%s) — "
            "ranking and total are both capped",
            corpora,
            candidates,
        )

    return SearchPage(
        hits=hits, total=total, total_is_exact=exact, corpora=list(corpora)
    )


def corpus_search_ids(
    supabase: SupabaseClient,
    corpus: str,
    query: str,
    *,
    limit: int = HUB_SEARCH_LIMIT,
    owner_user_id: Optional[str] = None,
) -> list[str]:
    """Ranked ``content_id`` list for ONE corpus — the hub-filter entry point. SYNC.

    Returns ids in score order, best first, so the caller can fetch the corpus
    rows it already knows how to fetch and re-impose this order.

    ⚠ FACETS ARE DELIBERATELY NOT PUSHED INTO THE RPC HERE. A hub's own filters
    (``entity`` matching ``entity_id`` OR ``entity_ref``, a circular's
    ``entity`` resolved through an ``entities`` name lookup, ``sector``
    array-contains) are expressed against the CORPUS table and are not all
    representable in ``search_index.facets`` — ``entity_id`` is not even in it. So
    BM25 supplies the ordered candidate set and the wing's existing filter builder
    still runs, unchanged, on the corpus rows. One behaviour, not two.
    """
    page = run_bm25(
        supabase,
        corpora=[corpus],
        query=query,
        owner_user_id=owner_user_id,
        limit=max(1, min(int(limit or HUB_SEARCH_LIMIT), MAX_RESULTS)),
        offset=0,
    )
    out: list[str] = []
    seen: set[str] = set()
    for hit in page.hits:
        cid = hit.get("content_id")
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def rank_map(ids: Iterable[str]) -> dict[str, int]:
    """``{content_id: rank}`` for re-imposing BM25 order on rows fetched by id.

    The corpus fetch comes back in whatever order PostgREST felt like, and an
    ``IN`` list chunked at 150 comes back in several such orders — so the ranking
    has to be re-applied in Python or it is simply lost.
    """
    return {str(cid): i for i, cid in enumerate(ids)}


__all__ = [
    "ALL_CORPORA",
    "CORPUS_SECTION",
    "DEFAULT_CANDIDATES",
    "DEFAULT_PAGE_SIZE",
    "FACET_KEYS",
    "HUB_SEARCH_LIMIT",
    "MAX_PAGE_SIZE",
    "MAX_QUERY_CHARS",
    "MAX_RESULTS",
    "MIN_QUERY_CHARS",
    "MSG_SEARCH_FAILED",
    "MSG_SEARCH_TOO_SHORT",
    "PRIVATE_CORPORA",
    "PUBLIC_CORPORA",
    "SearchPage",
    "clean_corpora",
    "clean_facets",
    "corpus_search_ids",
    "normalize_query",
    "public_url",
    "rank_map",
    "require_query",
    "run_bm25",
]
