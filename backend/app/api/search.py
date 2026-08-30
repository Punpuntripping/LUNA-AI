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

NOT METERED (owner, 2026-08-30). ``/search`` used to charge the per-user item
budget through the same ``section:slug`` keys as a hub page, so that finding a
document by searching and finding it by browsing were ONE item. That is gone:
search is navigation, and a reader who has browsed to their hourly cap should
still be able to look something up. Nothing here debits the budget and nothing
here refuses on it.

⚠ WHAT THAT GIVES UP. Search now yields slug + title for up to
``MAX_RESULTS`` (200) rows per query with no per-user reach bound, so it is the
cheapest enumeration surface on the authed side. The remaining bounds are the
auth requirement, the ``_paging`` clamp, the middleware rate limiter, and the
unlock ledger on the bytes themselves — the item budget is no longer one of
them. Re-metering is a two-call restore — ``library_budget.enforce_item_budget``
before the query and ``library_budget.charge_items`` per wing after it, plus the
``user_id``/tier resolution both need. That code is DELETED rather than left
commented out, so the live path reads clean; ``git show 3e01ef2`` is the
reference implementation if it ever comes back.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.models.search import SearchHit, SearchResponse
from backend.app.services import (
    case_service,
    library_items_service,
    search_service,
)
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["search"])

# Every byte here is per-caller (both routes are auth-only, and /search/mine is
# literally their own content), so nothing may be shared- or ISR-cached. Same
# rule the shelf and the authed hub responses follow.
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

    NOT metered against the library item budget (owner, 2026-08-30): yielded
    items are neither charged nor refused on it, so a reader at their hourly hub
    cap can still search. ``current_user`` stays required — the route is
    auth-only — but nothing here reads the caller's tier any more.
    """
    response.headers["Cache-Control"] = _PRIVATE_CACHE_CONTROL

    query = search_service.require_query(q)
    corpora = search_service.clean_corpora(corpus, search_service.PUBLIC_CORPORA)
    page, page_size, offset = _paging(page, page_size)

    if not corpora or offset >= search_service.MAX_RESULTS:
        # Past the result ceiling there is nothing to serve. Deep paging through
        # search results is a traversal technique, not a reading pattern (§5.4) —
        # an empty page is the honest answer.
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

    NOT METERED — and since 2026-08-30 neither is ``/search``, so this is no
    longer the contrast it once was. The reason here is narrower and survives
    that change: blogs and templates are the caller's own writing, and a shelf
    row is by definition a document they already reached (that is how it got on
    the shelf), so this route would have nothing to charge even if the item
    budget still applied to search.
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
