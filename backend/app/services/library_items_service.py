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
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.services import library_service as ls
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
    "record_use",
    "save_item",
    "unsave_item",
    "list_items",
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
_URL_PREFIX = {
    "regulation": "/regulations",
    "judgment": "/judgments",
    "circular": "/circulars",
    "service": "/compliance",
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
_SERVICE_SELECT = (
    "id, service_name_ar, provider_name, is_most_used, sectors, intro_description"
)
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


def _hydrate(
    supabase: SupabaseClient, by_type: dict[str, list[str]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """``{(content_type, content_id): card fields}`` for one page.

    One batched corpus fetch + one batched sidecar slug lookup per content_type
    present on the page. The per-type card shapes mirror the public hub item
    models 1:1 (``RegHubItem``, ``JudgmentHubItem``, ``CircularHubItem``,
    ``ComplianceHubItem``, ``FormHubItem``) so the existing card components drop
    straight in (§5B.5).
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

    # --- compliance services (never gated — the الخدمات tab) --------------
    svc_ids = by_type.get("service") or []
    if svc_ids:
        rows = _rows_by_ids(supabase, "services", _SERVICE_SELECT, svc_ids)
        slugs = ls._slug_map(supabase, "service", svc_ids)
        for cid in svc_ids:
            r = rows.get(str(cid)) or {}
            slug = slugs.get(str(cid))
            cards[("service", str(cid))] = {
                "slug": slug,
                "url": _url_for("service", slug),
                "title": r.get("service_name_ar") or "",
                "provider_name": r.get("provider_name"),
                "is_most_used": bool(r.get("is_most_used")),
                "sectors": r.get("sectors") or [],
                "intro_snippet": ls._text_snippet(r.get("intro_description"), 160),
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


async def list_items(
    supabase: SupabaseClient,
    user_id: str,
    *,
    content_type: Optional[str] = None,
    sort: str = "recent",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
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
    """
    page = max(1, int(page or 1))
    page_size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
    sort = normalize_sort(sort)

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

    if sort == "most_used":
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
        "counts": counts,
        "stored_library_count": stored,
        "frozen_count": frozen,
        "is_paid": is_paid,
    }
