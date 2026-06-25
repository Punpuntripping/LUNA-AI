"""Redis counters + Postgres rehydration for the per-user quota gate.

Key layout:

    quota:{user_id}:ocr:day:{YYYY-MM-DD}   integer  pages   TTL 31d
    quota:{user_id}:ord:day:{YYYY-MM-DD}   float    USD     TTL 31d   (monthly window)
    quota:{user_id}:web:day:{YYYY-MM-DD}   integer  calls   TTL 31d
    quota:{user_id}:ord:sess               float    USD     TTL 5h    (session accumulator)
    quota:{user_id}:ord:wk                 float    USD     TTL 7d    (weekly accumulator)

Three ord windows, each with a DIFFERENT shape:

  * session — a FIXED 5-hour window that starts when the user sends a message.
    A single accumulator key created (value 0) by ``start_session`` on send via
    SET NX with a 5h TTL; settle increments it WITHOUT extending the TTL, so the
    window is anchored to the first message and resets exactly 5h later. After
    expiry the next message opens a fresh session. NOT a rolling window.

  * weekly — a FIXED 7-day window that starts when the user sends a message,
    exactly the session model but 7 days long. A single accumulator key created
    (value 0) by ``start_week`` on send via SET NX with a 7d TTL; settle
    increments it WITHOUT extending the TTL, so the window is anchored to the
    first message and resets exactly 7 days later. After expiry the next message
    opens a fresh week. NOT a calendar week and NOT a rolling window. (For a
    7-day plan this makes the weekly window span the whole subscription.)

  * monthly — rolling 30 UTC days, summed from the daily ord buckets (the only
    window still using the day-bucket model). Enforced as a silent backstop;
    NOT shown in the UI.

OCR + web stay on the rolling-30-day day-bucket model.

Redis is the hot path. On Redis miss (cold start, evicted key, brief outage) we
rehydrate from the llm_calls ledger (the durable cost/pages source of truth) and
write the value back so the next read hits Redis again. The session AND weekly
windows are inherently Redis-stateful (their anchor IS the key's creation time);
when Redis is down they degrade to a trailing-5h / trailing-7d PG approximation
rather than failing every send.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)

Meter = Literal["ocr", "ord", "web"]
METERS: tuple[Meter, ...] = ("ocr", "ord", "web")

# Per-meter day-bucket TTL — one day past the longest window read over the
# buckets (30-day monthly/ocr/web) → 31d.
_TTL_BY_METER: dict[str, int] = {
    "ord": 86_400 * 31,
    "ocr": 86_400 * 31,
    "web": 86_400 * 31,
}

# Session + weekly: fixed window lengths, each anchored at the first message
# (SET NX on send). The TTL == the window length exactly, so a missing key
# means the window ended (the next message opens a fresh one).
SESSION_TTL_S = 5 * 3_600          # 5 hours
WEEK_TTL_S = 86_400 * 7            # 7 days


def _ttl_for(meter: Meter) -> int:
    return _TTL_BY_METER.get(meter, 86_400 * 31)


# ── time helpers ────────────────────────────────────────────────────────────

def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def last_n_days_utc(n: int, today: date | None = None) -> list[date]:
    """Returns n dates ending at today: [today, today-1, ..., today-(n-1)]."""
    base = today or today_utc()
    return [base - timedelta(days=i) for i in range(n)]


def next_utc_midnight() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


# ── key layout ──────────────────────────────────────────────────────────────

def day_key(meter: Meter, user_id: str, day: date) -> str:
    return f"quota:{user_id}:{meter}:day:{day.isoformat()}"


def session_key(user_id: str) -> str:
    return f"quota:{user_id}:ord:sess"


def week_key(user_id: str) -> str:
    return f"quota:{user_id}:ord:wk"


# ── window usage result ─────────────────────────────────────────────────────

@dataclass
class WindowUsage:
    """Outcome of a window read.

    ``total`` is the spend (USD) we could determine; ``missing_days`` counts
    sub-windows whose value could not be determined (cold key AND PG fallback
    failed). The gate treats ``missing_days > 0`` as "unknowable → fail closed";
    the UI report renders ``total`` with an ``approximate`` flag and never raises.
    """

    total: float
    missing_days: int

    @property
    def complete(self) -> bool:
        return self.missing_days == 0


# ── PG rehydration ──────────────────────────────────────────────────────────

def rehydrate_window_from_pg(
    supabase: SupabaseClient,
    user_id: str,
    meter: Meter,
    dates: list[date],
) -> dict[date, float] | None:
    """ONE PG query for all requested days (day-bucket windows).

    Returns ``{day: value}`` zero-filled, or ``None`` when the query failed (the
    caller treats ``None`` as "unknown" — never as zero). Sync; callers wrap it
    in ``asyncio.to_thread``.
    """
    if not dates:
        return {}
    if meter == "web":                      # no PG backing yet — known zero
        return {d: 0.0 for d in dates}
    col = "cost_usd" if meter == "ord" else "pages_used"
    start, end = min(dates), max(dates) + timedelta(days=1)
    try:
        result = (
            supabase.table("llm_calls")
            .select(f"created_at,{col}")
            .eq("user_id", user_id)
            .gte("created_at", f"{start.isoformat()}T00:00:00Z")
            .lt("created_at", f"{end.isoformat()}T00:00:00Z")
            .execute()
        )
    except Exception as e:
        logger.warning(
            "quota.rehydrate_window_from_pg(meter=%s, %s..%s) failed: %s",
            meter, start, end, e,
        )
        return None
    out = {d: 0.0 for d in dates}
    for r in (getattr(result, "data", None) or []):
        try:
            day = datetime.fromisoformat(
                str(r["created_at"]).replace("Z", "+00:00")
            ).date()
        except Exception:
            continue
        if day in out:
            out[day] += float(r.get(col) or 0)
    return out


def rehydrate_ord_since_pg(
    supabase: SupabaseClient,
    user_id: str,
    start_ts: datetime,
) -> float | None:
    """Sum ord cost (USD) from the llm_calls ledger since ``start_ts``. Returns
    the sum, or ``None`` on query failure ("unknown", never zero). Sync; callers
    wrap it in ``asyncio.to_thread``. Backs the session + weekly accumulators on
    a cold key or a Redis outage."""
    try:
        result = (
            supabase.table("llm_calls")
            .select("cost_usd")
            .eq("user_id", user_id)
            .gte("created_at", start_ts.isoformat())
            .execute()
        )
    except Exception as e:
        logger.warning("quota.rehydrate_ord_since_pg(since=%s) failed: %s", start_ts, e)
        return None
    return sum(float(r.get("cost_usd") or 0) for r in (getattr(result, "data", None) or []))


# ── day-bucket window read (monthly ord, ocr, web) ───────────────────────────

async def usage_window(
    redis: AsyncRedis | None,
    supabase: SupabaseClient,
    user_id: str,
    meter: Meter,
    days: int,
) -> WindowUsage:
    """Meter usage over the last ``days`` UTC days (including today), summed from
    daily buckets. ``days=30`` → rolling monthly. Redis hot path; cold buckets
    rehydrated from PG in ONE batched query and written back. Cold key + PG
    failure → reported as unknown (never folded in as zero) so the gate can fail
    closed."""
    if days < 1:
        return WindowUsage(0.0, 0)
    dates = last_n_days_utc(days)
    keys = [day_key(meter, user_id, d) for d in dates]

    raw: list | None = None
    if redis is not None:
        try:
            raw = await redis.mget(keys)
        except Exception as e:
            logger.warning("quota.usage_window MGET failed (PG fallback): %s", e)

    if raw is None:  # Redis absent or MGET failed → whole window from PG
        per_day = await asyncio.to_thread(
            rehydrate_window_from_pg, supabase, user_id, meter, dates
        )
        if per_day is None:
            return WindowUsage(0.0, len(dates))
        return WindowUsage(float(sum(per_day.values())), 0)

    total, cold = 0.0, []
    for i, v in enumerate(raw):
        if v is None:
            cold.append(i)
        else:
            try:
                total += float(v)
            except (TypeError, ValueError):
                cold.append(i)

    if cold:
        per_day = await asyncio.to_thread(
            rehydrate_window_from_pg, supabase, user_id, meter,
            [dates[i] for i in cold],
        )
        if per_day is None:
            return WindowUsage(total, len(cold))
        ttl = _ttl_for(meter)
        for i in cold:
            val = per_day.get(dates[i], 0.0)
            total += val
            try:
                await redis.set(keys[i], val, ex=ttl)    # backfill, best-effort
            except Exception:
                pass
    return WindowUsage(total, 0)


# ── session window (fixed 5h, anchored at first message) ─────────────────────

async def start_session(redis: AsyncRedis | None, user_id: str) -> None:
    """Open a session if none is active: SET the accumulator to 0 with a 5h TTL,
    but only if absent (NX). A no-op when a session is already running (preserves
    its spend AND its remaining TTL) or when Redis is down. Called on every send
    so the window is anchored to the user's first message."""
    if redis is None:
        return
    try:
        await redis.set(session_key(user_id), 0, ex=SESSION_TTL_S, nx=True)
    except Exception as e:
        logger.warning("quota.start_session failed: %s", e)


