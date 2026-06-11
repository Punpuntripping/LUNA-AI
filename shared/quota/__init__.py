"""Per-user usage quota gate.

Three meters, each with its own enforcement window(s):

    ord (الاستهلاك العادي)  — daily AND rolling-7-day weekly (USD)
    ocr (الاستخراج)         — rolling-30-day monthly (pages)
    web (البحث)             — rolling-30-day monthly (calls; future skill)

The gate fires once per message, before OCR + router, from
backend.app.services.message_service. The same module exposes a read-only
report consumed by GET /api/v1/usage → the frontend Usage limits dialog.

Public API:
    check(redis, supabase, user_id, *, needs_ocr=..., est_ocr_pages=..., ...)
        Raises QuotaExceeded on the first failing (meter, period).
    current_usage_report(redis, supabase, user_id) -> dict
        Read-only snapshot: every meter+period the UI renders, with used,
        limit, pct, resets_at.
    settle_ord / settle_ocr / settle_web (async + _sync variants)
        Fire-and-forget post-hoc counter updates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

from shared.quota import redis_store
from shared.quota.redis_store import Meter

logger = logging.getLogger(__name__)


# ── exception ───────────────────────────────────────────────────────────────

Period = str  # "daily" | "weekly" | "monthly"


@dataclass
class QuotaExceeded(Exception):
    meter: Meter
    period: Period
    used: float
    limit: float
    resets_at: datetime

    def __post_init__(self) -> None:
        super().__init__(
            f"quota_exceeded: {self.meter} {self.period} "
            f"({self.used:.4f}/{self.limit:.4f})"
        )

    def to_event_payload(self) -> dict:
        return {
            "meter": self.meter,
            "period": self.period,
            "used": round(float(self.used), 6),
            "limit": round(float(self.limit), 6),
            "resets_at": self.resets_at.isoformat(),
            "message_ar": _arabic_message(self.meter, self.period),
        }


_AR_METER = {"ocr": "استخراج النص", "ord": "الاستهلاك العادي", "web": "البحث على الإنترنت"}
_AR_PERIOD = {"daily": "اليومي", "weekly": "الأسبوعي", "monthly": "الشهري"}


def _arabic_message(meter: Meter, period: Period) -> str:
    m = _AR_METER.get(meter, meter)
    p = _AR_PERIOD.get(period, period)
    return f"تم تجاوز الحد {p} لـ{m}."


# ── fail-closed "unknown" exception ──────────────────────────────────────────

QUOTA_UNAVAILABLE_AR = (
    "تعذّر التحقق من حدود الاستخدام مؤقتًا. الرجاء المحاولة مرة أخرى بعد قليل."
)


@dataclass
class QuotaUnavailable(Exception):
    """Raised by ``check`` when the usage window is genuinely unknowable —
    a cold Redis bucket AND the PG rehydrate fallback failed — and the known
    partial sum does NOT already exceed the limit. The gate fails CLOSED here:
    blocking new spend on an unknowable answer is the gate's entire job. The
    read-only report NEVER raises this (it degrades to an ``approximate`` bar).
    """

    meter: Meter
    period: Period

    def __post_init__(self) -> None:
        super().__init__(f"quota_unavailable: {self.meter} {self.period}")

    def to_event_payload(self) -> dict:
        return {
            "meter": self.meter,
            "period": self.period,
            "message_ar": QUOTA_UNAVAILABLE_AR,
        }


# ── user limits ─────────────────────────────────────────────────────────────

_DEFAULT_LIMITS = {
    "ord_cost_daily_limit_usd":  1.0,
    "ord_cost_weekly_limit_usd": 5.0,
    "ocr_pages_monthly_limit":   600,
    "web_calls_monthly_limit":   300,
}


def _user_limits(supabase: SupabaseClient, user_id: str) -> dict:
    """Read the four quota columns from the users row. Returns defaults on
    failure so the gate behaves predictably even mid-incident."""
    try:
        result = (
            supabase.table("users")
            .select(
                "ord_cost_daily_limit_usd,ord_cost_weekly_limit_usd,"
                "ocr_pages_monthly_limit,web_calls_monthly_limit"
            )
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = getattr(result, "data", None) or []
        if rows:
            merged = {**_DEFAULT_LIMITS, **{k: v for k, v in rows[0].items() if v is not None}}
            return merged
    except Exception as e:
        logger.warning("quota._user_limits failed (using defaults): %s", e)
    return dict(_DEFAULT_LIMITS)


# ── the gate ────────────────────────────────────────────────────────────────

async def check(
    redis: AsyncRedis | None,
    supabase: SupabaseClient,
    user_id: str,
    *,
    needs_ocr: bool = False,
    est_ocr_pages: int = 0,
    needs_ord: bool = True,
    needs_web: bool = False,
    est_web_calls: int = 0,
) -> None:
    """Raises ``QuotaExceeded`` on the first failing (meter, period), or
    ``QuotaUnavailable`` when a window is genuinely unknowable and the known
    partial sum is still under the limit (fail closed on the unknowable answer).

    OCR + web checks are *projected* (``current + est > limit``) because the
    cost is known up front (page count, call count). The ordinary meter is
    checked against current spend only (``current >= limit``) — LLM token
    cost can't be forecast before the call fires.

    Policy: a partial sum that ALREADY exceeds the limit is a valid rejection
    (we know enough → ``QuotaExceeded``); a partial sum that is under the limit
    but incomplete is unknowable → ``QuotaUnavailable``. Both wins are taken
    before the completeness check so a known overage is never masked by a 503.
    """
    limits = _user_limits(supabase, user_id)
    resets = redis_store.next_utc_midnight()

    if needs_ord:
        d = await redis_store.usage_window(redis, supabase, user_id, "ord", 1)
        w = await redis_store.usage_window(redis, supabase, user_id, "ord", 7)
        d_limit = float(limits.get("ord_cost_daily_limit_usd") or 0)
        w_limit = float(limits.get("ord_cost_weekly_limit_usd") or 0)
        if d.total >= d_limit:                      # known overage wins, even if partial
            raise QuotaExceeded("ord", "daily", d.total, d_limit, resets)
        if w.total >= w_limit:
            raise QuotaExceeded("ord", "weekly", w.total, w_limit, resets)
        if not d.complete or not w.complete:        # under limit but unknowable → closed
            raise QuotaUnavailable("ord", "daily" if not d.complete else "weekly")

    if needs_ocr:
        m = await redis_store.usage_window(redis, supabase, user_id, "ocr", 30)
        m_limit = int(limits.get("ocr_pages_monthly_limit") or 0)
        if m.total + est_ocr_pages > m_limit:       # known overage wins, even if partial
            raise QuotaExceeded("ocr", "monthly", m.total + est_ocr_pages, m_limit, resets)
        if not m.complete:                          # under limit but unknowable → closed
            raise QuotaUnavailable("ocr", "monthly")

    if needs_web:
        m = await redis_store.usage_window(redis, supabase, user_id, "web", 30)
        m_limit = int(limits.get("web_calls_monthly_limit") or 0)
        if m.total + est_web_calls > m_limit:       # known overage wins, even if partial
            raise QuotaExceeded("web", "monthly", m.total + est_web_calls, m_limit, resets)
        if not m.complete:                          # under limit but unknowable → closed
            raise QuotaUnavailable("web", "monthly")


# ── read-only snapshot for the UI ───────────────────────────────────────────

def _pct(used: float, limit: float) -> int:
    if limit <= 0:
        return 0
    p = (used / limit) * 100.0
    if p < 0:
        return 0
    if p > 100:
        return 100
    return int(round(p))


async def current_usage_report(
    redis: AsyncRedis | None,
    supabase: SupabaseClient,
    user_id: str,
) -> dict[str, Any]:
    """Snapshot the four bars rendered by the Usage limits dialog.

    NEVER raises — unlike ``check``, the report fails SOFT: a window that could
    only be partially determined renders its known ``total`` with
    ``"approximate": true`` so the dialog still shows the user something rather
    than a 503.

    Shape::

        {
          "ord": {
            "daily":  {"used": 0.25, "limit": 1.0, "pct": 25,
                       "resets_at": "...", "approximate": false},
            "weekly": {...}
          },
          "ocr":   {"monthly": {"used": 240, "limit": 600, "pct": 40,
                                "resets_at": "...", "approximate": false}},
          "web":   {"monthly": {...}}
        }
    """
    limits = _user_limits(supabase, user_id)
    resets = redis_store.next_utc_midnight().isoformat()

    ord_d = await redis_store.usage_window(redis, supabase, user_id, "ord", 1)
    ord_w = await redis_store.usage_window(redis, supabase, user_id, "ord", 7)
    ocr_m = await redis_store.usage_window(redis, supabase, user_id, "ocr", 30)
    web_m = await redis_store.usage_window(redis, supabase, user_id, "web", 30)

    ord_d_limit = float(limits["ord_cost_daily_limit_usd"])
    ord_w_limit = float(limits["ord_cost_weekly_limit_usd"])
    ocr_m_limit = int(limits["ocr_pages_monthly_limit"])
    web_m_limit = int(limits["web_calls_monthly_limit"])

    return {
        "ord": {
            "daily":  {"used": round(ord_d.total, 6), "limit": ord_d_limit,
                       "pct": _pct(ord_d.total, ord_d_limit), "resets_at": resets,
                       "approximate": not ord_d.complete},
            "weekly": {"used": round(ord_w.total, 6), "limit": ord_w_limit,
                       "pct": _pct(ord_w.total, ord_w_limit), "resets_at": resets,
                       "approximate": not ord_w.complete},
        },
        "ocr": {
            "monthly": {"used": int(ocr_m.total), "limit": ocr_m_limit,
                        "pct": _pct(ocr_m.total, ocr_m_limit), "resets_at": resets,
                        "approximate": not ocr_m.complete},
        },
        "web": {
            "monthly": {"used": int(web_m.total), "limit": web_m_limit,
                        "pct": _pct(web_m.total, web_m_limit), "resets_at": resets,
                        "approximate": not web_m.complete},
        },
    }


# ── settle hooks ────────────────────────────────────────────────────────────

async def settle_ord(redis: AsyncRedis | None, user_id: str, cost_usd: float) -> None:
    if cost_usd:
        await redis_store.incr_today(redis, user_id, "ord", float(cost_usd))


async def settle_ocr(redis: AsyncRedis | None, user_id: str, pages: int) -> None:
    if pages:
        await redis_store.incr_today(redis, user_id, "ocr", int(pages))


async def settle_web(redis: AsyncRedis | None, user_id: str, calls: int = 1) -> None:
    if calls:
        await redis_store.incr_today(redis, user_id, "web", int(calls))


def settle_ord_sync(user_id: str, cost_usd: float) -> None:
    if not cost_usd:
        return
    try:
        from shared.cache.redis import get_redis_client
        redis_store.incr_today_sync(get_redis_client(), user_id, "ord", float(cost_usd))
    except Exception as e:
        logger.debug("quota.settle_ord_sync failed: %s", e)


def settle_ocr_sync(user_id: str, pages: int) -> None:
    if not pages:
        return
    try:
        from shared.cache.redis import get_redis_client
        redis_store.incr_today_sync(get_redis_client(), user_id, "ocr", int(pages))
    except Exception as e:
        logger.debug("quota.settle_ocr_sync failed: %s", e)


def settle_web_sync(user_id: str, calls: int = 1) -> None:
    if not calls:
        return
    try:
        from shared.cache.redis import get_redis_client
        redis_store.incr_today_sync(get_redis_client(), user_id, "web", int(calls))
    except Exception as e:
        logger.debug("quota.settle_web_sync failed: %s", e)


__all__ = [
    "QuotaExceeded",
    "QuotaUnavailable",
    "QUOTA_UNAVAILABLE_AR",
    "check",
    "current_usage_report",
    "settle_ord",
    "settle_ocr",
    "settle_web",
    "settle_ord_sync",
    "settle_ocr_sync",
    "settle_web_sync",
]
