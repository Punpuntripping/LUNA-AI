"""Per-USER library item budget + the yield-to-open detector.

Plan: ``.claude/plans/cloudflare_navigation_hardening.md`` §2.2 (the budget) and
§2.3 (the detector).

WHAT THIS BOUNDS, AND WHY IT IS NOT A RATE LIMITER
--------------------------------------------------
The depth cap bounds how DEEP a caller goes; §2.1's filter validation bounds how
many distinct page 1s they can mint. Neither bounds a PAID caller, whose page
depth is deliberately unbounded — they are the only tier left that can still
traverse the corpus, and they are also the only tier with an identity. So this
meters *corpus reach*: the number of DISTINCT content ids the hub/list endpoints
have yielded to one user in a rolling window (default 500 / hour).

Distinct ids, not requests, is the whole point. A request counter is defeated by
9-items-a-page pagination and punishes an ordinary reader who re-loads the same
page; an id counter is indifferent to how the items were fetched and only ever
moves when the caller reaches something NEW.

⚠ PER-USER ONLY — DO NOT ADD AN ANON KEY. Anonymous library traffic reaches this
backend through the Next.js ISR renderer (``frontend/lib/library/api.ts``: a
server-side fetch with no auth header), so every anonymous visitor on the planet
arrives as ONE caller from ONE address. An IP- or session-keyed anon budget here
would meter the RENDERER, not the visitor, and would take the public library down
the first time it tripped. Bounding the anon layer is the edge's job (plan PART 3
+ PART 5); this is the authed half, and the two are not interchangeable.

⚠ NOT ``LIBRARY_ITEM_RATE_LIMIT``. That knob (600/min, ``rate_limit.py``) is a
runaway-client backstop over the item-page bucket and, once §3.2 puts ISR on the
private network, every anonymous render keys into a SINGLE bucket. It is not an
enumeration control and must not be tuned as one. This module is the enumeration
control, and it is orthogonal.

STORAGE — one Redis sorted set per user
---------------------------------------
    key    ``library:itembudget:{user_id}:{window_seconds}``
    member ``"{section}:{slug}"``  — the item's public identity
    score  epoch seconds of FIRST sight inside the window

``ZREMRANGEBYSCORE`` prunes the window on every touch, ``ZCARD`` is the used
count, and ``ZADD … NX`` is the charge: NX means the score never moves, so an id
costs its owner at most once per window no matter how often it is re-rendered.
``EXPIRE`` keeps abandoned keys from accumulating. Same client handling as the
two limiters (``request.app.state.redis``, pipeline, no connection ownership).

Slugs are the id space because they are the only identity the public payloads
carry — no hub card exposes a corpus uuid (the same reason
``library_items_service._resolve_content_id`` exists). The section prefix keeps
two wings that happen to share a slug from collapsing into one item.

GATE FIRST, CHARGE AFTER — and the overshoot that buys
------------------------------------------------------
``enforce_item_budget`` runs BEFORE the hub query (a refused caller must not cost
a DB round-trip) and refuses only when the window is already at the limit;
``charge_items`` runs AFTER, with the ids the response actually yielded. So the
true ceiling is ``limit + one page`` (9 ids). That is deliberate: the alternative
is charging for a page that is then refused, i.e. billing reach the caller never
received.

FAIL-CLOSED, VIA A BOUNDED PROCESS-LOCAL WINDOW
-----------------------------------------------
Same stance and same caveat as ``route_limits`` (the global middleware fails OPEN
because it is a cost damper; this is a boundary). With Redis gone the count falls
back to a per-process set, which is a FLOOR, not a cap: N workers/replicas each
allow the full budget. It can only ever count LESS than Redis would, so an outage
cannot manufacture a 429 for a legitimate reader.

THE 429 IS THE PROJECT'S EXISTING ONE — no new shape
----------------------------------------------------
Refusal raises ``LunaHTTPException(429, ErrorCode.RATE_LIMITED, …)`` with
``RATE_LIMIT_MESSAGE`` and ``rate_limit_headers`` imported from ``rate_limit.py``
— byte-identical to what the middleware and ``route_limits`` already return
(``rate_limit.py``: "Both produce the same 429 body/headers … one contract"), so
the frontend, the CORS ``expose_headers`` list and every client retry rule keep
working untouched. The one addition is ``Cache-Control: private, no-store``: this
429 is a per-USER answer on a URL that is otherwise shared-cacheable for an hour,
and one of them parked in the edge cache would serve one user's exhausted budget
to every visitor asking for that page.

THE LADDER — free 36 / paid 96, per hour (owner, 2026-08-02)
------------------------------------------------------------
``navigation_enumeration_defence.md`` §3 specified a four-row ladder (anon 60 /
free 300 / paid 500); ``cloudflare_navigation_hardening.md`` §2.2 collapsed it to
one flat 500. Neither shipped as written — this module now carries the owner's
tightened ladder, keyed on ``_hub_caller``'s browse tier:

    tier    ids/hour   ≈ hub pages (9 cards)   on breach
    ------  ---------  ----------------------  ---------
    anon    —          (never metered)         —
    free    36         4                       429
    paid    96         ~10.7                   429

**Anon is deliberately absent, not forgotten.** It is bounded by depth instead:
``ANON_HUB_MAX_PAGE = 1`` caps every navigation endpoint at page 1, so an
anonymous caller's reach per filter signature is one page. Metering it here is
not merely unnecessary, it is unsafe — see the PER-USER ONLY note above.

⚠ The paid row is BELOW a straight 12-page walk (108 ids) — that is the point,
and it was confirmed as intent. Re-visiting pages already seen stays free, so
this bites on new reach only.

TUNING
------
Env-configurable, read FRESH on every call so a change is an env flip rather than
a restart, and so tests can ``monkeypatch.setenv``:

    LIBRARY_USER_ITEM_BUDGET_FREE            default 36
    LIBRARY_USER_ITEM_BUDGET_PAID            default 96
    LIBRARY_USER_ITEM_BUDGET                 ALL-TIER override (<= 0 disables)
    LIBRARY_USER_ITEM_BUDGET_WINDOW_SECONDS  default 3600
    LIBRARY_YIELD_ALERT_THRESHOLD            default 200   (<= 0 disables §2.3)

``LIBRARY_USER_ITEM_BUDGET`` is kept as the single-knob override and kill switch:
when it is SET it wins for every tier (so ``=0`` still turns the whole meter off
in one flip); when it is UNSET the per-tier knobs above apply.

What this costs a real reader: the ~20 document pages a lawyer actually opens
cost NOTHING (document endpoints are anonymous and unmetered; only hub/list
yields count), and re-visiting a page is free by construction. A free account is
capped at hub page 3, i.e. 27 ids per filter signature, so 36 buys page 1–3 plus
one more filter — deliberately tight, and the tightest row in the ladder.
"""
from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from fastapi import Request
from redis.asyncio import Redis as AsyncRedis