async def session_usage(
    redis: AsyncRedis | None,
    supabase: SupabaseClient,
    user_id: str,
) -> tuple[WindowUsage, datetime | None]:
    """Read the current session's spend and reset time. Returns
    ``(WindowUsage, resets_at)`` where ``resets_at`` is when the active session
    expires, or ``None`` when no session is active (read-only path, e.g. the UI
    report). Pure read — does NOT open a session; call ``start_session`` first on
    the send path. Redis down → trailing-5h PG approximation."""
    now = datetime.now(timezone.utc)
    key = session_key(user_id)

    if redis is not None:
        try:
            pipe = redis.pipeline()
            pipe.get(key)
            pipe.pttl(key)
            val, pttl = await pipe.execute()
        except Exception as e:
            logger.warning("quota.session_usage read failed (PG fallback): %s", e)
        else:
            if val is not None:
                try:
                    total = float(val)
                except (TypeError, ValueError):
                    total = 0.0
                resets = (
                    now + timedelta(milliseconds=pttl)
                    if isinstance(pttl, int) and pttl > 0
                    else now + timedelta(seconds=SESSION_TTL_S)
                )
                return WindowUsage(total, 0), resets
            return WindowUsage(0.0, 0), None      # no active session

    # Redis down/absent → approximate the current session as trailing-5h spend.
    s = await asyncio.to_thread(
        rehydrate_ord_since_pg, supabase, user_id, now - timedelta(seconds=SESSION_TTL_S)
    )
    if s is None:
        return WindowUsage(0.0, 1), None          # unknowable → gate fails closed
    return WindowUsage(float(s), 0), now + timedelta(seconds=SESSION_TTL_S)


