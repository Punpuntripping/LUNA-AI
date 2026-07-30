"""«مكتبتي» — the user's library shelf API (access-tiers Phase B2).

Plan: ``.claude/plans/access_tiers_gating.md`` PART 5B (§5B.1–§5B.5).
Decisions: ``.claude/plans/access_tiers_gating_DECISIONS.md`` D16 / D16.1 / D16.2.

    GET    /api/v1/library/mine        — the shelf, hub-shaped, paged
    POST   /api/v1/library/mine/use    — the authed use beacon (free/open items)
    POST   /api/v1/library/mine/save   — pin   («حفظ»)
    DELETE /api/v1/library/mine/save   — unpin

EVERY route here is ``Depends(get_current_user)`` and sets
``Cache-Control: private, no-store``. That is not decoration:

* D11 — per-user bytes must never reach a shared/ISR cache. A shelf is by
  definition per-user.
* §5B.3 ISR TRAP — ``use_count`` must never be incremented from a cached server
  render (the blog's view-count-on-read mistake). These endpoints are authed +
  no-store, so the counter rides the authed CLIENT call by construction.

WHAT THE SHELF IS ALLOWED TO SHOW
---------------------------------
Only §1.3 NEVER-GATED fields: titles, entity, dates, topic chips, slugs,
snippets of already-free lead text. No body text, ever. That is what makes §5B.4
safe — a downgraded user still SEES every item with a lock badge
(``is_frozen``), because listing metadata leaks nothing, and «لديك {n} مصدراً
محفوظاً في مكتبتك» is only persuasive if the user can see the shelf.

``library_unlocks`` is MONEY. The READ paths here never touch it beyond a
SELECT — ``resolve_access`` must never be called to render a list, because that
would charge for scrolling.

The ONE write path is ``POST /library/mine/save``. As of 2026-07-28 «حفظ» is an
UNGATING action by user decision — **everything in مكتبتي is ungated** — so it
runs ``resolve_access`` exactly like a reveal and is charged once, permanently.
Saving something you cannot unlock is refused with the D14 402 body rather than
shelved as a locked row. The only lock badge left in the shelf is the §5B.4
freeze (a paid-era unlock on a lapsed plan), which the subscription caused, not
the save.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Response, status
from pydantic import BaseModel, Field
from supabase import Client as SupabaseClient

from backend.app.deps import get_current_user, get_supabase
from backend.app.errors import (
    ErrorCode,
    LunaHTTPException,
    library_refusal_response,
)
from backend.app.services import (
    case_service,
    library_items_service,
    library_service,
)
from shared.auth.jwt import AuthUser
from shared.db.run import run_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["library-mine"])

# Per-user content — never shared, never ISR-cached (D11 + §5B.3).
_PRIVATE_CACHE_CONTROL = "private, no-store"


# ============================================
# REQUEST / RESPONSE MODELS
# ============================================


class LibraryItemRef(BaseModel):
    """Which item a write endpoint is about — BY ID OR BY SLUG.

    ``content_id`` is the canonical key, the SAME one ``seo_item_meta`` and
    ``library_unlocks`` use: the corpus uuid for
    regulation/judgment/circular/service/form, and ``'{regulation_id}#{article_no}'``
    for a مادة. Rows returned by ``GET /library/mine`` always carry it, so a
    save/unsave from مكتبتي passes it straight back.

    A caller coming from a PUBLIC page has no id — no doc-page payload exposes a
    corpus uuid — so ``slug`` is accepted instead and resolved server-side.
    A مادة additionally needs ``parent_slug`` (its نظام's slug), because a مادة
    slug like «المادة-74» repeats across statutes.

    Exactly one of ``content_id`` / ``slug`` is required.

    ⚠ EVERY FIELD IS ATTACKER-CONTROLLED. These endpoints write straight into the
    shared ``library_items`` table, so the bounds below are load-bearing: without
    them any authed account could write unlimited junk rows (arbitrary
    ``content_type`` values, ``content_id`` as large as the body limit allows, at
    the 60/min ceiling ≈ 86k rows/day), which is storage exhaustion, index bloat,
    and corruption of the «الأكثر استخداماً» ranking signal §5B.3 says will later
    weight reference ordering. ``content_type`` is additionally validated against
    ``SHELF_CONTENT_TYPES`` in ``_require_ref``, and the DB carries its own CHECK
    constraints — three layers, because this is a write path with no gate in front
    of it.

    The length caps also close a latent PostgREST filter-injection primitive:
    these values reach ``.in_("content_id", …)``, which renders as ``in.(a,b,c)``,
    so a value containing a comma would split into two list members.
    """

    content_type: str = Field(max_length=32)
    content_id: Optional[str] = Field(default=None, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=400)
    parent_slug: Optional[str] = Field(default=None, max_length=400)


class MyLibraryArticle(BaseModel):
    """A مادة nested under its parent نظام (§5B.1 — never a top-level tab).

    Card fields are the مادة's label + the nested public URL; there is no body
    text here (never-gated class only)."""

    content_type: str = "article"
    content_id: str
    slug: Optional[str] = None
    url: Optional[str] = None
    title: str = ""
    article_no: Optional[int] = None
    article_label: Optional[str] = None
    reg_slug: Optional[str] = None

    source: Optional[str] = None
    use_count: int = 0
    first_used_at: Optional[str] = None
    last_used_at: Optional[str] = None
    saved_at: Optional[str] = None
    was_unlocked: bool = False
    is_frozen: bool = False
    is_shelf_row: bool = True
    is_available: bool = False


class MyLibraryItem(BaseModel):
    """One row of «مكتبتي» — a hub card plus its shelf state.

    The card fields are the UNION of the public hub item models (``RegHubItem``,
    ``JudgmentHubItem``, ``CircularHubItem``, ``ComplianceHubItem``,
    ``FormHubItem``); only the ones belonging to ``content_type`` are populated,
    with the SAME names the hubs use, so the existing card components drop
    straight in (§5B.5).

    Shelf state:
      * ``source``        — ``'auto'`` (opened) | ``'manual'`` (pinned) | null on
        a synthesized نظام header.
      * ``use_count`` / ``last_used_at`` — the §5B.3 ranking signals. Vocabulary
        is **usage** («استخدام»), never «فتح».
      * ``was_unlocked``  — a ``library_unlocks`` row exists for this user+item
        (a مادة also counts its parent نظام's row — D5).
      * ``is_frozen``     — that row exists BUT the §1.2 predicate now fails
        (the caller is on free and the row was charged to another period). The
        item is still LISTED: §5B.4 forbids hiding a frozen shelf.
      * ``is_shelf_row``  — False for a نظام synthesized purely to hold مواد the
        user saved without the statute itself.
      * ``is_available``  — a public URL could be resolved (an unpublished or
        de-slugged item still lists, it just cannot be linked).
      * ``group_use_count`` / ``group_last_used_at`` — self + nested مواد; this
        is what the ordering uses for a نظام group.
    """

    content_type: str
    content_id: str
    slug: Optional[str] = None
    url: Optional[str] = None
    title: str = ""

    # regulation
    entity_name: Optional[str] = None
    status: Optional[str] = None
    doc_type: Optional[str] = None
    summary_snippet: Optional[str] = None
    sectors: Optional[list[str]] = None
    # judgment
    court: Optional[str] = None
    court_level: Optional[str] = None
    court_level_label: Optional[str] = None
    city: Optional[str] = None
    date_hijri: Optional[str] = None
    date_gregorian: Optional[str] = None
    domains: Optional[list[str]] = None
    snippet: Optional[str] = None
    # circular
    source_label: Optional[str] = None
    body_snippet: Optional[str] = None
    body_length: Optional[int] = None
    # service (compliance)
    provider_name: Optional[str] = None
    is_most_used: Optional[bool] = None
    intro_snippet: Optional[str] = None
    # form
    category: Optional[str] = None
    use_case_snippet: Optional[str] = None
    # article (only when a مادة could not be nested under a نظام)
    article_no: Optional[int] = None
    article_label: Optional[str] = None
    reg_slug: Optional[str] = None

    # shelf state
    source: Optional[str] = None
    use_count: int = 0
    first_used_at: Optional[str] = None
    last_used_at: Optional[str] = None
    saved_at: Optional[str] = None
    was_unlocked: bool = False
    is_frozen: bool = False
    is_shelf_row: bool = True
    is_available: bool = False
    group_use_count: int = 0
    group_last_used_at: Optional[str] = None

    child_articles: list[MyLibraryArticle] = Field(default_factory=list)


class MyLibraryResponse(BaseModel):
    """A page of «مكتبتي» — the same envelope shape as the public hubs, plus the
    shelf totals the §5B.4 upgrade CTA needs.

    ``counts`` is the WHOLE shelf per content_type (tab visibility: ``form`` and
    ``calculator`` are secondary tabs shown only when non-empty; ``article`` is
    counted but is never a tab — مواد nest under their نظام).
    ``stored_library_count`` is the user's total ``library_unlocks`` ROW count —
    the «لديك {n} مصدراً محفوظاً في مكتبتك» number. ``frozen_count`` is how many
    of those the §1.2 predicate currently fails (0 for a paid caller).
    """

    items: list[MyLibraryItem] = Field(default_factory=list)
    page: int
    page_size: int
    total: int
    total_pages: int
    content_type: Optional[str] = None
    sort: str = "recent"
    counts: dict[str, int] = Field(default_factory=dict)
    stored_library_count: int = 0
    frozen_count: int = 0
    is_paid: bool = False


# ============================================
# HELPERS
# ============================================


async def _user_id(supabase: SupabaseClient, current_user: AuthUser) -> str:
    """``AuthUser.auth_id`` → ``users.user_id`` (D16.1 — never an auth_id)."""
    return await run_db(case_service.get_user_id, supabase, current_user.auth_id)


async def _resolve_ref(
    supabase: SupabaseClient,
    body: Optional[LibraryItemRef],
    *,
    content_type: Optional[str] = None,
    content_id: Optional[str] = None,
    slug: Optional[str] = None,
    parent_slug: Optional[str] = None,
    required: bool = True,
) -> tuple[str, Optional[str]]:
    """``(content_type, content_id)`` from the body or the query string.

    The JSON body is the contract; query params are accepted as a fallback
    because a request body on ``DELETE`` is awkward in some HTTP clients. A
    ``slug`` is resolved to the canonical id (see
    ``library_items_service.resolve_content_id``) so the shelf stays in the same
    id space as the ledger and the sidecar.

    400 (Arabic) when no item is identified at all. When the slug does not
    resolve, ``content_id`` comes back ``None`` — a 404 for an explicit save,
    a silent skip for the fire-and-forget beacon (``required=False``).
    """
    ct = (body.content_type if body else None) or content_type
    raw_id = (body.content_id if body else None) or content_id
    raw_slug = (body.slug if body else None) or slug
    raw_parent = (body.parent_slug if body else None) or parent_slug

    if not ct or not (raw_id or raw_slug):
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="نوع المحتوى ومعرّفه مطلوبان",
        )

    # ⚠ VALIDATE HERE, not per-endpoint. This helper is the single funnel for
    # /use, /save and /unsave, and all three write to the shared `library_items`
    # table with no gate in front of them. `/use` in particular used to accept
    # whatever `content_type` string it was handed, so an authed account could
    # write unlimited junk rows — storage exhaustion, index bloat, and corruption
    # of the «الأكثر استخداماً» ranking signal.
    if ct not in library_items_service.SHELF_CONTENT_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="نوع المحتوى غير مدعوم",
        )

    # These strings reach PostgREST `.in_(...)` filters, which render as
    # `in.(a,b,c)` — a comma would split one value into two list members. Not
    # exploitable today (every such query is user-scoped first), but the
    # primitive should not exist.
    for value in (raw_id, raw_slug, raw_parent):
        if value and any(c in value for c in ',()"'):
            raise LunaHTTPException(
                status_code=400,
                code=ErrorCode.VALIDATION_ERROR,
                detail="معرّف المحتوى غير صالح",
            )

    resolved = await library_items_service.resolve_content_id(
        supabase, ct, content_id=raw_id, slug=raw_slug, parent_slug=raw_parent
    )
    if resolved is None and required:
        raise LunaHTTPException(
            status_code=404,
            code=ErrorCode.VALIDATION_ERROR,
            detail="المصدر غير موجود",
        )
    return ct, resolved


# ============================================
# ENDPOINTS
# ============================================


@router.get("/library/mine", response_model=MyLibraryResponse)
async def list_my_library(
    response: Response,
    content_type: Optional[str] = Query(
        None,
        description=(
            "regulation | judgment | circular | service | form | calculator. "
            "'article' is normalized to 'regulation' — مواد nest under their "
            "نظام and are never a top-level tab (§5B.1)."
        ),
    ),
    sort: str = Query("recent", description="recent | most_used | saved"),
    page: int = Query(1, description="1-based page index."),
    page_size: int = Query(
        library_items_service.DEFAULT_PAGE_SIZE,
        description=f"1..{library_items_service.MAX_PAGE_SIZE}",
    ),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """The caller's shelf — every item they opened or pinned.

    Hub-shaped so the existing card components render it unchanged. Rows are
    NEVER filtered by entitlement: a downgraded caller still sees the whole
    shelf, with ``is_frozen=true`` on the paid-era rows (§5B.4 — "a frozen
    library rendered as an empty page is a worse product AND a worse conversion
    surface"). Everything returned is never-gated metadata (§1.3).

    Read-only: listing the shelf never bumps ``use_count`` and never touches
    ``library_unlocks``.
    """
    response.headers["Cache-Control"] = _PRIVATE_CACHE_CONTROL

    ct = library_items_service.normalize_content_type(content_type)
    sort_key = library_items_service.normalize_sort(sort)

    user_id = await _user_id(supabase, current_user)
    data = await library_items_service.list_items(
        supabase,
        user_id,
        content_type=ct,
        sort=sort_key,
        page=page,
        page_size=page_size,
    )
    return MyLibraryResponse(**data)


@router.post("/library/mine/use", status_code=status.HTTP_204_NO_CONTENT)
async def record_library_use(
    body: LibraryItemRef,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Record ONE use of a library item — the authed مكتبتي beacon (§5B.5).

    ⚠ ONE USE MUST COUNT EXACTLY ONCE (D16.2, **REVISED 2026-07-27**).
    The page view IS the use, so ``LibraryUseBeacon`` fires this for **gated and
    open items alike** — §5B.2 shelves an item when it is opened, "gated or not".
    Correspondingly ``/library/full`` does **NOT** write to the shelf.

    An earlier design had the beacon skip gated items and let the reveal record
    them; it was wrong twice over. A gated page whose summary the reader read but
    never revealed was never shelved at all, and once both fired, every gated
    reveal counted TWO uses against one for an open item — biasing
    «الأكثر استخداماً» toward gated content, which is the exact signal §5B.3 says
    should later weight reference ordering.

    The workspace reference-source endpoint still records its own use, because no
    document page (and therefore no beacon) is involved there.

    Nothing is enforced server-side: a per-beacon gate resolution would be needed
    to refuse anything, and dropping rows would lose pages the user really opened.
    The single-call rule is a client contract, stated here, in
    ``library_items_service.record_use`` and in ``LibraryUseBeacon``.

    Always 204 — a shelf-write failure (or a slug that no longer resolves) must
    never break the caller's read.
    """
    ct, cid = await _resolve_ref(supabase, body, required=False)
    user_id = await _user_id(supabase, current_user)
    if cid:
        # Never raises (D16.2) — logged and swallowed inside the service.
        await library_items_service.record_use(supabase, user_id, ct, cid)
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Cache-Control": _PRIVATE_CACHE_CONTROL},
    )


@router.post("/library/mine/save", status_code=status.HTTP_204_NO_CONTENT)
async def save_library_item(
    body: LibraryItemRef,
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Pin an item to مكتبتي («حفظ») — AN UNGATING ACTION.

    ⚠ REVERSED 2026-07-28 by user decision. §5B.2 originally made «حفظ» free at
    every tier ("it stores a POINTER, never content") and allowed saving a gated
    item you had not unlocked, which then listed LOCKED in مكتبتي as an intent
    signal. That is no longer the model:

        **EVERYTHING IN مكتبتي IS UNGATED.** Save, preview («عرض المصدر») and
        full-open («اعرض النص كاملاً») are all ungating actions.

    So a save runs the SAME entitlement path as a reveal and is charged the same
    once — permanently, per §1.2 — after which the item is readable everywhere.
    A refused save returns the D14 402 body, exactly like a refused reveal, so
    the frontend renders the identical quota/upgrade card.

    The one remaining lock badge in مكتبتي is the §5B.4 FREEZE — a paid-era
    unlock on a lapsed plan. That is caused by the subscription ending, not by
    shelving something unreadable, so it does not violate the rule above.

    Idempotent, and it never clobbers an existing row's counters: an explicit
    save on a ``source='auto'`` row only flips ``source``/``saved_at`` while
    ``use_count`` and ``first_used_at`` are preserved.

    404 «المصدر غير موجود» when a ``slug`` cannot be resolved (an explicit user
    action must fail loudly, unlike the beacon).
    """
    ct, cid = await _resolve_ref(supabase, body)
    user_id = await _user_id(supabase, current_user)

    # Ungate first — a save that cannot unlock must not shelve an unreadable row.
    decision = await library_service.resolve_access(
        supabase, user_id, ct, cid, surface="library"
    )
    if not decision.may_unlock:
        return library_refusal_response(decision)

    await library_items_service.save_item(supabase, user_id, ct, cid)
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Cache-Control": _PRIVATE_CACHE_CONTROL},
    )


@router.delete("/library/mine/save", status_code=status.HTTP_204_NO_CONTENT)
async def unsave_library_item(
    body: Optional[LibraryItemRef] = Body(None),
    content_type: Optional[str] = Query(None),
    content_id: Optional[str] = Query(None),
    slug: Optional[str] = Query(None),
    parent_slug: Optional[str] = Query(None),
    current_user: AuthUser = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_supabase),
):
    """Unpin an item («إزالة الحفظ»). Idempotent — unpinning something that is
    not on the shelf is a no-op, not a 404.

    Unpin is not "erase my history": a row the caller actually USED keeps its
    counters and simply reverts to ``source='auto'`` (it is still something they
    opened, and «الأكثر استخداماً» must not lose it). A row that existed only
    because of the pin is removed.

    Accepts the reference in the JSON body (the contract) or as query params,
    since a request body on ``DELETE`` is awkward in some clients. ``slug`` works
    here too — an unresolvable one is a no-op 204, matching the idempotency rule.
    """
    ct, cid = await _resolve_ref(
        supabase,
        body,
        content_type=content_type,
        content_id=content_id,
        slug=slug,
        parent_slug=parent_slug,
        required=False,
    )
    user_id = await _user_id(supabase, current_user)
    if cid:
        await library_items_service.unsave_item(supabase, user_id, ct, cid)
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Cache-Control": _PRIVATE_CACHE_CONTROL},
    )