from backend.app.errors import ErrorCode, LunaHTTPException
from backend.app.middleware.rate_limit import (
    RATE_LIMIT_MESSAGE,
    rate_limit_headers,
)
from shared.db.run import run_db

logger = logging.getLogger(__name__)

__all__ = [
    "ITEM_BUDGET_ENV",
    "FREE_ITEM_BUDGET_ENV",
    "PAID_ITEM_BUDGET_ENV",
    "ITEM_BUDGET_WINDOW_ENV",
    "YIELD_ALERT_THRESHOLD_ENV",
    "DEFAULT_FREE_ITEM_BUDGET",
    "DEFAULT_PAID_ITEM_BUDGET",
    "DEFAULT_ITEM_BUDGET",
    "DEFAULT_WINDOW_SECONDS",
    "DEFAULT_YIELD_ALERT_THRESHOLD",
    "ItemBudgetState",
    "budget_key",
    "item_budget_limit",
    "item_budget_window_seconds",
    "yield_alert_threshold",
    "item_keys",
    "enforce_item_budget",
    "charge_items",
    "count_document_opens",
    "yield_to_open_report",
    "reset_process_state",
]

# --- knobs -----------------------------------------------------------------

ITEM_BUDGET_ENV = "LIBRARY_USER_ITEM_BUDGET"  # all-tier override + kill switch
FREE_ITEM_BUDGET_ENV = "LIBRARY_USER_ITEM_BUDGET_FREE"
PAID_ITEM_BUDGET_ENV = "LIBRARY_USER_ITEM_BUDGET_PAID"
ITEM_BUDGET_WINDOW_ENV = "LIBRARY_USER_ITEM_BUDGET_WINDOW_SECONDS"
YIELD_ALERT_THRESHOLD_ENV = "LIBRARY_YIELD_ALERT_THRESHOLD"