# ── weekly window (fixed 7d, anchored at first message — session model) ───────

async def start_week(redis: AsyncRedis | None, user_id: str) -> None:
    """Open a weekly window if none is active: SET the accumulator to 0 with a 7d
    TTL, but only if absent (NX). A no-op when a week is already running
    (preserves its spend AND its remaining TTL) or when Redis is down. Called on
    every send so the window is anchored to the user's first message — for a
    7-day plan this makes the weekly window span the whole subscription."""
    if redis is None:
        return
    try:
        await redis.set(week_key(user_id), 0, ex=WEEK_TTL_S, nx=True)
    except Exception as e:
        logger.warning("quota.start_week failed: %s", e)


async def week_usage(
    redis: AsyncRedis | None,
    supabase: SupabaseClient,
    user_id: str,
) -> tuple[WindowUsage, datetime | None]:
    """Read the current weekly window's spend and reset time. Returns
    ``(WindowUsage, resets_at)`` where ``resets_at`` is when the active window
    expires, or ``None`` when no window is active (read-only path, e.g. the UI
    report). Pure read — does NOT open a window; call ``start_week`` first on the
    send path. Mirrors ``session_usage`` exactly (7d instead of 5h). Redis down →
    trailing-7d PG approximation."""
    now = datetime.now(timezone.utc)
    key = week_key(user_id)

    if redis is not None:
        try:
            pipe = redis.pipeline()
            pipe.get(key)
            pipe.pttl(key)
            val, pttl = await pipe.execute()
        except Exception as e:
            logger.warning("quota.week_usage read failed (PG fallback): %s", e)
        else:
            if val is not None:
                try:
                    total = float(val)
                except (TypeError, ValueError):
                    total = 0.0
                resets = (
                    now + timedelta(milliseconds=pttl)
                    if isinstance(pttl, int) and pttl > 0
                    else now + timedelta(seconds=WEEK_TTL_S)
                )
                return WindowUsage(total, 0), resets
            return WindowUsage(0.0, 0), None      # no active week

    # Redis down/absent → approximate the current week as trailing-7d spend.
    s = await asyncio.to_thread(
        rehydrate_ord_since_pg, supabase, user_id, now - timedelta(seconds=WEEK_TTL_S)
    )
    if s is None:
        return WindowUsage(0.0, 1), None          # unknowable → gate fails closed
    return WindowUsage(float(s), 0), now + timedelta(seconds=WEEK_TTL_S)


