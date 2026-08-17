"""«مكتبتي» — the user's library shelf (access-tiers Phase B2).

Plan: ``.claude/plans/access_tiers_gating.md`` PART 5B (§5B.1–§5B.5).
Decisions: ``.claude/plans/access_tiers_gating_DECISIONS.md`` D16 / D16.1 / D16.2.
Table: ``shared/db/migrations/106_library_items.sql``.

TWO TABLES, TWO JOBS — DO NOT MERGE THEM (D16)
----------------------------------------------
``library_unlocks`` (migration 104) is **MONEY**: inserted once via ON CONFLICT
DO NOTHING, never updated, and it is what every quota count and cost audit
measures. NOTHING in this module writes to it — it is read-only here, and only
to answer "did this user ever unlock this item?".

``library_items`` (migration 106) is **BEHAVIOUR**: upserted on every use, with
``use_count`` incremented. A page view must never touch the cost ledger, which
is exactly why this second table exists.

EVERYTHING ON THE SHELF IS UNGATED (user decision 2026-07-28)
------------------------------------------------------------
This REVERSES §5B.2's original "opening an item shelves it, gated or not" and
its "explicit saving is free at every tier and grants no access". The rule now:

* viewing a **gated** page  → nothing. Not shelved, not charged. That is what
  keeps the free summary layer free (§5.1) — skimming ten judgment summaries
  must not cost ten unlocks.
* viewing an **open** item  → shelved free (``source='auto'``). Services are
  policy-never-gated, which is precisely why the الخدمات tab fills.
* «اعرض النص كاملاً» · «عرض المصدر» · «حفظ» → each UNLOCKS and shelves. A save
  that cannot unlock is refused (402), never shelved as a locked row.

So every row here is readable by construction. The one lock badge left in مكتبتي
is the §5B.4 FREEZE — a paid-era unlock on a lapsed plan — which the subscription
ending caused, not the shelving.

VOCABULARY (§5B.3) — the user-facing concept is **usage** («استخدام»), never
«فتح». The naming matches end to end: ``use_count``, ``last_used_at``,
``sort=most_used``. There is deliberately no translation layer between the label
and the column; do not introduce one.

All functions that touch Supabase are SYNCHRONOUS (``_``-prefixed) and are
invoked from the async wrappers via ``run_db``/``asyncio.to_thread``, the same
convention as ``library_service`` and ``blog_service``.

``user_id`` everywhere is a **``users.user_id``**, never an ``auth_id`` (D16.1);
routes map it with ``case_service.get_user_id``.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import library_service as ls
from backend.app.services import search_service
from shared import quota as _quota
from shared.db.run import run_db
from shared.seo.judgment_naming import court_level_label, judgment_display_title

logger = logging.getLogger(__name__)

__all__ = [
    "SHELF_CONTENT_TYPES",
    "SORTS",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "MAX_SHELF_SCAN",
    "normalize_content_type",
    "normalize_sort",
    "resolve_content_id",
    "public_page_url",
    "public_page_urls_for_reference_rows",
    "record_use",
    "save_item",
    "unsave_item",
    "list_items",
    "search_shelf",
    "shelf_rank",
]

# Everything that can land on the shelf. ``calculator`` has no corpus table yet
# (the calculators wing is hand-built pages), so it hydrates to a bare row — it
# is listed here so a save never 400s on a type the product already ships.
SHELF_CONTENT_TYPES = (
    "regulation",
    "article",
    "judgment",
    "circular",
    "service",
    "form",
    "calculator",
)

# §5B.5 sorts. 'recent' = last_used_at DESC NULLS LAST (the default),
# 'most_used' = use_count DESC («الأكثر استخداماً»), 'saved' = saved_at DESC.
SORTS = ("recent", "most_used", "saved")

DEFAULT_PAGE_SIZE = 12
MAX_PAGE_SIZE = 50

# Upper bound on the rows one listing request reads. The shelf is a per-user
# table (indexed on user_id) and is bounded in practice by the unlock allowance,
# but مكتبتي assembles its page in PYTHON — مواد must nest under a parent نظام
# that may itself be anywhere in the shelf (§5B.1), which no single PostgREST
# ORDER/RANGE can express. The scan is chunked at the PostgREST 1,000-row clamp.
MAX_SHELF_SCAN = 2000
_SCAN_CHUNK = 1000

# The one column set this module reads off library_items.
_ITEM_COLS = (
    "item_row_id, content_type, content_id, source, use_count, "
    "first_used_at, last_used_at, saved_at"
)

# Public page path per content_type. مادة is nested under its نظام
# ('/regulations/{reg}/{article}') and so is built separately.
#
# ⚠ ``service`` HAS NO ENTRY, deliberately (2026-08-03). The compliance wing was
# retired, so a shelved government service has no page in our library: it keeps
# its title on the shelf and renders unlinked (`ShelfCard`'s plain fallback), and
# the chat panel drops its «فتح الخدمة في ريحان» button because `_url_for`
# returns None. Re-adding the key without rebuilding the pages ships 404s.
_URL_PREFIX = {
    "regulation": "/regulations",
    "judgment": "/judgments",
    "circular": "/circulars",
    "form": "/forms",
}


# ==========================================================================
# Validation helpers (Arabic errors)
# ==========================================================================


def normalize_content_type(value: Optional[str]) -> Optional[str]:
    """Validate a ``content_type`` filter/body value, or raise 400 (Arabic).

    ``None``/empty means "every type" for the listing endpoint.

    §5B.1: **مواد are never a top-level tab** — "a مادة without its statute reads
    as an orphan" — so a request for ``article`` is normalized to ``regulation``
    and answered with the نظام view the مواد nest inside.
    """
    v = (value or "").strip()
    if not v:
        return None
    if v not in SHELF_CONTENT_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="نوع المحتوى غير صالح",
        )
    if v == "article":
        return "regulation"
    return v


def normalize_sort(value: Optional[str]) -> str:
    """Validate the ``sort`` query value, or raise 400 (Arabic)."""
    v = (value or "").strip() or "recent"
    if v not in SORTS:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="ترتيب غير صالح",
        )
    return v


def _require_ref(content_type: str, content_id: str) -> tuple[str, str]:
    """Validate a ``(content_type, content_id)`` pair for a write. 400 (Arabic)."""
    ct = (content_type or "").strip()
    cid = str(content_id or "").strip()
    if ct not in SHELF_CONTENT_TYPES:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="نوع المحتوى غير صالح",
        )
    if not cid:
        raise LunaHTTPException(
            status_code=400,
            code=ErrorCode.VALIDATION_ERROR,
            detail="معرّف المحتوى مطلوب",
        )
    return ct, cid


def _sidecar_content_id(
    supabase: SupabaseClient, content_type: str, slug: str
) -> Optional[str]:
    """``seo_item_meta`` slug → ``content_id`` for one wing. SYNC, read-only."""
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
        logger.warning("library_items: sidecar lookup failed (%s): %s", slug, e)
        return None
    rows = res.data or []
    cid = rows[0].get("content_id") if rows else None
    return str(cid) if cid else None


def _resolve_content_id(
    supabase: SupabaseClient,
    content_type: str,
    slug: str,
    parent_slug: Optional[str] = None,
) -> Optional[str]:
    """Public page slug → the canonical ``content_id``. SYNC, read-only.

    The shelf MUST be keyed on the same id space as ``seo_item_meta`` and
    ``library_unlocks`` (that is what makes ``was_unlocked``/``is_frozen`` join
    at all), but the public page payloads only ever carry SLUGS — no doc-page
    response exposes a corpus uuid. So the client sends what it has and the
    resolution happens here, mirroring
    ``public_library._resolve_full_target`` per wing:

      * ``article``  — needs BOTH slugs: a مادة slug («المادة-74») repeats across
        statutes, so it is resolved through its parent نظام →
        ``'{regulation_id}#{article_no}'``.
      * ``form``     — forms have NO sidecar row; the slug lives on the forms
        table and the id is the gate key. The liability gate
        (``approved`` + ``is_published``) is applied here too, so a draft form
        can never be shelved by guessing its slug.
      * everything else — the ``seo_item_meta`` sidecar.

    Returns ``None`` when the slug does not resolve (caller decides: a 404 for
    an explicit save, a silent skip for the fire-and-forget beacon).
    """
    slug = (slug or "").strip()
    if not slug:
        return None

    if content_type == "article":
        parent_slug = (parent_slug or "").strip()
        if not parent_slug:
            return None
        reg_id = _sidecar_content_id(supabase, "regulation", parent_slug)
        if not reg_id:
            return None
        try:
            res = (
                supabase.table("seo_articles")
                .select("article_no")
                .eq("regulation_id", str(reg_id))
                .eq("slug", slug)
                .limit(1)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("library_items: article slug lookup failed: %s", e)
            return None
        rows = res.data or []
        if not rows:
            return None
        return f"{reg_id}#{int(rows[0].get('article_no') or 0)}"

    if content_type == "form":
        try:
            res = (
                supabase.table("forms")
                .select("id")
                .eq("slug", slug)
                .eq("review_status", "approved")
                .eq("is_published", True)
                .limit(1)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("library_items: form slug lookup failed: %s", e)
            return None
        rows = res.data or []
        return str(rows[0]["id"]) if rows and rows[0].get("id") else None

    return _sidecar_content_id(supabase, content_type, slug)


async def resolve_content_id(
    supabase: SupabaseClient,
    content_type: str,
    *,
    content_id: Optional[str] = None,
    slug: Optional[str] = None,
    parent_slug: Optional[str] = None,
) -> Optional[str]:
    """The item key for a write request: an explicit ``content_id`` wins,
    otherwise the slug is resolved through ``_resolve_content_id``. ``None`` when
    neither yields a key."""
    if content_id and str(content_id).strip():
        return str(content_id).strip()
    if not slug:
        return None
    return await run_db(
        _resolve_content_id, supabase, content_type, slug, parent_slug
    )


def _now_iso() -> str:
    """``now()`` as an ISO-8601 UTC string.

    PostgREST cannot send a SQL ``now()`` through an UPDATE/INSERT body, so the
    timestamp is stamped by the app. These are behavioural counters (ranking
    only, never money and never a period boundary — those are derived in SQL per
    D8), so app-clock skew is harmless here.
    """
    return datetime.now(timezone.utc).isoformat()


# ==========================================================================
# WRITES — record_use / save / unsave
# ==========================================================================


def _find_row(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> Optional[dict[str, Any]]:
    """The user's shelf row for one item, or ``None``. SYNC, read-only."""
    res = (
        supabase.table("library_items")
        .select(_ITEM_COLS)
        .eq("user_id", str(user_id))
        .eq("content_type", str(content_type))
        .eq("content_id", str(content_id))
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def _insert_row(
    supabase: SupabaseClient,
    user_id: str,
    content_type: str,
    content_id: str,
    body: dict[str, Any],
) -> bool:
    """Insert a shelf row, ignoring a concurrent duplicate. Returns True when
    THIS call inserted it (PostgREST returns zero rows for an ignored conflict —
    the same mechanism ``library_service._insert_unlock`` relies on). SYNC."""
    res = (
        supabase.table("library_items")
        .upsert(
            {
                "user_id": str(user_id),
                "content_type": str(content_type),
                "content_id": str(content_id),
                **body,
            },
            on_conflict="user_id,content_type,content_id",
            ignore_duplicates=True,
        )
        .execute()
    )
    return bool(res.data)


def _record_use(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> None:
    """Upsert the shelf row and increment ``use_count`` by exactly one. SYNC.

    Expresses the §5B.2 upsert::

        INSERT INTO library_items (user_id, content_type, content_id, use_count,
                                   first_used_at, last_used_at)
        VALUES (..., 1, now(), now())
        ON CONFLICT (user_id, content_type, content_id) DO UPDATE
        SET use_count = library_items.use_count + 1, last_used_at = now();

    PostgREST cannot send ``SET use_count = use_count + 1``, so migration 107
    ships that statement as the ``record_library_item_use`` RPC and this function
    calls it — one atomic round-trip, no lost updates.

    The read-modify-write below is a FALLBACK, kept only so a missing/failing RPC
    degrades to an approximate counter instead of silently losing the shelf row
    entirely (``record_use`` swallows exceptions, so a hard failure here would be
    invisible). It can lose an increment under a simultaneous double-click; that
    costs one count on a ranking signal and is never money — money is
    ``library_unlocks``, which is insert-once and untouched by this module.

    An existing ``source='manual'`` pin is NOT downgraded to ``'auto'`` by either
    path: this writes counters only.
    """
    try:
        supabase.rpc(
            "record_library_item_use",
            {
                "p_user_id": user_id,
                "p_content_type": content_type,
                "p_content_id": content_id,
            },
        ).execute()
        return
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "library_items: record_library_item_use RPC failed (%s/%s): %s — "
            "falling back to read-modify-write",
            content_type,
            content_id,
            e,
        )

    now = _now_iso()
    row = _find_row(supabase, user_id, content_type, content_id)

    if row is None:
        inserted = _insert_row(
            supabase,
            user_id,
            content_type,
            content_id,
            {
                "source": "auto",
                "use_count": 1,
                "first_used_at": now,
                "last_used_at": now,
            },
        )
        if inserted:
            return
        # Lost the insert race — re-read and fall through to the update branch.
        row = _find_row(supabase, user_id, content_type, content_id)
        if row is None:
            return

    (
        supabase.table("library_items")
        .update(
            {
                "use_count": int(row.get("use_count") or 0) + 1,
                "last_used_at": now,
                "first_used_at": row.get("first_used_at") or now,
            }
        )
        .eq("item_row_id", row["item_row_id"])
        .execute()
    )


async def record_use(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> None:
    """Record ONE use of a library item on the caller's مكتبتي shelf (D16.2).

    Upserts the shelf row and increments ``use_count``. Idempotent per call but
    NOT deduped — **each call is one use**.

    NEVER RAISES to the caller. A shelf-write failure must not break a content
    read: the shelf is a ranking/product surface, the read is the thing the user
    actually asked for.

    ⚠ ISR TRAP (§5B.3) — THIS IS THE BLOG'S EXACT PAST MISTAKE. The blog's
    view-count-on-read is why it must run ``force-dynamic`` while the library
    runs ISR. This call must therefore NEVER run inside a cached/ISR server
    render: a server-side write either poisons the shared cache or is skipped on
    every cache hit and undercounts silently. It rides the AUTHED CLIENT call
    only. Every endpoint that calls it is ``Depends(get_current_user)`` +
    ``Cache-Control: private, no-store``, so it is safe by construction today.
    If you are about to call it from a public/cacheable page handler: don't.

    EXACTLY ONE CALL PER USER ACTION (D16.2, **REVISED 2026-07-27**) —
    the page view is the use:
      * any document page → ``POST /api/v1/library/mine/use``, fired by
        ``LibraryUseBeacon`` for GATED and OPEN items alike (§5B.2 shelves an
        item when it is opened, "gated or not").
      * ``/library/full`` reveal → records NOTHING. The reveal always happens on
        a page the beacon already counted; recording here too would make every
        gated reveal count twice and bias «الأكثر استخداماً» toward gated content.
      * workspace reference source → records its own, since no document page and
        therefore no beacon is involved there.
    """
    try:
        ct = (content_type or "").strip()
        cid = str(content_id or "").strip()
        if not user_id or not ct or not cid:
            return
        await run_db(_record_use, supabase, str(user_id), ct, cid)
    except Exception as e:  # noqa: BLE001
        # Swallow deliberately — see the docstring. Logged, never re-raised.
        logger.warning(
            "library_items: record_use failed (%s/%s): %s", content_type, content_id, e
        )


def _save_item(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> None:
    """Pin an item («حفظ») — ``source='manual'``. SYNC.

    Free at every tier and grants NO access: this stores a pointer, never
    content, and never writes to ``library_unlocks``. Saving a gated item the
    user has not unlocked is allowed (§5B.2) — it shows locked in مكتبتي, which
    is a useful intent signal.

    An explicit save must NOT clobber an existing ``source='auto'`` row's
    counters: ``use_count`` and ``first_used_at`` are preserved and only
    ``source``/``saved_at`` move.
    """
    now = _now_iso()
    row = _find_row(supabase, user_id, content_type, content_id)

    if row is None:
        inserted = _insert_row(
            supabase,
            user_id,
            content_type,
            content_id,
            {"source": "manual", "use_count": 0, "saved_at": now},
        )
        if inserted:
            return
        row = _find_row(supabase, user_id, content_type, content_id)
        if row is None:
            return

    (
        supabase.table("library_items")
        .update({"source": "manual", "saved_at": now})
        .eq("item_row_id", row["item_row_id"])
        .execute()
    )


async def save_item(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> None:
    """Explicitly pin an item to مكتبتي. Costs no unlock, grants no access."""
    ct, cid = _require_ref(content_type, content_id)
    try:
        await run_db(_save_item, supabase, str(user_id), ct, cid)
    except Exception as e:  # noqa: BLE001
        logger.exception("library_items: save failed (%s/%s): %s", ct, cid, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="تعذّر حفظ العنصر في مكتبتك",
        )


def _unsave_item(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> None:
    """Unpin an item. SYNC.

    Unpinning is not "delete my reading history". A row the user actually USED
    (``use_count > 0``) is demoted back to ``source='auto'`` and keeps its
    counters — it is still something they opened, and «الأكثر استخداماً» must not
    lose it. A row that exists ONLY because of the pin (``use_count = 0``) is
    deleted outright, since without the pin it would never have been shelved.
    """
    row = _find_row(supabase, user_id, content_type, content_id)
    if row is None:
        return
    if int(row.get("use_count") or 0) > 0:
        (
            supabase.table("library_items")
            .update({"source": "auto"})
            .eq("item_row_id", row["item_row_id"])
            .execute()
        )
        return
    (
        supabase.table("library_items")
        .delete()
        .eq("item_row_id", row["item_row_id"])
        .execute()
    )


async def unsave_item(
    supabase: SupabaseClient, user_id: str, content_type: str, content_id: str
) -> None:
    """Explicitly unpin an item («إزالة الحفظ»). Idempotent — unpinning an item
    that is not on the shelf is a no-op, not a 404."""
    ct, cid = _require_ref(content_type, content_id)
    try:
        await run_db(_unsave_item, supabase, str(user_id), ct, cid)
    except Exception as e:  # noqa: BLE001
        logger.exception("library_items: unsave failed (%s/%s): %s", ct, cid, e)
        raise LunaHTTPException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            detail="تعذّر تعديل مكتبتك",
        )


# ==========================================================================
# READS — the shelf scan
# ==========================================================================


def _scan_shelf(
    supabase: SupabaseClient, user_id: str, content_types: Optional[list[str]]
) -> list[dict[str, Any]]:
    """Every shelf row for the user (optionally filtered by type). SYNC.

    Chunked at the PostgREST 1,000-row clamp and capped at ``MAX_SHELF_SCAN``.
    Ordered by ``last_used_at`` DESC NULLS LAST so that a shelf larger than the
    cap keeps its most relevant rows. Fail-soft: a query error yields the rows
    already gathered (an empty list at worst) — مكتبتي degrades to fewer cards
    rather than 500ing.
    """
    out: list[dict[str, Any]] = []
    offset = 0
    while offset < MAX_SHELF_SCAN:
        take = min(_SCAN_CHUNK, MAX_SHELF_SCAN - offset)
        try:
            qb = (
                supabase.table("library_items")
                .select(_ITEM_COLS)
                .eq("user_id", str(user_id))
            )
            if content_types:
                qb = qb.in_("content_type", list(content_types))
            res = (
                qb.order("last_used_at", desc=True, nullsfirst=False)
                .order("saved_at", desc=True)
                .range(offset, offset + take - 1)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("library_items: shelf scan failed (%s): %s", user_id, e)
            break
        batch = res.data or []
        out.extend(batch)
        if len(batch) < take:
            break
        offset += take
    return out


def _type_counts(supabase: SupabaseClient, user_id: str) -> dict[str, int]:
    """``{content_type: rows}`` across the WHOLE shelf. SYNC.

    Drives tab visibility: ``form`` and ``calculator`` are secondary tabs shown
    only when non-empty (§5B.1). One chunked scan of a single column, not seven
    count queries. Fail-soft → zeros.
    """
    counts = {ct: 0 for ct in SHELF_CONTENT_TYPES}
    offset = 0
    while offset < MAX_SHELF_SCAN:
        take = min(_SCAN_CHUNK, MAX_SHELF_SCAN - offset)
        try:
            res = (
                supabase.table("library_items")
                .select("content_type")
                .eq("user_id", str(user_id))
                .range(offset, offset + take - 1)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("library_items: type counts failed (%s): %s", user_id, e)
            break
        batch = res.data or []
        for r in batch:
            ct = r.get("content_type")
            if ct:
                counts[ct] = counts.get(ct, 0) + 1
        if len(batch) < take:
            break
        offset += take
    return counts


def _unlock_rows(
    supabase: SupabaseClient, user_id: str, content_ids: Iterable[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    """``{(content_type, content_id): unlock row}`` for the page's items. SYNC.

    READ-ONLY on ``library_unlocks`` — that table is MONEY (D16): this module
    never inserts, updates or deletes there. One chunked ``IN`` lookup on
    ``content_id`` (the composite key is matched in Python), which is what makes
    ``was_unlocked`` / ``is_frozen`` cost one query per page instead of one
    ``resolve_access`` per row — and ``resolve_access`` would CHARGE.
    """
    ids = list(dict.fromkeys(str(c) for c in content_ids if c))
    if not ids or not user_id:
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for i in range(0, len(ids), 150):
        chunk = ids[i : i + 150]
        try:
            res = (
                supabase.table("library_unlocks")
                .select("content_type, content_id, period_key, cost, unlocked_at")
                .eq("user_id", str(user_id))
                .in_("content_id", chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("library_items: unlock lookup failed: %s", e)
            continue
        for r in res.data or []:
            ct = r.get("content_type")
            cid = r.get("content_id")
            if ct and cid is not None:
                out[(str(ct), str(cid))] = r
    return out


def _frozen_count(
    supabase: SupabaseClient, user_id: str, period_key: Optional[str]
) -> int:
    """How many of the user's unlock rows the §1.2 predicate currently FAILS.

    Only meaningful for a non-paid caller (a paid caller's predicate passes on
    every row, so the count is 0 and this is never called). Powers the §5B.4
    upgrade CTA «لديك {n} مصدراً محفوظاً في مكتبتك — رقِّ باقتك لفتحها من جديد.».
    Read-only. Fail-soft → 0.
    """
    if not user_id:
        return 0
    try:
        qb = (
            supabase.table("library_unlocks")
            .select("unlock_id", count="exact")
            .eq("user_id", str(user_id))
        )
        if period_key:
            qb = qb.neq("period_key", str(period_key))
        res = qb.limit(1).execute()
        return int(getattr(res, "count", None) or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("library_items: frozen count failed (%s): %s", user_id, e)
        return 0


# ==========================================================================
# HYDRATION — card fields, from the same sources the public hubs use
#
# EVERYTHING rendered here is in the §1.3 NEVER-GATED class: titles, entity,
# dates, topic chips, slugs. No body text, no شرح, no truncated preview. That is
# precisely why §5B.4 can list a FROZEN item without leaking anything — the
# shelf shows what you have, the item page still decides what you may read.
# ==========================================================================

# Same column sets the public hubs select, so a card's title/metadata are
# byte-identical between /library/mine and the wing it came from.
_REG_SELECT = (
    "id, reg_ref, clean_title, title, entity_name, status_class, "
    "doc_type_bucket, summary, sectors"
)
_JUDGMENT_SELECT = (
    "id, case_ref, court, court_level, city, case_number, judgment_number, "
    "date_hijri, date_gregorian, legal_domains, short_summary, summary, "
    "facts, ruling"
)
_CIRCULAR_SELECT = "id, circ_ref, title, content, source, entity_id"
# Title only — the shelf has nothing else to render for a service (see `_hydrate`).
_SERVICE_SELECT = "id, service_name_ar"
_FORM_SELECT = "id, slug, title_ar, category, use_case_md"


def _rows_by_ids(
    supabase: SupabaseClient,
    table: str,
    select_cols: str,
    ids: list[str],
    *,
    id_col: str = "id",
) -> dict[str, dict[str, Any]]:
    """``{id: row}`` for one page's corpus rows. SYNC, read-only.

    Chunks the ``IN`` lookup at 150 (PostgREST encodes ``in.(...)`` in the query
    string and hundreds of uuids blow the URL-length limit into a 400 — the trap
    ``library_service._slug_map`` documents). Fail-soft: a blip yields the rows
    it could fetch; unhydrated shelf rows still LIST (§5B.4 — never filter a row
    out), just without card metadata.
    """
    ids = list(dict.fromkeys(str(i) for i in ids if i))
    if not ids:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), 150):
        chunk = ids[i : i + 150]
        try:
            res = (
                supabase.table(table)
                .select(select_cols)
                .in_(id_col, chunk)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("library_items: %s hydration failed: %s", table, e)
            continue
        for r in res.data or []:
            key = r.get(id_col)
            if key is not None:
                out[str(key)] = r
    return out


def _url_for(content_type: str, slug: Optional[str]) -> Optional[str]:
    """Public page path for a slugged item, or ``None`` when unservable."""
    prefix = _URL_PREFIX.get(content_type)
    if not prefix or not slug:
        return None
    return f"{prefix}/{slug}"


def _public_page_url(
    supabase: SupabaseClient,
    content_type: str,
    content_id: str,
    parent_regulation_id: Optional[str] = None,
) -> Optional[str]:
    """``(content_type, content_id)`` → its page in OUR library, or ``None``. SYNC.

    The other half of :mod:`reference_resolver`: that module maps a chat
    citation's ``ref_id`` onto the ``(content_type, content_id)`` pair the ledger
    and the sidecar speak; this maps that pair onto the in-app address a reader
    can actually open («فتح ... في ريحان»).

    A **مادة resolves to its نظام page** (user decision 2026-08-01), NOT to
    ``/regulations/{reg}/{article}``. The chunk behind a citation is an arbitrary
    slice — 81% of ``reg:`` refs already lift to the whole statute (D15.1) — so
    sending the other 19% somewhere structurally different would make the same
    button land in two different places for no reason the reader can see. The
    نظام page carries the مادة anyway, and it is exactly what the unlock grants.

    ``None`` is a normal answer, not a failure: an item with no ``seo_item_meta``
    slug has no published page, and the caller must then render NO in-app link
    (never a hub fallback, never a guessed URL — both are dead ends dressed up as
    navigation). Fail-soft on every sidecar error for the same reason a shelf
    write is fail-soft: a missing link must not break a paid content reveal.
    """
    ct = (content_type or "").strip()
    cid = str(content_id or "").strip()
    if not ct or not cid:
        return None

    if ct == "article":
        parent = ls.parent_regulation_of_article(cid, parent_regulation_id)
        if not parent:
            return None
        ct, cid = "regulation", parent

    if ct not in _URL_PREFIX:
        return None

    try:
        slug = ls._slug_map(supabase, ct, [cid]).get(cid)
    except Exception as e:  # noqa: BLE001
        logger.warning("library_items: public url lookup failed (%s/%s): %s", ct, cid, e)
        return None
    return _url_for(ct, slug)


async def public_page_url(
    supabase: SupabaseClient,
    content_type: str,
    content_id: str,
    parent_regulation_id: Optional[str] = None,
) -> Optional[str]:
    """Async wrapper over :func:`_public_page_url` (sync client via ``run_db``)."""
    return await run_db(
        _public_page_url, supabase, content_type, content_id, parent_regulation_id
    )


# ==========================================================================
# BATCHED REFERENCE → PAGE URLS (the المراجع panel's «فتح ... في ريحان»)
#
# NAVIGATION, NOT CONTENT. Everything below resolves is a PATH to a page that
# enforces its own access tier — no body, no snippet, no gate decision. It must
# therefore never touch ``resolve_access``, never write ``library_unlocks``, and
# never move the balance chip: metering a link would double-charge the reader for
# the reveal they are about to pay for on the other side, and would break the
# D15.1 «name what you unlocked» line.
# ==========================================================================

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _uuid_or_empty(value: Any) -> str:
    """``value`` as a uuid string, or ``""`` when it is not one."""
    s = str(value or "").strip()
    return s if _UUID_RE.match(s) else ""


def _chunk_regulation_ids(
    supabase: SupabaseClient, chunk_ids: list[str]
) -> dict[str, str]:
    """``{chunks_v2.id: regulation_id}`` — ONE batched select. SYNC, fail-soft.

    ``owns`` is deliberately NOT read: :func:`_public_page_url` maps an
    ``article`` to its parent نظام anyway (user decision 2026-08-01), so the
    مادة/نظام split that ``reference_resolver`` needs for METERING is irrelevant
    to a link. Skipping it keeps the payload one column wide.
    """
    ids = list(dict.fromkeys(c for c in chunk_ids if c))
    if not ids:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(ids), 150):
        batch = ids[i : i + 150]
        try:
            res = (
                supabase.table("chunks_v2")
                .select("id, regulation_id")
                .in_("id", batch)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("library_items: chunk→regulation lookup failed: %s", e)
            continue
        for r in res.data or []:
            cid = r.get("id")
            rid = r.get("regulation_id")
            if cid and rid:
                out[str(cid)] = str(rid)
    return out


def _article_regulation_ids(
    supabase: SupabaseClient, article_ids: list[str]
) -> dict[str, str]:
    """``{articles_v2.id: regulation_id}`` — ONE batched select. SYNC, fail-soft.

    The ``articles`` twin of :func:`_chunk_regulation_ids`, and it exists for the
    same reason: a مادة has no page of its own that this button may open. A مادة
    **resolves to its نظام** (user decision 2026-08-01, the collapse
    :func:`_public_page_url` already applies at ``ct == "article"``), so the only
    thing worth reading off the row is its parent.

    That function cannot simply be called here: it keys on the SIDECAR key
    ``'{regulation_id}#{article_no}'`` (which carries its parent in the string),
    whereas an ``articles`` reference row carries a bare ``articles_v2.id`` uuid
    that carries neither half. Hence the lookup — one, batched, for the panel.

    ``articles_v2`` is a VIEW, so there is no FK to embed the parent through;
    a second select is the only shape available (same finding as
    ``references_service._build_article_shells``).
    """
    ids = list(dict.fromkeys(a for a in article_ids if a))
    if not ids:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(ids), 150):
        batch = ids[i : i + 150]
        try:
            res = (
                supabase.table("articles_v2")
                .select("id, regulation_id")
                .in_("id", batch)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("library_items: article→regulation lookup failed: %s", e)
            continue
        for r in res.data or []:
            aid = r.get("id")
            rid = r.get("regulation_id")
            if aid and rid:
                out[str(aid)] = str(rid)
    return out


def _public_page_urls_for_reference_rows(
    supabase: SupabaseClient, rows: list[dict[str, Any]]
) -> dict[int, str]:
    """``{n: url}`` for a whole المراجع panel, in ≤ 5 round-trips. SYNC.

    ``rows`` are ``workspace_item_references`` rows — ``n``, ``domain``,
    ``item_id``, ``ref_id``. The write path (``persist_item_references``) already
    resolved ``item_id`` to the source-row PK, and that is what makes this cheap:

    ====================  ==========================================  ======
    domain                content_id                                  lookups
    ====================  ==========================================  ======
    ``cases``             ``item_id`` **IS** ``cases.id``              0
    ``circulars``         ``item_id`` **IS** ``circulars.id``          0
    ``regulations``       ``chunks_v2.regulation_id``                  1
    ``regulation_docs``   ``item_id`` **IS** ``regulations_v2.id``     0
    ``articles``          ``articles_v2.regulation_id``                1
    ``compliance``        — no wing —                                  0
    ====================  ==========================================  ======

    then at most three ``ls._slug_map`` calls (judgment / circular / regulation).
    **Total ≤ 5 round-trips per panel load, independent of reference count.** A
    legacy row with a NULL ``item_id`` adds at most ONE more (the batched
    ``case_ref → cases.id`` lookup below) — still bounded, still batched.

    THE TWO simple_search DOMAINS LAND IN THE ``regulation`` BUCKET (plan §6.1a).
    ``regulation_docs`` **is** a نظام, and an ``articles`` مادة **collapses** to
    its نظام — the same collapse :func:`_public_page_url` applies, deliberately
    reused rather than re-invented as an ``/regulations/{reg}/{article}`` scheme
    the reveal path does not use either. Neither gets a ``_URL_PREFIX`` key of its
    own, and none is needed: both end up asking the sidecar for a ``regulation``
    slug, so they share the wing's single existing lookup.

    COMPLIANCE NEVER GETS A URL. ``_URL_PREFIX`` has no ``service`` key: the
    compliance wing was retired, so a government service has no page in our
    library. Re-adding it here without rebuilding those pages ships 404s.

    FAIL-SOFT THROUGHOUT. Any error yields no URL for the affected references —
    never a 500, never a guessed URL. A missing button is correct; a button into
    a 404 is not, and neither is a panel that fails to load because a sidecar
    blipped.

    Keys are only present for references that resolved; the caller stamps
    ``None`` for the rest.
    """
    if not rows:
        return {}

    # ---- phase 1: (n → content_id) per wing, from what the row already holds.
    judgment_by_n: dict[int, str] = {}
    circular_by_n: dict[int, str] = {}
    chunk_by_n: dict[int, str] = {}
    article_by_n: dict[int, str] = {}    # simple_search: articles_v2.id
    case_ref_by_n: dict[int, str] = {}   # legacy fallback: NULL item_id
    # Seeded directly by ``regulation_docs`` (item_id IS the نظام), then filled
    # in for chunks and مواد once their parent lookups return.
    regulation_by_n: dict[int, str] = {}

    for row in rows:
        try:
            n = int(row.get("n"))
        except (TypeError, ValueError):
            continue
        domain = (row.get("domain") or "").strip()
        item_id = _uuid_or_empty(row.get("item_id"))
        ref_id = (row.get("ref_id") or "").strip()

        if domain == "cases":
            if item_id:
                judgment_by_n[n] = item_id
            elif ref_id.startswith("case:"):
                tail = ref_id[5:].strip()
                # A pre-URA-v3 row minted ``case:<uuid>`` — that IS cases.id.
                if _uuid_or_empty(tail):
                    judgment_by_n[n] = tail
                elif tail:
                    case_ref_by_n[n] = tail
        elif domain == "circulars":
            circ_id = item_id or _uuid_or_empty(
                ref_id[len("circular:"):] if ref_id.startswith("circular:") else ""
            )
            if circ_id:
                circular_by_n[n] = circ_id
        elif domain == "regulations":
            chunk_id = item_id or _uuid_or_empty(
                ref_id[4:] if ref_id.startswith("reg:") else ""
            )
            if chunk_id:
                chunk_by_n[n] = chunk_id
        elif domain == "regulation_docs":
            # ``regdoc:<regulations_v2.id>`` — the نظام itself, zero lookups.
            # NEVER accept a ``reg:`` prefix here: that one carries a
            # chunks_v2.id, and the sidecar would answer for a different (or
            # no) document while validating perfectly (§6.2).
            reg_id = item_id or _uuid_or_empty(
                ref_id[len("regdoc:"):] if ref_id.startswith("regdoc:") else ""
            )
            if reg_id:
                regulation_by_n[n] = reg_id
        elif domain == "articles":
            # ``article:<articles_v2.id>`` — one batched parent lookup below,
            # then the نظام page (the 2026-08-01 collapse).
            article_id = item_id or _uuid_or_empty(
                ref_id[len("article:"):] if ref_id.startswith("article:") else ""
            )
            if article_id:
                article_by_n[n] = article_id
        # compliance → no wing, no URL. Deliberately not an else-branch: an
        # unknown future domain must fall through to "no button" as well.

    # Legacy case rows: case_ref → cases.id. Reuses the batch helper the write
    # path already owns rather than minting a second query for the same join.
    # Imported inside the function because ``references_service`` imports THIS
    # module at load time — a module-level import here would close the cycle.
    if case_ref_by_n:
        try:
            from backend.app.services.references_service import _fetch_case_ids

            id_by_ref = _fetch_case_ids(supabase, list(case_ref_by_n.values()))
            for n, case_ref in case_ref_by_n.items():
                resolved = id_by_ref.get(case_ref)
                if resolved:
                    judgment_by_n[n] = str(resolved)
        except Exception as e:  # noqa: BLE001
            logger.warning("library_items: legacy case_ref resolution failed: %s", e)

    # Regulations: chunk → its نظام. One batched select.
    if chunk_by_n:
        reg_by_chunk = _chunk_regulation_ids(supabase, list(chunk_by_n.values()))
        for n, chunk_id in chunk_by_n.items():
            reg_id = reg_by_chunk.get(chunk_id)
            if reg_id:
                regulation_by_n[n] = reg_id

    # Articles: مادة → its نظام. One more batched select, and it merges into the
    # SAME bucket — so a panel mixing chunks, مواد and whole أنظمة still costs one
    # sidecar lookup for the whole wing.
    if article_by_n:
        reg_by_article = _article_regulation_ids(supabase, list(article_by_n.values()))
        for n, article_id in article_by_n.items():
            reg_id = reg_by_article.get(article_id)
            if reg_id:
                regulation_by_n[n] = reg_id

    # ---- phase 2: one sidecar slug lookup per wing present on the panel.
    out: dict[int, str] = {}
    for content_type, by_n in (
        ("judgment", judgment_by_n),
        ("circular", circular_by_n),
        ("regulation", regulation_by_n),
    ):
        if not by_n:
            continue
        try:
            slugs = ls._slug_map(supabase, content_type, list(by_n.values()))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "library_items: slug map failed for %s: %s", content_type, e
            )
            continue
        for n, content_id in by_n.items():
            url = _url_for(content_type, slugs.get(str(content_id)))
            if url:
                out[n] = url
    return out


async def public_page_urls_for_reference_rows(
    supabase: SupabaseClient, rows: list[dict[str, Any]]
) -> dict[int, str]:
    """Async wrapper over :func:`_public_page_urls_for_reference_rows`.

    Every Supabase read happens inside ONE ``run_db`` thread hop. Fail-soft to
    ``{}``: the panel must render even when nothing can be linked.
    """
    if not rows:
        return {}
    try:
        return await run_db(_public_page_urls_for_reference_rows, supabase, rows)
    except Exception as e:  # noqa: BLE001
        logger.warning("library_items: reference url resolution failed: %s", e)
        return {}


def _hydrate(
    supabase: SupabaseClient, by_type: dict[str, list[str]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """``{(content_type, content_id): card fields}`` for one page.

    One batched corpus fetch + one batched sidecar slug lookup per content_type
    present on the page. The per-type card shapes mirror the public hub item
    models 1:1 (``RegHubItem``, ``JudgmentHubItem``, ``CircularHubItem``,
    ``FormHubItem``) so the existing card components drop straight in (§5B.5).
    ``service`` is the exception and carries a title alone — its wing is gone.
    """
    cards: dict[tuple[str, str], dict[str, Any]] = {}

    # --- regulations -----------------------------------------------------
    reg_ids = by_type.get("regulation") or []
    if reg_ids:
        rows = _rows_by_ids(supabase, "regulations_v2", _REG_SELECT, reg_ids)
        slugs = ls._slug_map(supabase, "regulation", reg_ids)
        for cid in reg_ids:
            r = rows.get(str(cid)) or {}
            slug = slugs.get(str(cid))
            cards[("regulation", str(cid))] = {
                "slug": slug,
                "url": _url_for("regulation", slug),
                "title": (r.get("clean_title") or r.get("title") or "").strip(),
                "entity_name": r.get("entity_name"),
                "status": ls.map_reg_status(r.get("status_class")) if r else None,
                "doc_type": ls.map_doc_type_bucket(r.get("doc_type_bucket")),
                "summary_snippet": ls._text_snippet(r.get("summary"), 160),
                "sectors": r.get("sectors") or [],
            }

    # --- مواد (nested under their نظام) -----------------------------------
    art_ids = by_type.get("article") or []
    if art_ids:
        art_slugs = ls._slug_map(supabase, "article", art_ids)
        parent_ids = [
            p
            for p in (ls.parent_regulation_of_article(cid) for cid in art_ids)
            if p
        ]
        reg_slugs = ls._slug_map(supabase, "regulation", parent_ids)
        for cid in art_ids:
            cid_s = str(cid)
            suffix = cid_s.rsplit("#", 1)[1] if "#" in cid_s else ""
            article_no = int(suffix) if suffix.isdigit() else None
            label = f"المادة {article_no}" if article_no is not None else "مادة"
            slug = art_slugs.get(cid_s)
            parent_id = ls.parent_regulation_of_article(cid_s)
            reg_slug = reg_slugs.get(str(parent_id)) if parent_id else None
            cards[("article", cid_s)] = {
                "slug": slug,
                # The public مادة page is nested under its نظام
                # (see library_service.sitemap_article_urls).
                "url": (
                    f"/regulations/{reg_slug}/{slug}" if (reg_slug and slug) else None
                ),
                "title": label,
                "article_no": article_no,
                "article_label": label,
                "reg_slug": reg_slug,
            }

    # --- judgments -------------------------------------------------------
    jud_ids = by_type.get("judgment") or []
    if jud_ids:
        rows = _rows_by_ids(supabase, "cases", _JUDGMENT_SELECT, jud_ids)
        slugs = ls._slug_map(supabase, "judgment", jud_ids)
        for cid in jud_ids:
            r = rows.get(str(cid)) or {}
            slug = slugs.get(str(cid))
            cards[("judgment", str(cid))] = {
                "slug": slug,
                "url": _url_for("judgment", slug),
                "title": judgment_display_title(r) if r else "",
                "court": (r.get("court") or "").strip(),
                "court_level": r.get("court_level"),
                "court_level_label": court_level_label(r.get("court_level")),
                "city": r.get("city"),
                "date_hijri": r.get("date_hijri"),
                "date_gregorian": ls._iso_date(r.get("date_gregorian")),
                "domains": [d for d in (r.get("legal_domains") or []) if d],
                "snippet": ls._text_snippet(
                    ls._strip_bullets(r.get("short_summary")), 160
                ),
            }

    # --- circulars -------------------------------------------------------
    circ_ids = by_type.get("circular") or []
    if circ_ids:
        rows = _rows_by_ids(supabase, "circulars", _CIRCULAR_SELECT, circ_ids)
        slugs = ls._slug_map(supabase, "circular", circ_ids)
        names = ls._entity_name_map(
            supabase, [r.get("entity_id") for r in rows.values()]
        )
        for cid in circ_ids:
            r = rows.get(str(cid)) or {}
            slug = slugs.get(str(cid))
            source_label, _ = ls._normalize_circular_source(r.get("source"))
            content = r.get("content") or ""
            cards[("circular", str(cid))] = {
                "slug": slug,
                "url": _url_for("circular", slug),
                "title": (r.get("title") or "").strip(),
                "entity_name": names.get(str(r.get("entity_id"))),
                "source_label": source_label,
                "body_snippet": ls._text_snippet(content, 160),
                "body_length": len(content),
            }

    # --- government services -----------------------------------------------
    # TITLE ONLY, since the compliance wing was retired (2026-08-03). No slug (no
    # page to address), no url (`ShelfCard` renders it unlinked), and none of the
    # card metadata the الخدمات hub card used to draw — provider, sectors and the
    # intro snippet all went with it. The row still LISTS: the reader unlocked
    # this service in a chat and it is theirs, so §5B.4's never-filter-a-row rule
    # holds; it simply has nothing to show but its name.
    svc_ids = by_type.get("service") or []
    if svc_ids:
        rows = _rows_by_ids(supabase, "services", _SERVICE_SELECT, svc_ids)
        for cid in svc_ids:
            r = rows.get(str(cid)) or {}
            cards[("service", str(cid))] = {
                # `slug`/`url` are stated as None rather than omitted: every other
                # content_type sets them, and a row that simply LACKS the keys
                # reads as "not hydrated" to anything doing a membership test.
                # This one hydrated fine — it just has nowhere to go.
                "slug": None,
                "url": None,
                "title": r.get("service_name_ar") or "",
            }

    # --- forms -----------------------------------------------------------
    # Forms have NO seo_item_meta sidecar (D7: there are no 'form' rows) — the
    # slug lives on the forms row itself, and resolve_gate keys forms on
    # ``forms.id`` (library_service.get_form_detail), so that is the content_id.
    # A slug is accepted too, defensively, since forms are the one wing whose
    # public key and gate key differ.
    form_ids = by_type.get("form") or []
    if form_ids:
        rows = _rows_by_ids(supabase, "forms", _FORM_SELECT, form_ids)
        missing = [str(i) for i in form_ids if str(i) not in rows]
        if missing:
            by_slug = _rows_by_ids(
                supabase, "forms", _FORM_SELECT, missing, id_col="slug"
            )
            rows.update(by_slug)
        for cid in form_ids:
            r = rows.get(str(cid)) or {}
            slug = r.get("slug")
            cards[("form", str(cid))] = {
                "slug": slug,
                "url": _url_for("form", slug),
                "title": (r.get("title_ar") or "").strip(),
                "category": r.get("category"),
                "use_case_snippet": ls._text_snippet(r.get("use_case_md"), 160),
            }

    return cards


# ==========================================================================
# LISTING — the مكتبتي page
# ==========================================================================


def _sort_ts(value: Any) -> str:
    """Sortable string for a nullable timestamptz (NULLS LAST under DESC).

    PostgREST always renders timestamptz in UTC (``…+00:00``), so lexicographic
    ordering of the ISO strings matches chronological ordering, and ``NULL → ""``
    sorts last under ``reverse=True`` — which is the ``DESC NULLS LAST`` the
    ``idx_library_items_user_recent`` index expresses.
    """
    return str(value or "")


def _article_no_of(content_id: str) -> int:
    """``article_no`` from a ``'{regulation_id}#{n}'`` key, for child ordering."""
    if "#" not in content_id:
        return 0
    suffix = content_id.rsplit("#", 1)[1]
    return int(suffix) if suffix.isdigit() else 0


def _row_view(
    row: dict[str, Any],
    card: dict[str, Any],
    unlock_state: tuple[bool, bool],
) -> dict[str, Any]:
    """One shelf row + its card fields, flattened (the §5B.5 row shape)."""
    was_unlocked, is_frozen = unlock_state
    view = {
        "content_type": row["content_type"],
        "content_id": row["content_id"],
        "source": row.get("source"),
        "use_count": int(row.get("use_count") or 0),
        "first_used_at": row.get("first_used_at"),
        "last_used_at": row.get("last_used_at"),
        "saved_at": row.get("saved_at"),
        "was_unlocked": was_unlocked,
        "is_frozen": is_frozen,
        "is_shelf_row": True,
        # HYDRATED, not LINKABLE. An item unlocked from a chat citation is
        # very often a regulation with no public page yet (only 100 of 3,373
        # carry a slug while the library is in sample mode), and the reader
        # owns it either way — so it renders as a normal card and simply is
        # not a link. `url` is what decides linkability; this decides whether
        # there is anything to show at all.
        "is_available": bool((card.get("title") or "").strip()),
    }
    view.update(card)
    return view


def _virtual_parent(content_id: str, card: dict[str, Any],
                    unlock_state: tuple[bool, bool]) -> dict[str, Any]:
    """A نظام header for مواد whose statute is NOT itself on the shelf.

    §5B.1: "a مادة without its statute reads as an orphan". The parent is
    rendered so its مواد have something to nest under; ``is_shelf_row=False``
    tells the frontend the نظام itself was never opened or pinned (so it renders
    as a group header, and «حفظ» on it is an add, not a toggle-off).
    """
    was_unlocked, is_frozen = unlock_state
    view = {
        "content_type": "regulation",
        "content_id": content_id,
        "source": None,
        "use_count": 0,
        "first_used_at": None,
        "last_used_at": None,
        "saved_at": None,
        "was_unlocked": was_unlocked,
        "is_frozen": is_frozen,
        "is_shelf_row": False,
        # HYDRATED, not LINKABLE. An item unlocked from a chat citation is
        # very often a regulation with no public page yet (only 100 of 3,373
        # carry a slug while the library is in sample mode), and the reader
        # owns it either way — so it renders as a normal card and simply is
        # not a link. `url` is what decides linkability; this decides whether
        # there is anything to show at all.
        "is_available": bool((card.get("title") or "").strip()),
    }
    view.update(card)
    return view


# ==========================================================================
# SHELF SEARCH (bm25_navigation_search.md §5.2 · §6.2)
#
# مكتبتي is a JOIN, not a corpus: its rows are public documents the caller
# happened to open or pin. So searching it is "rank the public corpora, keep what
# is on this shelf" — which is why there is no ``owner_user_id`` on those
# ``search_index`` rows and why ``bm25_search`` is called with ``p_owner=None``
# here. The user-scoping is the intersection, done below, in this process.
#
# ⚠ KNOWN RECALL BOUND, and it is inherent rather than lazy. ``bm25_search``
# cannot be handed a candidate id list, so the intersection runs against the top
# ``MAX_RESULTS`` (200) hits of the whole public index. A shelf item that matches
# the query but ranks 201st for the corpus at large will not be found. With the
# index holding ~100 slugged rows per wing that is unreachable today; when the
# slug backfill lands it becomes reachable for very common terms on a very large
# shelf. The fix is a candidate-id parameter on the RPC — Wave F, not a Python
# workaround here, because any workaround means a second ranking path.
#
# مواد: NOT indexed (D6). A shelf مادة matches when its PARENT نظام matches,
# which is also how the shelf DISPLAYS it (§5B.1 nests مواد under their statute),
# so the two rules agree. ``form``/``calculator`` are out of the index entirely
# (D7) and therefore never match a search — documented, not silent.
# ==========================================================================


def _shelf_keys(supabase: SupabaseClient, user_id: str) -> set[tuple[str, str]]:
    """``{(content_type, content_id)}`` for the caller's whole shelf. SYNC.

    Reuses ``_scan_shelf`` (chunked + capped + fail-soft) rather than issuing its
    own query, so the search view of the shelf can never see rows the listing
    view cannot.
    """
    return {
        (str(r.get("content_type")), str(r.get("content_id")))
        for r in _scan_shelf(supabase, str(user_id), None)
        if r.get("content_type") and r.get("content_id")
    }


def shelf_rank(
    supabase: SupabaseClient, user_id: str, query: str
) -> tuple[dict[tuple[str, str], int], bool]:
    """``({(content_type, content_id): rank}, total_is_exact)`` for a shelf search. SYNC.

    Rank 0 is the best match. A مادة inherits its نظام's rank (see the block
    comment); a shelf row whose type is not indexed simply never appears.
    """
    query = (query or "").strip()
    if not query:
        return {}, True

    keys = _shelf_keys(supabase, str(user_id))
    if not keys:
        return {}, True

    page = search_service.run_bm25(
        supabase,
        corpora=search_service.PUBLIC_CORPORA,
        query=query,
        owner_user_id=None,
        limit=search_service.MAX_RESULTS,
        offset=0,
    )

    # corpus id → rank, then project onto the shelf's (type, id) key space.
    hit_rank = {
        (str(h.get("corpus")), str(h.get("content_id"))): i
        for i, h in enumerate(page.hits)
    }

    out: dict[tuple[str, str], int] = {}
    for ct, cid in keys:
        if ct == "article":
            parent = ls.parent_regulation_of_article(cid)
            rank = hit_rank.get(("regulation", str(parent))) if parent else None
        else:
            rank = hit_rank.get((ct, cid))
        if rank is not None:
            out[(ct, cid)] = rank
    return out, page.total_is_exact


def search_shelf(
    supabase: SupabaseClient, user_id: str, query: str
) -> tuple[list[dict[str, Any]], bool]:
    """Shelf hits in ``search_service`` hit shape — backs ``GET /search/mine``. SYNC.

    Returns ``(hits, total_is_exact)``. The hits carry the slug resolved from the
    sidecar so ``/search/mine`` can build a URL without a second lookup per row;
    a shelf item with no published page yields no slug and therefore no link,
    which is the same answer ``_public_page_url`` gives the listing.
    """
    ranks, exact = shelf_rank(supabase, str(user_id), query)
    if not ranks:
        return [], exact

    # مواد resolve to their نظام's page (the rule ``_public_page_url`` states),
    # so they are folded into the parent rather than emitted as their own hit —
    # otherwise one نظام could occupy five result rows.
    by_item: dict[tuple[str, str], int] = {}
    for (ct, cid), rank in ranks.items():
        if ct == "article":
            parent = ls.parent_regulation_of_article(cid)
            if not parent:
                continue
            ct, cid = "regulation", str(parent)
        by_item[(ct, cid)] = min(by_item.get((ct, cid), rank), rank)

    by_type: dict[str, list[str]] = {}
    for ct, cid in by_item:
        by_type.setdefault(ct, []).append(cid)
    cards = _hydrate(supabase, by_type)

    hits: list[dict[str, Any]] = []
    for (ct, cid), rank in sorted(by_item.items(), key=lambda kv: kv[1]):
        card = cards.get((ct, cid), {})
        hits.append(
            {
                "corpus": ct,
                "content_id": cid,
                "slug": card.get("slug"),
                "title": card.get("title") or "",
                "facets": {},
                # A DESCENDING pseudo-score so the merge in ``/search/mine``
                # interleaves shelf hits with blog/template hits sensibly. The
                # real BM25 score is not carried through the shelf intersection
                # (rank is what survives), and mixing a raw score with a
                # rank-derived one would be worse than being explicit about it.
                "score": float(search_service.MAX_RESULTS - rank),
            }
        )
    return hits, exact


async def list_items(
    supabase: SupabaseClient,
    user_id: str,
    *,
    content_type: Optional[str] = None,
    sort: str = "recent",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    q: Optional[str] = None,
) -> dict[str, Any]:
    """One page of «مكتبتي» — the hub-shaped envelope (§5B.5).

    Returns::

        {
          "items": [ { ...hub card fields,
                       content_type, content_id, source, use_count,
                       first_used_at, last_used_at, saved_at,
                       was_unlocked, is_frozen,
                       is_shelf_row, is_available,
                       group_use_count, group_last_used_at,
                       child_articles: [ ...same shape, minus child_articles ] } ],
          "page", "page_size", "total", "total_pages",
          "content_type", "sort",
          "counts": {content_type: rows},
          "stored_library_count": int,
          "frozen_count": int,
          "is_paid": bool
        }

    ``total`` counts TOP-LEVEL groups under the current filter (a نظام plus its
    nested مواد is one), while ``counts`` is the raw per-type row count over the
    WHOLE shelf — they legitimately differ, and ``counts`` is what tab visibility
    keys off. ``stored_library_count`` is a third number again: ROWS in
    ``library_unlocks`` (D16.1 — the shelf inventory behind «لديك {n} مصدراً»),
    never SUM(cost), which is the quota's number.

    Behaviour that is NOT negotiable:

    * **مواد nest under their نظام** (§5B.1) — never a top-level tab. An article
      row is attached to its parent regulation's ``child_articles``; when the
      نظام is not itself on the shelf a header row is synthesized for it
      (``is_shelf_row=False``) rather than emitting an orphan مادة.
    * **A frozen shelf still lists everything** (§5B.4) — rows are NEVER filtered
      by entitlement. "A frozen library rendered as an empty page is a worse
      product AND a worse conversion surface." Everything rendered is in the
      never-gated class (§1.3), so listing a frozen item leaks nothing.
    * ``is_frozen`` uses the SAME §1.2 predicate Layer B uses
      (``library_service._predicate_passes``) against ONE ``library_state`` read
      for the whole page. ``resolve_access`` is deliberately NOT called per row:
      it would CHARGE and insert ledger rows just to render a list.

    If the quota RPC is unreachable the BADGE degrades (``is_frozen=False``,
    ``is_paid=False``, ``frozen_count=0``) but the listing does not — an unknown
    predicate over never-gated metadata leaks nothing, whereas an empty shelf
    would be a visible product failure.

    ``q`` (BM25 over the shelf, see ``shelf_rank``) narrows the rows AND replaces
    ``sort`` for that request — a result list ordered by "recently used" is not a
    result list. ``counts`` deliberately stays whole-shelf: it drives TAB
    VISIBILITY, and tabs vanishing mid-search would make the shelf look emptied.
    ``total``/``total_pages`` do reflect the filter, because those describe the
    list the caller is paging.
    """
    page = max(1, int(page or 1))
    page_size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
    sort = normalize_sort(sort)
    q = (q or "").strip() or None

    # مواد ride along with the نظام tab (and with the unfiltered view).
    if content_type is None:
        scan_types: Optional[list[str]] = None
    elif content_type == "regulation":
        scan_types = ["regulation", "article"]
    else:
        scan_types = [content_type]

    rows = await run_db(_scan_shelf, supabase, str(user_id), scan_types)
    counts = await run_db(_type_counts, supabase, str(user_id))
    stored = await ls.stored_library_count(supabase, str(user_id))

    # ---- group: articles nest under their parent نظام (§5B.1) -------------
    top_rows: list[dict[str, Any]] = []
    reg_index: dict[str, dict[str, Any]] = {}
    children: dict[str, list[dict[str, Any]]] = {}
    orphan_articles: list[dict[str, Any]] = []

    for r in rows:
        if r.get("content_type") == "regulation":
            reg_index[str(r.get("content_id"))] = r
            top_rows.append(r)
        elif r.get("content_type") != "article":
            top_rows.append(r)

    for r in rows:
        if r.get("content_type") != "article":
            continue
        parent_id = ls.parent_regulation_of_article(str(r.get("content_id")))
        if not parent_id:
            # No resolvable parent key — surface it rather than dropping it.
            orphan_articles.append(r)
            continue
        children.setdefault(str(parent_id), []).append(r)

    # Parents referenced only by their مواد get a synthesized header row.
    virtual_parent_ids = [pid for pid in children if pid not in reg_index]

    # ---- order (group-aware) ---------------------------------------------
    def _group_stats(content_id: str, own: Optional[dict[str, Any]]) -> tuple[int, str, str]:
        kids = children.get(content_id, [])
        use = int((own or {}).get("use_count") or 0) + sum(
            int(k.get("use_count") or 0) for k in kids
        )
        last = max(
            [_sort_ts((own or {}).get("last_used_at"))]
            + [_sort_ts(k.get("last_used_at")) for k in kids]
        )
        saved = max(
            [_sort_ts((own or {}).get("saved_at"))]
            + [_sort_ts(k.get("saved_at")) for k in kids]
        )
        return use, last, saved

    entries: list[dict[str, Any]] = []
    for r in top_rows:
        cid = str(r.get("content_id"))
        is_reg = r.get("content_type") == "regulation"
        use, last, saved = (
            _group_stats(cid, r)
            if is_reg
            else (
                int(r.get("use_count") or 0),
                _sort_ts(r.get("last_used_at")),
                _sort_ts(r.get("saved_at")),
            )
        )
        entries.append(
            {"row": r, "virtual": False, "content_type": r.get("content_type"),
             "content_id": cid, "use": use, "last": last, "saved": saved}
        )
    for pid in virtual_parent_ids:
        use, last, saved = _group_stats(pid, None)
        entries.append(
            {"row": None, "virtual": True, "content_type": "regulation",
             "content_id": pid, "use": use, "last": last, "saved": saved}
        )
    for r in orphan_articles:
        entries.append(
            {"row": r, "virtual": False, "content_type": "article",
             "content_id": str(r.get("content_id")),
             "use": int(r.get("use_count") or 0),
             "last": _sort_ts(r.get("last_used_at")),
             "saved": _sort_ts(r.get("saved_at"))}
        )

    if q:
        # ---- SEARCH MODE (§5.2) — narrow, then order by relevance ---------
        # A GROUP matches when the نظام itself matches OR any of its nested مواد
        # do: the مادة is displayed inside the نظام card, so hiding the group
        # would hide a genuine hit. The group's rank is the best of the two.
        ranks, _exact = await run_db(shelf_rank, supabase, str(user_id), q)
        if ranks:
            def _entry_rank(e: dict[str, Any]) -> Optional[int]:
                cid = str(e["content_id"])
                candidates = [ranks.get((str(e["content_type"]), cid))]
                if e["content_type"] == "regulation":
                    candidates += [
                        ranks.get(("article", str(k.get("content_id"))))
                        for k in children.get(cid, [])
                    ]
                found = [r for r in candidates if r is not None]
                return min(found) if found else None

            ranked = [(r, e) for e in entries if (r := _entry_rank(e)) is not None]
            ranked.sort(key=lambda pair: pair[0])
            entries = [e for _r, e in ranked]
        else:
            entries = []
    elif sort == "most_used":
        entries.sort(key=lambda e: (e["use"], e["last"]), reverse=True)
    elif sort == "saved":
        entries.sort(key=lambda e: (e["saved"], e["last"]), reverse=True)
    else:  # 'recent' — last_used_at DESC NULLS LAST (empty string sorts last)
        entries.sort(key=lambda e: (e["last"], e["saved"]), reverse=True)

    total = len(entries)
    total_pages = max(1, math.ceil(total / page_size)) if total else 1
    window = entries[(page - 1) * page_size : (page - 1) * page_size + page_size]

    # ---- hydrate ONLY the page -------------------------------------------
    by_type: dict[str, list[str]] = {}
    for e in window:
        by_type.setdefault(e["content_type"], []).append(e["content_id"])
        if e["content_type"] == "regulation":
            for k in children.get(e["content_id"], []):
                by_type.setdefault("article", []).append(str(k.get("content_id")))
    cards = await run_db(_hydrate, supabase, by_type)

    # ---- entitlement badges (ONE state read, ONE unlock lookup) ----------
    lookup_ids: list[str] = []
    for ct, ids in by_type.items():
        lookup_ids.extend(ids)
        if ct == "article":
            lookup_ids.extend(
                p for p in (ls.parent_regulation_of_article(i) for i in ids) if p
            )
    unlocks = await run_db(_unlock_rows, supabase, str(user_id), lookup_ids)

    # ONE state read for the whole page — the §1.2 predicate needs the caller's
    # CURRENT plan + period, not a per-row decision. Never resolve_access here.
    state: Optional[_quota.LibraryQuotaState] = None
    try:
        state = await _quota.library_state(supabase, str(user_id))
    except Exception as e:  # noqa: BLE001
        # Degrade the BADGE, never the listing: everything on this page is
        # never-gated metadata (§1.3), so an unknown predicate leaks nothing.
        logger.warning("library_items: library_state unavailable: %s", e)

    def _badges(content_type: str, content_id: str) -> tuple[bool, bool]:
        row = unlocks.get((content_type, str(content_id)))
        if row is None and content_type == "article":
            # D5 — a نظام unlock covers its مواد. Same rule Layer B applies in
            # resolve_access; the reverse does NOT hold.
            parent_id = ls.parent_regulation_of_article(str(content_id))
            if parent_id:
                row = unlocks.get(("regulation", str(parent_id)))
        if row is None:
            return False, False
        if state is None:
            return True, False
        return True, not ls._predicate_passes(row, state)

    items: list[dict[str, Any]] = []
    for e in window:
        ct = e["content_type"]
        cid = e["content_id"]
        card = cards.get((ct, cid), {})
        badges = _badges(ct, cid)
        view = (
            _virtual_parent(cid, card, badges)
            if e["virtual"]
            else _row_view(e["row"], card, badges)
        )
        kids = []
        if ct == "regulation":
            for k in sorted(
                children.get(cid, []),
                key=lambda x: _article_no_of(str(x.get("content_id"))),
            ):
                kid_cid = str(k.get("content_id"))
                kids.append(
                    _row_view(k, cards.get(("article", kid_cid), {}),
                              _badges("article", kid_cid))
                )
        view["child_articles"] = kids
        view["group_use_count"] = e["use"]
        view["group_last_used_at"] = e["last"] or None
        items.append(view)

    # §5B.4 conversion surface: how much of the shelf a downgraded caller can no
    # longer reach. A paid caller's predicate passes on every row → always 0.
    is_paid = bool(state.is_paid) if state is not None else False
    frozen = 0
    if stored and state is not None and not is_paid:
        frozen = await run_db(
            _frozen_count, supabase, str(user_id), state.period_key
        )

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "content_type": content_type,
        "sort": sort,
        # Echoed so the client can tell "the shelf is empty" from "this SEARCH is
        # empty" without re-reading its own query string.
        "q": q,
        "counts": counts,
        "stored_library_count": stored,
        "frozen_count": frozen,
        "is_paid": is_paid,
    }
