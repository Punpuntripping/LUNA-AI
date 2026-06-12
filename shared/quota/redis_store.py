"""Redis counters + Postgres rehydration for the per-user quota gate.

Key layout:

    quota:{user_id}:ocr:day:{YYYY-MM-DD}     integer  pages   TTL 31d
    quota:{user_id}:ord:day:{YYYY-MM-DD}     float    USD     TTL 31d
    quota:{user_id}:web:day:{YYYY-MM-DD}     integer  calls   TTL 31d
    quota:{user_id}:ord:hour:{YYYY-MM-DDTHH} float    USD     TTL 6h

Daily buckets back the weekly (7d) and monthly (30d) windows; the ord meter
additionally writes an hourly bucket backing the rolling 5-hour *session*
window. Every reported window is the sum of the appropriate trailing buckets.
TTL is one unit longer than the longest window the bucket is read over so the
rolling sum always sees a complete window.

Redis is the hot path. On Redis miss (cold start, evicted key, brief outage)
we rehydrate the missing day from the llm_calls ledger (the durable cost/pages
source of truth) and write it back so the next read hits Redis again.
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

# Per-meter TTL — one day longer than the longest window we read it over.
# All three meters are now gated on a 30-day monthly window → 31d TTL.
_TTL_BY_METER: dict[str, int] = {
    "ord": 86_400 * 31,
    "ocr": 86_400 * 31,
    "web": 86_400 * 31,
}

# Rolling session window for the ord meter: hourly buckets, summed over the
# trailing SESSION_WINDOW_HOURS. TTL one hour past the window.
SESSION_WINDOW_HOURS = 5
_HOUR_TTL = 3_600 * (SESSION_WINDOW_HOURS + 1)


def _ttl_for(meter: Meter) -> int:
    return _TTL_BY_METER.get(meter, 86_400 * 8)


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


def current_hour_utc() -> datetime:
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def last_n_hours_utc(n: int, now_hour: datetime | None = None) -> list[datetime]:
    """Returns n hour marks ending at the current hour: [now, now-1h, ...]."""
    base = now_hour or current_hour_utc()
    return [base - timedelta(hours=i) for i in range(n)]


def next_hour_top() -> datetime:
    """Top of the next UTC hour — when the oldest session bucket rolls off."""
    return current_hour_utc() + timedelta(hours=1)


# ── key layout ──────────────────────────────────────────────────────────────

def day_key(meter: Meter, user_id: str, day: date) -> str:
    return f"quota:{user_id}:{meter}:day:{day.isoformat()}"


def hour_key(user_id: str, hour: datetime) -> str:
    return f"quota:{user_id}:ord:hour:{hour.strftime('%Y-%m-%dT%H')}"


# ── window usage result ─────────────────────────────────────────────────────

@dataclass
class WindowUsage:
    """Outcome of a usage_window read.

    ``total`` is the sum over days whose value is KNOWN (hot Redis bucket or a
    successful PG rehydrate). ``missing_days`` is the count of days whose value
    could not be determined — cold key AND the PG fallback failed. The gate
    treats ``missing_days > 0`` as "unknowable → fail closed"; the UI report
    renders ``total`` with an ``approximate`` flag and never raises.
    """

    total: float        # sum over days whose value is KNOWN
    missing_days: int   # days whose value could not be determined

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
    """ONE PG query for all requested days.

    Returns ``{day: value}`` zero-filled for days with no rows, or ``None``
    when the query failed (the caller treats ``None`` as "unknown" — never as
    zero). Sync; callers wrap it in ``asyncio.to_thread``.

    PG source of truth = the per-call llm_calls ledger (migration 058). Cost
    (``cost_usd``) and OCR pages (``pages_used``) both live there now; agent_runs
    no longer carries them. The ``web`` meter has no PG backing yet (future
    skill) so it returns a known-zero dict rather than ``None``.
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
        if day in out:                       # span may cover non-requested days
            out[day] += float(r.get(col) or 0)
    return out


# ── counter reads ──────────────────────────────────────────────────────────

async def usage_window(
    redis: AsyncRedis | None,
    supabase: SupabaseClient,
    user_id: str,
    meter: Meter,
    days: int,
) -> WindowUsage:
    """Returns the meter's usage over the last ``days`` UTC days (including
    today). ``days=1`` → today only; ``days=7`` → rolling weekly; ``days=30`` →
    rolling monthly.

    Redis is the source of truth; cold buckets are rehydrated from PG in ONE
    batched query (off the event loop via ``to_thread``) and written back. If
    Redis is unavailable the whole window is read from PG.

    Returns a :class:`WindowUsage`: ``total`` over the days we could determine,
    plus ``missing_days`` for cold days whose PG rehydrate ALSO failed — those
    are reported as unknown (``None`` sentinel from the PG helper), never folded
    in as zero. The gate fails closed on ``missing_days > 0``; the report renders
    the partial ``total`` with an ``approximate`` flag.
    """
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
            return WindowUsage(0.0, len(dates))          # ← sentinel, not 0
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
            # Cold key + PG failure → these days are UNKNOWN, not zero.
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