DEFAULT_FREE_ITEM_BUDGET = 36
DEFAULT_PAID_ITEM_BUDGET = 96
# Back-compat alias: the flat default before the ladder landed. It is the PAID
# row because that is the limit an unknown tier resolves to (see
# ``item_budget_limit``), so importers reading "the default" still read the
# number this module will actually apply when nobody says otherwise.
DEFAULT_ITEM_BUDGET = DEFAULT_PAID_ITEM_BUDGET
DEFAULT_WINDOW_SECONDS = 3600
DEFAULT_YIELD_ALERT_THRESHOLD = 200

# ``_hub_caller``'s tier string for a signed-in account on the free plan (or a
# locked one, which browses like free). Anything else that reaches this module
# with a user_id is a paying tier.
FREE_TIER = "free"

# Defensive ceiling on how many ids ONE response may charge. A hub page is 9;
# this only exists so a future endpoint that returns hundreds of rows cannot
# balloon the ZSET in a single call.
MAX_KEYS_PER_CALL = 200

# Redis key namespaces. The budget key is also what ``yield_to_open_report``
# scans, so the pattern below and ``budget_key`` must stay in step.
_KEY_PREFIX = "library:itembudget"
_ALERT_KEY_PREFIX = "library:yieldalert"


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back on junk. Zero/negative is
    PRESERVED (it is the kill switch), unlike ``rate_limit._env_int``."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def item_budget_limit(tier: Optional[str] = None) -> int:
    """Distinct ids one user may be yielded per window, by browse tier.

    ``tier`` is ``_hub_caller``'s class — ``"free"`` or ``"paid"``. ``"anon"``
    never arrives here (an anonymous caller has no ``user_id``, and both entry
    points no-op on that before asking for a limit).

    An UNKNOWN or missing tier resolves to the PAID row, deliberately. This is
    the same forgiving-on-failure stance the rest of the meter takes: a tier
    lookup that hiccups must not manufacture a 429 for a legitimate reader. The
    hole it opens is small and self-closing — a caller whose tier cannot be
    resolved is a caller whose *depth* cap already fell back to free (3 hub
    pages = 27 ids), so they cannot reach even the free row by walking.

    ``LIBRARY_USER_ITEM_BUDGET``, when set, overrides every tier — including
    ``<= 0``, which is the kill switch for the whole meter.
    """
    if os.environ.get(ITEM_BUDGET_ENV) is not None:
        return _env_int(ITEM_BUDGET_ENV, DEFAULT_PAID_ITEM_BUDGET)
    if (tier or "").strip().lower() == FREE_TIER:
        return _env_int(FREE_ITEM_BUDGET_ENV, DEFAULT_FREE_ITEM_BUDGET)
    return _env_int(PAID_ITEM_BUDGET_ENV, DEFAULT_PAID_ITEM_BUDGET)


def item_budget_window_seconds() -> int:
    """The rolling window, seconds. Junk or ``<= 0`` falls back to one hour."""
    value = _env_int(ITEM_BUDGET_WINDOW_ENV, DEFAULT_WINDOW_SECONDS)
    return value if value > 0 else DEFAULT_WINDOW_SECONDS


def yield_alert_threshold() -> int:
    """§2.3 detection threshold — ids yielded before we look for zero opens."""
    return _env_int(YIELD_ALERT_THRESHOLD_ENV, DEFAULT_YIELD_ALERT_THRESHOLD)


def budget_key(user_id: str, window_seconds: int) -> str:
    return f"{_KEY_PREFIX}:{user_id}:{window_seconds}"


def _alert_key(user_id: str, window_seconds: int) -> str:
    return f"{_ALERT_KEY_PREFIX}:{user_id}:{window_seconds}"


def user_id_from_budget_key(key: str) -> Optional[str]:
    """``library:itembudget:{user_id}:{window}`` → ``user_id`` (or ``None``)."""
    if isinstance(key, bytes):
        key = key.decode("utf-8", "replace")
    if not key.startswith(_KEY_PREFIX + ":"):
        return None
    tail = key[len(_KEY_PREFIX) + 1:]
    user_id, sep, _window = tail.rpartition(":")
    return user_id if sep and user_id else None


# --- the item identity -----------------------------------------------------