# ── counter writes ──────────────────────────────────────────────────────────

async def incr_today(
    redis: AsyncRedis | None,
    user_id: str,
    meter: Meter,
    amount: float | int,
) -> None:
    """Fire-and-forget. Increments today's day bucket by ``amount`` and refreshes
    the TTL. Used by the ocr + web meters. No-op when Redis is unavailable — PG
    rehydration catches up on the next read."""
    if redis is None or not amount:
        return
    key = day_key(meter, user_id, today_utc())
    ttl = _ttl_for(meter)
    try:
        pipe = redis.pipeline()
        if isinstance(amount, int) and not isinstance(amount, bool):
            pipe.incrby(key, amount)
        else:
            pipe.incrbyfloat(key, float(amount))
        pipe.expire(key, ttl)
        await pipe.execute()
    except Exception as e:
        logger.warning("quota.incr_today(meter=%s) failed: %s", meter, e)


def incr_today_sync(
    redis: SyncRedis | None,
    user_id: str,
    meter: Meter,
    amount: float | int,
) -> None:
    """Sync counterpart of incr_today (used by the usage_sink flush)."""
    if redis is None or not amount:
        return
    key = day_key(meter, user_id, today_utc())
    ttl = _ttl_for(meter)
    try:
        pipe = redis.pipeline()
        if isinstance(amount, int) and not isinstance(amount, bool):
            pipe.incrby(key, amount)
        else:
            pipe.incrbyfloat(key, float(amount))
        pipe.expire(key, ttl)
        pipe.execute()
    except Exception as e:
        logger.warning("quota.incr_today_sync(meter=%s) failed: %s", meter, e)


def _ord_keys(user_id: str) -> tuple[str, str, str]:
    """(daily, session, weekly) ord keys for the current instant."""
    return (
        day_key("ord", user_id, today_utc()),
        session_key(user_id),
        week_key(user_id),
    )


async def incr_ord(redis: AsyncRedis | None, user_id: str, cost_usd: float) -> None:
    """Ord settle: increment the daily bucket (monthly window), the session
    accumulator, and the weekly accumulator in one pipeline. The session AND
    weekly TTLs are set NX so settle never extends those fixed windows (both are
    anchored to the first message); only the date-stamped daily TTL is refreshed."""
    if redis is None or not cost_usd:
        return
    amt = float(cost_usd)
    dkey, skey, wkey = _ord_keys(user_id)
    try:
        pipe = redis.pipeline()
        pipe.incrbyfloat(dkey, amt)
        pipe.expire(dkey, _ttl_for("ord"))
        pipe.incrbyfloat(skey, amt)
        pipe.expire(skey, SESSION_TTL_S, nx=True)
        pipe.incrbyfloat(wkey, amt)
        pipe.expire(wkey, WEEK_TTL_S, nx=True)
        await pipe.execute()
    except Exception as e:
        logger.warning("quota.incr_ord failed: %s", e)


def incr_ord_sync(redis: SyncRedis | None, user_id: str, cost_usd: float) -> None:
    """Sync counterpart of incr_ord — used by the usage_sink flush."""
    if redis is None or not cost_usd:
        return
    amt = float(cost_usd)
    dkey, skey, wkey = _ord_keys(user_id)
    try:
        pipe = redis.pipeline()
        pipe.incrbyfloat(dkey, amt)
        pipe.expire(dkey, _ttl_for("ord"))
        pipe.incrbyfloat(skey, amt)
        pipe.expire(skey, SESSION_TTL_S, nx=True)
        pipe.incrbyfloat(wkey, amt)
        pipe.expire(wkey, WEEK_TTL_S, nx=True)
        pipe.execute()
    except Exception as e:
        logger.warning("quota.incr_ord_sync failed: %s", e)
