"""Redis counters + Postgres rehydration for the per-user quota gate.

Key layout (one per meter per UTC day):

    quota:{user_id}:ocr:day:{YYYY-MM-DD}   integer  pages   TTL 31d
    quota:{user_id}:ord:day:{YYYY-MM-DD}   float    USD     TTL 8d
    quota:{user_id}:web:day:{YYYY-MM-DD}   integer  calls   TTL 31d

The daily bucket is the only thing ever written. Every reported window
(daily / weekly / monthly) is the sum of the appropriate trailing buckets.
TTL is one day longer than the longest window the meter is read over so the
rolling sum always sees a complete window.

Redis is the hot path. On Redis miss (cold start, evicted key, brief outage)
we rehydrate the missing day from the llm_calls ledger (the durable cost/pages
source of truth) and write it back so the next read hits Redis again.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)

Meter = Literal["ocr", "ord", "web"]
METERS: tuple[Meter, ...] = ("ocr", "ord", "web")

# Per-meter TTL — one day longer than the longest window we read it over.
# ord is gated on daily + 7-day weekly → 8d TTL.
# ocr + web are gated on the 30-day monthly window → 31d TTL.
_TTL_BY_METER: dict[str, int] = {
    "ord": 86_400 * 8,
    "ocr": 86_400 * 31,
    "web": 86_400 * 31,
}


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


# ── key layout ──────────────────────────────────────────────────────────────

def day_key(meter: Meter, user_id: str, day: date) -> str:
    return f"quota:{user_id}:{meter}:day:{day.isoformat()}"


# ── PG rehydration ──────────────────────────────────────────────────────────

def rehydrate_from_pg(
    supabase: SupabaseClient,
    user_id: str,
    meter: Meter,
    day: date,
) -> float:
    """Sum the PG value for one (user, meter, day). Returns 0.0 on error or
    when the meter has no PG-backed source (e.g. web — future skill)."""
    # PG source of truth = the per-call llm_calls ledger (migration 058). Cost
    # and OCR pages both live there now; agent_runs no longer carries them.
    try:
        if meter == "ord":
            result = (
                supabase.table("llm_calls")
                .select("cost_usd")
                .eq("user_id", user_id)
                .gte("created_at", f"{day.isoformat()}T00:00:00Z")
                .lt("created_at", f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z")
                .execute()
            )
            rows = getattr(result, "data", None) or []
            return float(sum((r.get("cost_usd") or 0) for r in rows))
        if meter == "ocr":
            result = (
                supabase.table("llm_calls")
                .select("pages_used")
                .eq("user_id", user_id)
                .gte("created_at", f"{day.isoformat()}T00:00:00Z")
                .lt("created_at", f"{(day + timedelta(days=1)).isoformat()}T00:00:00Z")
                .execute()
            )
            rows = getattr(result, "data", None) or []
            return float(sum((r.get("pages_used") or 0) for r in rows))
        # web: no PG backing yet (future skill). Treat as zero.
        return 0.0
    except Exception as e:
        logger.warning("quota.rehydrate_from_pg(meter=%s, day=%s) failed: %s", meter, day, e)
        return 0.0


# ── counter reads ──────────────────────────────────────────────────────────

async def usage_window(
    redis: AsyncRedis | None,
    supabase: SupabaseClient,
    user_id: str,
    meter: Meter,
    days: int,
) -> float:
    """Returns the meter's total usage over the last ``days`` UTC days
    (including today). ``days=1`` → today only; ``days=7`` → rolling weekly;
    ``days=30`` → rolling monthly.

    Redis is the source of truth; missing buckets are rehydrated from PG and
    written back. If Redis is unavailable the whole window is read from PG.
    """
    if days < 1:
        return 0.0
    dates = last_n_days_utc(days)
    keys = [day_key(meter, user_id, d) for d in dates]

    if redis is None:
        return float(sum(rehydrate_from_pg(supabase, user_id, meter, d) for d in dates))

    try:
        raw = await redis.mget(keys)
    except Exception as e:
        logger.warning("quota.usage_window MGET failed (PG fallback): %s", e)
        return float(sum(rehydrate_from_pg(supabase, user_id, meter, d) for d in dates))

    ttl = _ttl_for(meter)
    total = 0.0
    for i, v in enumerate(raw):
        if v is None:
            pg_val = rehydrate_from_pg(supabase, user_id, meter, dates[i])
            try:
                await redis.set(keys[i], pg_val, ex=ttl)
            except Exception as e:
                logger.debug("quota.usage_window backfill SET failed: %s", e)
            total += float(pg_val)
        else:
            try:
                total += float(v)
            except (TypeError, ValueError):
                pass

    return total


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