def item_keys(section: str, items: Iterable[Mapping[str, Any]]) -> list[str]:
    """``[{'slug': …}, …]`` → the deduped member list for one hub response.

    Slug-based because that is the only identity a public hub card carries.
    Items without a slug are skipped rather than collapsed onto one key — an
    unslugged row is not reach, and lumping them together would let a single
    malformed row mask a real one.
    """
    section = (section or "").strip() or "unknown"
    out: list[str] = []
    seen: set[str] = set()
    for it in items or []:
        try:
            slug = str((it or {}).get("slug") or "").strip()
        except AttributeError:  # not a mapping — ignore rather than 500 a page
            continue
        if not slug:
            continue
        key = f"{section}:{slug}"
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:MAX_KEYS_PER_CALL]


# --- state -----------------------------------------------------------------


@dataclass(frozen=True)
class ItemBudgetState:
    """The window as it stood when the request was gated."""

    user_id: str
    used: int
    limit: int
    remaining: int
    window_seconds: int
    reset_at: int
    backend: str  # "redis" | "process" (process => Redis was unavailable)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


# --- process-local fallback -------------------------------------------------


class _ProcessLocalDistinctWindow:
    """Per-process distinct-id window used when Redis is unavailable.

    Bounded by construction, because it must never become the memory leak that
    an outage turns into an outage of its own:

    * at most ``MAX_TRACKED_IDENTITIES`` users (LRU, expired buckets swept first);
    * at most ``limit + 1`` ids per user (oldest first-seen dropped), which is
      all the count needs — past the limit the caller is refused anyway.

    Deliberately smaller than ``route_limits._ProcessLocalWindow``'s identity cap:
    each bucket here holds ids, not timestamps. Only AUTHED library browsers ever
    land in it, so the live population is tiny; the cap is a ceiling, not a
    sizing estimate.

    Not thread-safe by design — every method is synchronous with no awaits, so it
    is atomic with respect to the event loop it runs on.
    """

    MAX_TRACKED_IDENTITIES = 500

    def __init__(self) -> None:
        self._buckets: "OrderedDict[str, OrderedDict[str, float]]" = OrderedDict()

    def _bucket(self, identity: str, cutoff: float) -> "OrderedDict[str, float]":
        bucket = self._buckets.get(identity)
        if bucket is None:
            bucket = OrderedDict()
            self._buckets[identity] = bucket
        self._buckets.move_to_end(identity)
        for member in [m for m, ts in bucket.items() if ts <= cutoff]:
            bucket.pop(member, None)
        return bucket

    def count(self, identity: str, now: float, window_seconds: int) -> int:
        return len(self._bucket(identity, now - window_seconds))

    def add(
        self,
        identity: str,
        members: Sequence[str],
        now: float,
        window_seconds: int,
        limit: int,
    ) -> int:
        bucket = self._bucket(identity, now - window_seconds)
        for member in members:
            bucket.setdefault(member, now)  # NX semantics: first sight wins
        cap = max(1, limit) + 1
        while len(bucket) > cap:
            bucket.popitem(last=False)
        self._evict(now - window_seconds)
        return len(bucket)

    def _evict(self, cutoff: float) -> None:
        if len(self._buckets) <= self.MAX_TRACKED_IDENTITIES:
            return
        stale = [
            k
            for k, b in self._buckets.items()
            if not b or max(b.values()) <= cutoff
        ]
        for k in stale:
            self._buckets.pop(k, None)
        while len(self._buckets) > self.MAX_TRACKED_IDENTITIES:
            self._buckets.popitem(last=False)

    def reset(self) -> None:
        self._buckets.clear()


_fallback = _ProcessLocalDistinctWindow()

# Once-per-window guard for the §2.3 alert when Redis is unavailable (the Redis
# path uses SET NX EX). ``{user_id: expiry_epoch}``, swept on write.
_alert_guard: "OrderedDict[str, float]" = OrderedDict()
_ALERT_GUARD_MAX = 500


def reset_process_state() -> None:
    """Drop every process-local bucket + alert guard (tests)."""
    _fallback.reset()
    _alert_guard.clear()


# --- Redis plumbing ---------------------------------------------------------


