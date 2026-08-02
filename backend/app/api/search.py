"""Shared BM25 navigation search — the two dedicated endpoints (Wave B).

Plan: ``.claude/plans/bm25_navigation_search.md`` §5.1 · D3 · D9 · §5.4
Migration: ``shared/db/migrations/111_bm25_search_index.sql``

    GET /api/v1/search        — cross-wing over the four PUBLIC corpora
                                (أنظمة · أحكام · تعاميم · خدمات). Backs the new
                                ``/library`` search page (D5).
    GET /api/v1/search/mine   — the caller's OWN material: مدوناتي, قوالبي, and
                                مكتبتي (their shelf).

BOTH REQUIRE AUTH (D9). ``Depends(get_current_user)``, not
``get_current_user_optional`` — an anonymous caller gets 401 here, and that is
the whole point: search is the one navigation surface an account buys. The public
HUBS behave differently on purpose (they drop ``q`` and serve the wing rather
than erroring), because a hub URL is a page a stranger may legitimately land on,
while this is an API a signed-in client calls.

⚠ THE PATH IS ``/api/v1/search``, NOT ``/api/v1/public/search``. The plan's §5.1
still says "public" — that line predates D9 in the same document and is a
contradiction, not an instruction: an endpoint that 401s anonymous callers has no
business under ``/public/``, where every other route is anon-by-design and the
prefix is load-bearing documentation.

NO SNIPPETS, NO GATE (D3 option 2). Nothing gated is indexed, so nothing gated
can come back. A hit is corpus + slug + title + facets; the card the frontend
renders keeps using the static free excerpt it already renders. See
``backend/app/models/search.py``.

METERING (§5.4). ``/search`` charges the per-user item budget exactly like a hub
page, through the same ``section:slug`` keys — so a document found by searching
and the same document found by browsing are ONE item, and a search is not a way
to buy extra corpus reach. There is no search exemption.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.models.search import SearchHit, SearchResponse
from backend.app.services import (
    case_service,
    library_budget_service as library_budget,
    library_items_service,
    search_service,
)
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["search"])

# Every byte here is per-caller (the results are metered against their budget,
# and /search/mine is literally their own content), so nothing may be shared- or
# ISR-cached. Same rule the shelf and the authed hub responses follow.
_PRIVATE_CACHE_CONTROL = "private, no-store"

# What ``/search/mine`` may be asked for. ``shelf`` is not a corpus — it is the
# caller's مكتبتي rows, which live in the PUBLIC corpora but are scoped to what
# they already opened or pinned (see ``library_items_service.search_shelf``).
_MINE_SCOPES = ("blog", "template", "shelf")


def _paging(page: int, page_size: int) -> tuple[int, int, int]:
    """``(page, page_size, offset)`` clamped to the module's bounds.

    Clamped rather than refused: a client asking for 500 results at once is a
    client with a stale constant, not an attacker worth a 400 — and the clamp is
    what actually bounds the yield.
    """
    page = max(1, int(page or 1))
    page_size = max(
        1, min(int(page_size or search_service.DEFAULT_PAGE_SIZE),
               search_service.MAX_PAGE_SIZE)
    )
    return page, page_size, (page - 1) * page_size


def _hit_models(hits: list[dict]) -> list[SearchHit]:
    return [
        SearchHit(
            corpus=h.get("corpus") or "",
            content_id=h.get("content_id") or "",
            slug=h.get("slug"),
            title=h.get("title") or "",
            facets=h.get("facets") or {},
            url=search_service.public_url(h.get("corpus") or "", h.get("slug")),
            score=float(h.get("score") or 0.0),
        )
        for h in hits
    ]


@router.get("/search", response_model=SearchResponse)
async def search_library(
    request: Request,
    response: Response,
    q: str = Query(..., description="search text (>= 3 chars)"),
    corpus: Optional[list[str]] = Query(
        None,
        description=(
            "repeatable wing filter: regulation | judgment | circular | service. "
            "Absent = all four."
        ),
    ),
    page: int = Query(1, description="1-based page index"),
    page_size: int = Query(
        search_service.DEFAULT_PAGE_SIZE,
        description=f"1..{search_service.MAX_PAGE_SIZE}",
    ),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Cross-wing search over the four public corpora — the ``/library`` page.

    Ranked by BM25 across all requested wings in ONE call, so «إجازة الأمومة»
    surfaces the نظام and the حكم that actually discuss it rather than making the
    reader pick a wing first. Only SLUGGED (published) items are indexed, so
    every hit has a real address — until the slug backfill completes (plan §2)
    that also means recall is bounded by what is published, not by the corpus.

    ``total`` is the count over the RPC's candidate set and ``total_is_exact``
    says whether that is the true total (see ``SearchResponse``).

    Yielded items are charged to the caller's library item budget, identically to
    a hub page (§5.4) — 429 past it, before any query runs.
    """
    response.headers["Cache-Control"] = _PRIVATE_CACHE_CONTROL

    query = search_service.require_query(q)
    corpora = search_service.clean_corpora(corpus, search_service.PUBLIC_CORPORA)
    page, page_size, offset = _paging(page, page_size)

    # The item budget is keyed on ``users.user_id`` (never ``auth_id``) — the id
    # space ``library_items`` / ``library_unlocks`` join on. A caller whose row
    # cannot be resolved is unmetered, exactly as on the hubs, and for the same
    # reason: refusing a search because a profile lookup hiccuped is the worse
    # failure of the two.
    user_id: Optional[str] = None
    try:
        user_id = await run_db(case_service.get_user_id, supabase, current_user.auth_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("Search: could not resolve user_id (%s) — unmetered", e)

    await library_budget.enforce_item_budget(request, user_id)

    if not corpora or offset >= search_service.MAX_RESULTS:
        # Past the result ceiling there is nothing to serve and nothing to
        # charge. Deep paging through search results is a traversal technique,
        # not a reading pattern (§5.4) — an empty page is the honest answer.
        return SearchResponse(
            query=query, corpora=list(corpora), page=page, page_size=page_size
        )

    limit = min(page_size, search_service.MAX_RESULTS - offset)
    result = await run_db(
        search_service.run_bm25,
        supabase,
        corpora=corpora,
        query=query,
        owner_user_id=None,
        limit=limit,
        offset=offset,
    )

    # Charge per WING, so the keys collide with the hub keys for the same
    # documents. ``item_keys`` skips slugless rows, which cannot happen here (the
    # index only holds slugged rows) but costs nothing to keep honest.
    for wing in corpora:
        section = search_service.CORPUS_SECTION.get(wing)
        if not section:
            continue
        rows = [{"slug": h.get("slug")} for h in result.hits if h.get("corpus") == wing]
        if rows:
            await library_budget.charge_items(
                request, user_id, library_budget.item_keys(section, rows),
                supabase=supabase,
            )

    return SearchResponse(
        items=_hit_models(result.hits),
        query=query,
        corpora=list(corpora),
        page=page,
        page_size=page_size,
        total=result.total,
        total_is_exact=result.total_is_exact,
    )


@router.get("/search/mine", response_model=SearchResponse)
async def search_mine(
    response: Response,
    q: str = Query(..., description="search text (>= 3 chars)"),
    scope: Optional[list[str]] = Query(
        None,
        description=(
            "repeatable: blog | template | shelf. Absent = all three. "
            "'shelf' searches مكتبتي (items the caller opened or pinned)."
        ),
    ),
    page: int = Query(1, description="1-based page index"),
    page_size: int = Query(
        search_service.DEFAULT_PAGE_SIZE,
        description=f"1..{search_service.MAX_PAGE_SIZE}",
    ),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Search the caller's own material: مدوناتي, قوالبي and مكتبتي.

    TWO RPC CALLS, MERGED HERE, and that is forced by the index rather than
    chosen: ``bm25_search`` matches ``owner_user_id IS NULL`` (public rows) or
    ``= p_owner`` (one owner's rows), never both — which is exactly what makes a
    private row structurally unable to fall out of a public search. So the
    owner-scoped corpora (blog, template) are one call and the shelf, whose rows
    are PUBLIC documents the caller happens to have on their shelf, is another.

    Merged by raw score. Cross-corpus scores are only approximately comparable
    (different IDF tables per corpus), which is acceptable for a "my stuff" list
    and is called out in ``SearchHit.score``.

    NOT METERED, unlike ``/search``. The item budget bounds CORPUS REACH; blogs
    and templates are the caller's own writing, and a shelf row is by definition
    a document they already reached (that is how it got on the shelf). Charging
    again would meter re-finding something you already own.
    """
    response.headers["Cache-Control"] = _PRIVATE_CACHE_CONTROL

    query = search_service.require_query(q)
    scopes = search_service.clean_corpora(scope, _MINE_SCOPES)
    page, page_size, offset = _paging(page, page_size)

    user_id = await run_db(case_service.get_user_id, supabase, current_user.auth_id)

    hits: list[dict] = []
    exact = True

    owned = [s for s in scopes if s in search_service.PRIVATE_CORPORA]
    if owned:
        # One call, owner-scoped. Fetched to the merge ceiling rather than to the
        # page: a merge cannot be paged at the source without over-fetching one
        # side and under-fetching the other.
        page_owned = await run_db(
            search_service.run_bm25,
            supabase,
            corpora=owned,
            query=query,
            owner_user_id=user_id,
            limit=search_service.MAX_RESULTS,
            offset=0,
        )
        hits.extend(page_owned.hits)
        exact = exact and page_owned.total_is_exact

    if "shelf" in scopes:
        shelf_hits, shelf_exact = await run_db(
            library_items_service.search_shelf, supabase, user_id, query
        )
        hits.extend(shelf_hits)
        exact = exact and shelf_exact

    hits.sort(key=lambda h: float(h.get("score") or 0.0), reverse=True)
    window = hits[offset : offset + page_size]

    return SearchResponse(
        items=_hit_models(window),
        query=query,
        corpora=list(scopes),
        page=page,
        page_size=page_size,
        # Post-merge this IS the count of what was assembled, so it is exact
        # unless one of the underlying calls hit the candidate ceiling.
        total=len(hits),
        total_is_exact=exact,
    )