# ── session window (ord only, hourly buckets) ───────────────────────────────

def rehydrate_hours_from_pg(
    supabase: SupabaseClient,
    user_id: str,
    hours: list[datetime],
) -> dict[datetime, float] | None:
    """ONE PG query for all requested hour buckets (ord cost only).

    Returns ``{hour: usd}`` zero-filled, or ``None`` when the query failed
    (callers treat ``None`` as "unknown", never as zero). Sync; callers wrap
    in ``asyncio.to_thread``.
    """
    if not hours:
        return {}
    start = min(hours)
    end = max(hours) + timedelta(hours=1)
    try:
        result = (
            supabase.table("llm_calls")
            .select("created_at,cost_usd")
            .eq("user_id", user_id)
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .execute()
        )
    except Exception as e:
        logger.warning(
            "quota.rehydrate_hours_from_pg(%s..%s) failed: %s", start, end, e
        )
        return None
    out = {h: 0.0 for h in hours}
    for r in (getattr(result, "data", None) or []):
        try:
            ts = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
        except Exception:
            continue
        h = ts.replace(minute=0, second=0, microsecond=0)
        if h in out:
            out[h] += float(r.get("cost_usd") or 0)
    return out


async def session_window(
    redis: AsyncRedis | None,
    supabase: SupabaseClient,
    user_id: str,
    hours: int = SESSION_WINDOW_HOURS,
) -> WindowUsage:
    """Ord spend (USD) over the trailing ``hours`` hourly buckets — the
    rolling session window. Same semantics as :func:`usage_window`: Redis hot
    path, cold buckets rehydrated from llm_calls in one query, unknown hours
    reported via ``missing_days`` (here: missing hours) so the gate can fail
    closed."""
    if hours < 1:
        return WindowUsage(0.0, 0)
    marks = last_n_hours_utc(hours)
    keys = [hour_key(user_id, h) for h in marks]

    raw: list | None = None
    if redis is not None:
        try:
            raw = await redis.mget(keys)
        except Exception as e:
            logger.warning("quota.session_window MGET failed (PG fallback): %s", e)

    if raw is None:  # Redis absent or MGET failed → whole window from PG
        per_hour = await asyncio.to_thread(
            rehydrate_hours_from_pg, supabase, user_id, marks
        )
        if per_hour is None:
            return WindowUsage(0.0, len(marks))
        return WindowUsage(float(sum(per_hour.values())), 0)

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
        per_hour = await asyncio.to_thread(
            rehydrate_hours_from_pg, supabase, user_id,
            [marks[i] for i in cold],
        )
        if per_hour is None:
            return WindowUsage(total, len(cold))
        for i in cold:
            val = per_hour.get(marks[i], 0.0)
            total += val
            try:
                await redis.set(keys[i], val, ex=_HOUR_TTL)  # backfill, best-effort
            except Exception:
                pass
    return WindowUsage(total, 0)


# ── counter writes ──────────────────────────────────────────────────────────

async def incr_today(
    redis: AsyncRedis | None,
    user_id: str,
    meter: Meter,
    amount: float | int,
) -> None:
    """Fire-and-forget. Increments today's bucket by ``amount`` and refreshes
    the TTL. No-op when Redis is unavailable — PG rehydration will catch up on
    the next read."""
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
    """Sync counterpart of incr_today. Used by the sync usage_sink flush
    (settle) so it does not require bridging into an async context."""
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


async def incr_ord(
    redis: AsyncRedis | None,
    user_id: str,
    cost_usd: float,
) -> None:
    """Ord settle: one pipeline incrementing today's daily bucket AND the
    current hourly session bucket. Fire-and-forget; no-op without Redis."""
    if redis is None or not cost_usd:
        return
    dkey = day_key("ord", user_id, today_utc())
    hkey = hour_key(user_id, current_hour_utc())
    try:
        pipe = redis.pipeline()
        pipe.incrbyfloat(dkey, float(cost_usd))
        pipe.expire(dkey, _ttl_for("ord"))
        pipe.incrbyfloat(hkey, float(cost_usd))
        pipe.expire(hkey, _HOUR_TTL)
        await pipe.execute()
    except Exception as e:
        logger.warning("quota.incr_ord failed: %s", e)


def incr_ord_sync(
    redis: SyncRedis | None,
    user_id: str,
    cost_usd: float,
) -> None:
    """Sync counterpart of incr_ord — used by the usage_sink flush."""
    if redis is None or not cost_usd:
        return
    dkey = day_key("ord", user_id, today_utc())
    hkey = hour_key(user_id, current_hour_utc())
    try:
        pipe = redis.pipeline()
        pipe.incrbyfloat(dkey, float(cost_usd))
        pipe.expire(dkey, _ttl_for("ord"))
        pipe.incrbyfloat(hkey, float(cost_usd))
        pipe.expire(hkey, _HOUR_TTL)
        pipe.execute()
    except Exception as e:
        logger.warning("quota.incr_ord_sync failed: %s", e)