def _redis(request: Optional[Request]) -> Optional[AsyncRedis]:
    """The app's Redis client, or ``None`` — same access path as both limiters
    (they do not own the connection either; ``main.py``'s supervisor does, and it
    parks ``None`` on ``app.state`` during an outage)."""
    if request is None:
        return None
    try:
        return getattr(request.app.state, "redis", None)
    except Exception:  # noqa: BLE001 — no app/state in some test harnesses
        return None


async def _used_redis(
    redis: AsyncRedis, key: str, now: float, window_seconds: int
) -> int:
    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, now - window_seconds)
    pipe.zcard(key)
    results = await pipe.execute()
    return int(results[1] or 0)


async def _charge_redis(
    redis: AsyncRedis,
    key: str,
    members: Sequence[str],
    now: float,
    window_seconds: int,
) -> int:
    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, now - window_seconds)
    # NX: an id already inside the window keeps its ORIGINAL score, so it ages
    # out one window after FIRST sight and can never be charged twice for the
    # same visit. Re-rendering a page the caller already saw is free.
    pipe.zadd(key, {m: now for m in members}, nx=True)
    pipe.zcard(key)
    pipe.expire(key, window_seconds)
    results = await pipe.execute()
    return int(results[2] or 0)


# --- the gate ---------------------------------------------------------------


def _budget_exceeded(state: ItemBudgetState) -> LunaHTTPException:
    """The project's EXISTING 429 — same body ``luna_exception_handler`` renders
    for ``route_limits`` and same headers ``rate_limit.rate_limited_response``
    sets. Plus ``no-store``: this answer belongs to one user and the hub URL it
    sits on is otherwise shared-cacheable for an hour."""
    headers = rate_limit_headers(
        reset_at=state.reset_at,
        window_seconds=state.window_seconds,
        remaining=0,
    )
    headers["Cache-Control"] = "private, no-store"
    return LunaHTTPException(
        status_code=429,
        code=ErrorCode.RATE_LIMITED,
        detail=RATE_LIMIT_MESSAGE,
        headers=headers,
    )


async def enforce_item_budget(
    request: Optional[Request],
    user_id: Optional[str],
    tier: Optional[str] = None,
) -> Optional[ItemBudgetState]:
    """Refuse (429) when this user's window is already full. Call BEFORE the query.

    ``user_id`` ``None`` (anonymous) is a NO-OP and always will be — see the
    module header. ``tier`` is ``_hub_caller``'s browse class and selects the row
    of the ladder; omitting it resolves to the paid row (``item_budget_limit``).
    Returns the state on the allowed path, ``None`` when the meter does not apply
    (anonymous, or disabled by env).
    """
    if not user_id:
        return None

    limit = item_budget_limit(tier)
    if limit <= 0:
        return None

    window = item_budget_window_seconds()
    key = budget_key(str(user_id), window)
    now = time.time()

    redis = _redis(request)
    used: Optional[int] = None
    backend = "redis"

    if redis is not None:
        try:
            used = await _used_redis(redis, key, now, window)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Library item budget: Redis error, falling back to the "
                "in-process window (fail-closed): %s",
                e,
            )
            used = None

    if used is None:
        backend = "process"
        used = _fallback.count(str(user_id), now, window)

    state = ItemBudgetState(
        user_id=str(user_id),
        used=used,
        limit=limit,
        remaining=max(0, limit - used),
        window_seconds=window,
        reset_at=int(now + window),
        backend=backend,
    )

    if state.exhausted:
        logger.info(
            "Library item budget exhausted: user=%s used=%s limit=%s window=%ss "
            "backend=%s",
            state.user_id, state.used, state.limit, state.window_seconds,
            state.backend,
        )
        raise _budget_exceeded(state)

    return state


async def charge_items(
    request: Optional[Request],
    user_id: Optional[str],
    members: Sequence[str],
    *,
    tier: Optional[str] = None,
    supabase: Any = None,
) -> Optional[int]:
    """Record the ids a response actually yielded. Call AFTER the query.

    NEVER RAISES. A metering failure must not break a page the caller already
    paid for with a DB round-trip — the budget is an abuse bound, not a
    correctness property of the response.

    Returns the distinct total in the window after the charge, or ``None`` when
    nothing was charged (anonymous, disabled, empty page, or a storage failure).
    """
    try:
        if not user_id or not members:
            return None
        # Same row the gate used, so the process-local fallback's per-user cap
        # (``limit + 1``) matches what ``enforce_item_budget`` will refuse on.
        limit = item_budget_limit(tier)
        if limit <= 0:
            return None

        window = item_budget_window_seconds()
        key = budget_key(str(user_id), window)
        now = time.time()
        members = list(dict.fromkeys(m for m in members if m))[:MAX_KEYS_PER_CALL]
        if not members:
            return None

        redis = _redis(request)
        total: Optional[int] = None
        if redis is not None:
            try:
                total = await _charge_redis(redis, key, members, now, window)
            except Exception as e:  # noqa: BLE001
                logger.warning("Library item budget: charge failed (%s)", e)
                total = None
        if total is None:
            total = _fallback.add(str(user_id), members, now, window, limit)

        await _maybe_flag_yield_without_opens(
            request, supabase, str(user_id), total, window
        )
        return total
    except Exception as e:  # noqa: BLE001 — belt and braces; see the docstring
        logger.warning("Library item budget: charge_items swallowed %s", e)
        return None


# ===========================================================================
# §2.3 — YIELD-TO-OPEN DETECTION (never enforcement)
#
# The signature of enumeration is reach without reading: hundreds of items
# yielded by the hubs and not one document opened. Both halves now exist and
# they live in different stores, which is why this is the only place that can
# join them:
#
#   yielded — the §2.2 ZSET above. ``library_items`` cannot supply it: that table
#             records USES (``record_library_item_use``, fired by the authed
#             ``LibraryUseBeacon`` on a DOCUMENT page), never hub impressions.
#   opened  — ``library_items`` rows whose ``last_used_at`` falls inside the
#             window. Verified shape (migration 106): user_id · content_type ·
#             content_id · source · use_count · first_used_at · last_used_at ·
#             saved_at, UNIQUE(user_id, content_type, content_id). A «حفظ» pin
#             inserts with ``use_count=0`` and NO ``last_used_at``, so a saved-
#             but-never-opened row correctly does not read as an open.
#
# TWO WAYS TO RUN IT, because they answer different questions:
#
#   1. INLINE (live) — ``charge_items`` calls the guard below when a user crosses
#      the threshold. Guarded by ``SET NX EX`` so it costs ONE count query per
#      user per window at most, and swallowed whole: detection must never be able
#      to fail a request. It emits a WARNING log — the alerting path this project
#      actually has today (Logfire ingests backend logs; there is no alert-rule
#      infrastructure to hook, so building one would be inventing a dependency).
#   2. SWEEP (offline) — ``yield_to_open_report`` scans every live budget key and
#      returns the flagged sessions. Run it with:
#
#          python -m backend.app.services.library_budget_service
#
#      (reads REDIS_URL + the Supabase service key from the environment, prints
#      one JSON line per flagged session and a count; read-only throughout).
# ===========================================================================


def _count_document_opens(supabase: Any, user_id: str, since_iso: str) -> int:
    """``library_items`` rows this user USED since ``since_iso``. SYNC, read-only.

    ``count='exact'`` + ``limit(1)`` is the cheap COUNT(*) PostgREST offers, and
    ``idx_library_items_user_recent`` (user_id, last_used_at DESC) serves it.
    """
    res = (
        supabase.table("library_items")
        .select("item_row_id", count="exact")
        .eq("user_id", str(user_id))
        .gte("last_used_at", since_iso)
        .limit(1)
        .execute()
    )
    return int(getattr(res, "count", None) or 0)


async def count_document_opens(
    supabase: Any, user_id: str, *, window_seconds: Optional[int] = None
) -> int:
    """Document opens by one user in the trailing window (0 on any failure —
    a detector that 500s is worse than one that reports nothing)."""
    window = window_seconds or item_budget_window_seconds()
    since = (datetime.now(timezone.utc) - timedelta(seconds=window)).isoformat()
    try:
        return await run_db(_count_document_opens, supabase, str(user_id), since)
    except Exception as e:  # noqa: BLE001
        logger.warning("Yield-to-open: open count failed for %s: %s", user_id, e)
        return 0


def _claim_alert_slot_locally(user_id: str, now: float, window_seconds: int) -> bool:
    """One alert per user per window without Redis. Bounded LRU."""
    expiry = _alert_guard.get(user_id)
    if expiry is not None and expiry > now:
        return False
    for stale in [k for k, exp in _alert_guard.items() if exp <= now]:
        _alert_guard.pop(stale, None)
    _alert_guard[user_id] = now + window_seconds
    _alert_guard.move_to_end(user_id)
    while len(_alert_guard) > _ALERT_GUARD_MAX:
        _alert_guard.popitem(last=False)
    return True


async def _maybe_flag_yield_without_opens(
    request: Optional[Request],
    supabase: Any,
    user_id: str,
    yielded: int,
    window_seconds: int,
) -> None:
    """Log a WARNING when a user crosses the threshold with ZERO opens (§2.3).

    Detection only: it never raises, never blocks, never changes the response.
    One count query per user per window, gated by ``SET NX EX``.
    """
    try:
        threshold = yield_alert_threshold()
        if threshold <= 0 or yielded <= threshold or supabase is None:
            return

        now = time.time()
        redis = _redis(request)
        claimed = False
        if redis is not None:
            try:
                claimed = bool(
                    await redis.set(
                        _alert_key(user_id, window_seconds),
                        str(int(now)),
                        ex=window_seconds,
                        nx=True,
                    )
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Yield-to-open: alert guard unavailable (%s)", e)
                claimed = _claim_alert_slot_locally(user_id, now, window_seconds)
        else:
            claimed = _claim_alert_slot_locally(user_id, now, window_seconds)

        if not claimed:
            return

        opens = await count_document_opens(
            supabase, user_id, window_seconds=window_seconds
        )
        if opens == 0:
            logger.warning(
                "Library yield-to-open alert: user=%s yielded=%s opens=0 "
                "window=%ss threshold=%s (plan §2.3 — detection only)",
                user_id, yielded, window_seconds, threshold,
            )
        else:
            logger.info(
                "Library yield-to-open: user=%s yielded=%s opens=%s window=%ss "
                "— not flagged",
                user_id, yielded, opens, window_seconds,
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("Yield-to-open: detector swallowed %s", e)


async def yield_to_open_report(
    redis: Optional[AsyncRedis],
    supabase: Any,
    *,
    threshold: Optional[int] = None,
    window_seconds: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Every live session with ``> threshold`` items yielded and ZERO opens.

    Read-only sweep over the §2.2 keys — nothing here enforces anything. Returns
    one dict per FLAGGED session::

        {"user_id": …, "yielded": int, "opens": 0, "window_seconds": int}

    ``opens`` is always 0 by construction (unflagged sessions are dropped); it is
    emitted so the row reads as evidence rather than as an assertion.
    """
    if redis is None:
        logger.warning("Yield-to-open sweep: no Redis client — nothing to scan")
        return []

    window = window_seconds or item_budget_window_seconds()
    limit = threshold if threshold is not None else yield_alert_threshold()
    if limit <= 0:
        return []

    now = time.time()
    flagged: list[dict[str, Any]] = []
    pattern = f"{_KEY_PREFIX}:*"

    async for raw_key in redis.scan_iter(match=pattern, count=200):
        key = raw_key.decode("utf-8", "replace") if isinstance(raw_key, bytes) else raw_key
        user_id = user_id_from_budget_key(key)
        if not user_id:
            continue
        try:
            await redis.zremrangebyscore(key, 0, now - window)
            yielded = int(await redis.zcard(key) or 0)
        except Exception as e:  # noqa: BLE001
            logger.warning("Yield-to-open sweep: read failed on %s: %s", key, e)
            continue
        if yielded <= limit:
            continue
        opens = await count_document_opens(
            supabase, user_id, window_seconds=window
        )
        if opens == 0:
            flagged.append(
                {
                    "user_id": user_id,
                    "yielded": yielded,
                    "opens": 0,
                    "window_seconds": window,
                }
            )
    return flagged


def _main() -> int:
    """``python -m backend.app.services.library_budget_service`` — the §2.3 sweep.

    Read-only. Needs the same environment the backend runs with (``REDIS_URL``
    for the budget keys, Supabase service credentials for the open counts).
    """
    import asyncio
    import json

    from shared.cache.redis import get_async_redis_client
    from shared.db.client import get_admin_client

    async def _run() -> int:
        redis = get_async_redis_client()
        supabase = get_admin_client()
        rows = await yield_to_open_report(redis, supabase)
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        print(
            f"flagged={len(rows)} threshold={yield_alert_threshold()} "
            f"window={item_budget_window_seconds()}s"
        )
        return 0

    return asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover — operator entry point
    raise SystemExit(_main())
