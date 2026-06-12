"""Per-user usage quota gate — subscription-plan + points based.

Currency: 1 USD = 100 points. LLM spend is tracked internally in USD
(llm_calls.cost_usd); limits are defined in points on the ``plans`` catalog
(migration 068) and converted at the gate.

Meters and windows:

    ord (نقاط الاستخدام)  — rolling 5h *session* + rolling 7d weekly +
                             rolling 30d monthly (points)
    ocr (الاستخراج)        — rolling-30-day monthly (pages)
    web (البحث)            — rolling-30-day monthly (calls; future skill)

Limits resolve per user:

    users.plan_id → plans row  (NULL plan_id = account LOCKED → PlanInactive)
    expired time-boxed plan (subscription_expires_at in the past) → falls back
        to the ``free`` plan's limits
    per-user override columns (points_*_override, ocr_pages_monthly_limit,
        web_calls_monthly_limit) — NULL = inherit plan; for dev limit-testing

A NULL plan limit = unlimited (the window is not even read); 0 = feature not
included in the plan.

The gate fires once per message, before OCR + router, from
backend.app.services.message_service. The same module exposes a read-only
report consumed by GET /api/v1/usage → the frontend Usage limits dialog.

Public API:
    check(redis, supabase, user_id, *, needs_ocr=..., est_ocr_pages=..., ...)
        Raises PlanInactive (no plan assigned) or QuotaExceeded on the first
        failing (meter, period).
    current_usage_report(redis, supabase, user_id) -> dict
        Read-only snapshot: plan block + every meter+period the UI renders.
    settle_ord / settle_ocr / settle_web (async + _sync variants)
        Fire-and-forget post-hoc counter updates. settle_ord writes BOTH the
        daily bucket (weekly/monthly windows) and the hourly session bucket.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

from shared.quota import redis_store
from shared.quota.redis_store import Meter

logger = logging.getLogger(__name__)

POINTS_PER_USD = 100.0


# ── exceptions ──────────────────────────────────────────────────────────────

Period = str  # "session" | "weekly" | "monthly"


@dataclass
class QuotaExceeded(Exception):
    meter: Meter
    period: Period
    used: float    # ord: points; ocr: pages; web: calls
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
            "message_ar": _arabic_message(self.meter, self.period, self.limit),
        }


PLAN_INACTIVE_AR = "حسابك غير مفعّل بعد. تواصل معنا لتفعيل اشتراكك."


@dataclass
class PlanInactive(Exception):
    """User has no plan assigned (users.plan_id IS NULL) — the account is
    locked until the operator assigns a plan manually in Supabase. Emitted on
    the same ``quota_exceeded`` SSE event as QuotaExceeded so the frontend
    banner renders it without a new code path."""

    def __post_init__(self) -> None:  # dataclass for symmetry with the others
        super().__init__("plan_inactive")

    def to_event_payload(self) -> dict:
        return {
            "meter": "plan",
            "period": "none",
            "used": 0,
            "limit": 0,
            "resets_at": "",
            "message_ar": PLAN_INACTIVE_AR,
        }


_AR_METER = {"ocr": "استخراج النص", "ord": "الاستخدام", "web": "البحث على الإنترنت"}
_AR_PERIOD = {"weekly": "الأسبوعي", "monthly": "الشهري"}


def _arabic_message(meter: Meter, period: Period, limit: float) -> str:
    if limit <= 0:
        return f"باقتك الحالية لا تشمل {_AR_METER.get(meter, meter)}."
    if period == "session":
        return "تم تجاوز حد الجلسة (آخر ٥ ساعات). يتجدد الحد تدريجيًا مع مرور الوقت."
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
    blocking new spend on an unknowable answer is the gate's entire job.
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


# ── plan catalog cache ──────────────────────────────────────────────────────

_PLANS_TTL_S = 300.0
_plans_cache: dict[str, dict] | None = None
_plans_cache_at: float = 0.0


def _load_plans(supabase: SupabaseClient) -> dict[str, dict]:
    """Plan catalog keyed by plan_id, cached in-process for 5 minutes. The
    table is tiny and changes rarely (manual UPDATEs). On refresh failure a
    stale cache is served; with no cache at all the error propagates (callers
    fail closed)."""
    global _plans_cache, _plans_cache_at
    now = time.monotonic()
    if _plans_cache is not None and now - _plans_cache_at < _PLANS_TTL_S:
        return _plans_cache
    try:
        result = supabase.table("plans").select("*").execute()
        rows = getattr(result, "data", None) or []
        if rows:
            _plans_cache = {r["plan_id"]: r for r in rows}
            _plans_cache_at = now
            return _plans_cache
        raise RuntimeError("plans table returned no rows")
    except Exception:
        if _plans_cache is not None:
            logger.warning("quota._load_plans refresh failed — serving stale cache")
            return _plans_cache
        raise


# ── effective limits resolution ─────────────────────────────────────────────

@dataclass
class EffectiveLimits:
    plan_id: str | None            # raw assignment (None = never activated)
    plan_name_ar: str | None
    expires_at: str | None
    expired: bool
    effective_plan_id: str | None  # after expiry fallback; None = locked
    effective_name_ar: str | None
    points_monthly: int | None     # None = unlimited
    points_weekly: int | None
    points_session: int | None
    ocr_pages_monthly: int | None
    web_calls_monthly: int | None

    @property
    def locked(self) -> bool:
        return self.effective_plan_id is None


_LOCKED = dict(
    effective_plan_id=None, effective_name_ar=None,
    points_monthly=0, points_weekly=0, points_session=0,
    ocr_pages_monthly=0, web_calls_monthly=0,
)


def _user_limits(supabase: SupabaseClient, user_id: str) -> EffectiveLimits:
    """Resolve the user's effective limits: plan row → expiry fallback to
    free → per-user overrides. Raises on DB failure — ``check`` translates
    that into QuotaUnavailable (fail closed); the report lets it propagate
    as a 500 so the dialog shows its error state."""
    result = (
        supabase.table("users")
        .select(
            "plan_id,subscription_expires_at,"
            "points_monthly_override,points_weekly_override,points_session_override,"
            "ocr_pages_monthly_limit,web_calls_monthly_limit"
        )
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(result, "data", None) or []
    if not rows:
        # No users row — treat as locked rather than open.
        return EffectiveLimits(
            plan_id=None, plan_name_ar=None, expires_at=None, expired=False,
            **_LOCKED,
        )
    row = rows[0]
    plan_id = row.get("plan_id")
    expires_at = row.get("subscription_expires_at")

    if plan_id is None:
        return EffectiveLimits(
            plan_id=None, plan_name_ar=None, expires_at=None, expired=False,
            **_LOCKED,
        )

    plans = _load_plans(supabase)
    plan = plans.get(plan_id)
    plan_name_ar = (plan or {}).get("name_ar")

    expired = False
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            expired = exp <= datetime.now(timezone.utc)
        except Exception:
            logger.warning("quota: unparseable subscription_expires_at %r", expires_at)

    effective = plans.get("free") if expired else plan
    if effective is None:
        # Assigned plan missing from catalog (or free row deleted) — config
        # error; lock rather than allow unbounded spend.
        logger.error("quota: plan %r not in catalog — treating user as locked", plan_id)
        return EffectiveLimits(
            plan_id=plan_id, plan_name_ar=plan_name_ar,
            expires_at=expires_at, expired=expired, **_LOCKED,
        )

    def _ov(override_col: str, plan_col: str) -> int | None:
        ov = row.get(override_col)
        return ov if ov is not None else effective.get(plan_col)

    return EffectiveLimits(
        plan_id=plan_id,
        plan_name_ar=plan_name_ar,
        expires_at=expires_at,
        expired=expired,
        effective_plan_id=effective["plan_id"],
        effective_name_ar=effective.get("name_ar"),
        points_monthly=_ov("points_monthly_override", "points_monthly"),
        points_weekly=_ov("points_weekly_override", "points_weekly"),
        points_session=_ov("points_session_override", "points_session"),
        ocr_pages_monthly=_ov("ocr_pages_monthly_limit", "ocr_pages_monthly"),
        web_calls_monthly=_ov("web_calls_monthly_limit", "web_calls_monthly"),
    )


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
    """Raises ``PlanInactive`` (no plan assigned), ``QuotaExceeded`` on the
    first failing (meter, period), or ``QuotaUnavailable`` when a window is
    genuinely unknowable and the known partial sum is still under the limit.

    Ord windows are checked shortest-first (session → weekly → monthly) so
    the user sees the soonest-to-recover limit. A NULL plan limit skips the
    window entirely (unlimited — no Redis read). OCR + web checks are
    *projected* (``current + est > limit``); the ord meter is checked against
    current spend only — LLM token cost can't be forecast before the call.

    Policy: a partial sum that ALREADY exceeds the limit is a valid rejection;
    a partial sum under the limit but incomplete is unknowable → closed.
    """
    try:
        lim = await asyncio.to_thread(_user_limits, supabase, user_id)
    except Exception as e:
        logger.warning("quota._user_limits failed (fail closed): %s", e)
        raise QuotaUnavailable("ord", "monthly")

    if lim.locked:
        raise PlanInactive()

    if needs_ord:
        checks: list[tuple[Period, int]] = []
        if lim.points_session is not None:
            checks.append(("session", lim.points_session))
        if lim.points_weekly is not None:
            checks.append(("weekly", lim.points_weekly))
        if lim.points_monthly is not None:
            checks.append(("monthly", lim.points_monthly))
        for period, limit_points in checks:
            if period == "session":
                w = await redis_store.session_window(redis, supabase, user_id)
                resets = redis_store.next_hour_top()
            else:
                days = 7 if period == "weekly" else 30
                w = await redis_store.usage_window(redis, supabase, user_id, "ord", days)
                resets = redis_store.next_utc_midnight()
            used_points = w.total * POINTS_PER_USD
            if used_points >= float(limit_points):    # known overage wins, even if partial
                raise QuotaExceeded("ord", period, used_points, float(limit_points), resets)
            if not w.complete:                        # under limit but unknowable → closed
                raise QuotaUnavailable("ord", period)

    resets_midnight = redis_store.next_utc_midnight()

    if needs_ocr and lim.ocr_pages_monthly is not None:
        m_limit = int(lim.ocr_pages_monthly)
        if m_limit <= 0:
            raise QuotaExceeded("ocr", "monthly", 0, 0, resets_midnight)
        m = await redis_store.usage_window(redis, supabase, user_id, "ocr", 30)
        if m.total + est_ocr_pages > m_limit:         # known overage wins, even if partial
            raise QuotaExceeded("ocr", "monthly", m.total + est_ocr_pages, m_limit, resets_midnight)
        if not m.complete:                            # under limit but unknowable → closed
            raise QuotaUnavailable("ocr", "monthly")

    if needs_web and lim.web_calls_monthly is not None:
        m_limit = int(lim.web_calls_monthly)
        if m_limit <= 0:
            raise QuotaExceeded("web", "monthly", 0, 0, resets_midnight)
        m = await redis_store.usage_window(redis, supabase, user_id, "web", 30)
        if m.total + est_web_calls > m_limit:         # known overage wins, even if partial
            raise QuotaExceeded("web", "monthly", m.total + est_web_calls, m_limit, resets_midnight)
        if not m.complete:                            # under limit but unknowable → closed
            raise QuotaUnavailable("web", "monthly")


# ── read-only snapshot for the UI ───────────────────────────────────────────

def _pct(used: float, limit: float | None) -> int:
    if not limit or limit <= 0:
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
    """Snapshot for the Settings → حدود الاستخدام dialog.

    Fails SOFT on window reads — a partially-determined window renders its
    known ``total`` with ``"approximate": true``. A limits-resolution failure
    propagates (the dialog has an error state for the resulting 500).

    Shape::

        {
          "locked": false,
          "plan": {"plan_id", "name_ar", "expires_at", "expired",
                   "effective_plan_id", "effective_name_ar"} | null,
          "points": {                      # ord meter, in points (1$ = 100)
            "session": {"used", "limit"|null, "pct", "resets_at", "approximate"},
            "weekly":  {...},
            "monthly": {...}
          },
          "ocr": {"monthly": {...}},       # pages
          "web": {"monthly": {...}}        # calls
        }

    ``limit: null`` = unlimited. ``locked: true`` → plan is null and the
    bars are omitted (frontend shows the activation notice).
    """
    lim = await asyncio.to_thread(_user_limits, supabase, user_id)

    if lim.locked:
        return {
            "locked": True,
            "plan": None,
            "points": {"session": None, "weekly": None, "monthly": None},
            "ocr": {"monthly": None},
            "web": {"monthly": None},
        }

    resets_midnight = redis_store.next_utc_midnight().isoformat()
    resets_hour = redis_store.next_hour_top().isoformat()

    ord_s = await redis_store.session_window(redis, supabase, user_id)
    ord_w = await redis_store.usage_window(redis, supabase, user_id, "ord", 7)
    ord_m = await redis_store.usage_window(redis, supabase, user_id, "ord", 30)
    ocr_m = await redis_store.usage_window(redis, supabase, user_id, "ocr", 30)
    web_m = await redis_store.usage_window(redis, supabase, user_id, "web", 30)

    def _points_bar(w, limit: int | None, resets: str) -> dict:
        used = round(w.total * POINTS_PER_USD, 2)
        return {
            "used": used,
            "limit": limit,
            "pct": _pct(used, limit),
            "resets_at": resets,
            "approximate": not w.complete,
        }

    def _count_bar(w, limit: int | None) -> dict:
        return {
            "used": int(w.total),
            "limit": limit,
            "pct": _pct(w.total, limit),
            "resets_at": resets_midnight,
            "approximate": not w.complete,
        }

    return {
        "locked": False,
        "plan": {
            "plan_id": lim.plan_id,
            "name_ar": lim.plan_name_ar,
            "expires_at": lim.expires_at,
            "expired": lim.expired,
            "effective_plan_id": lim.effective_plan_id,
            "effective_name_ar": lim.effective_name_ar,
        },
        "points": {
            "session": _points_bar(ord_s, lim.points_session, resets_hour),
            "weekly":  _points_bar(ord_w, lim.points_weekly, resets_midnight),
            "monthly": _points_bar(ord_m, lim.points_monthly, resets_midnight),
        },
        "ocr": {"monthly": _count_bar(ocr_m, lim.ocr_pages_monthly)},
        "web": {"monthly": _count_bar(web_m, lim.web_calls_monthly)},
    }


# ── settle hooks ────────────────────────────────────────────────────────────

async def settle_ord(redis: AsyncRedis | None, user_id: str, cost_usd: float) -> None:
    if cost_usd:
        await redis_store.incr_ord(redis, user_id, float(cost_usd))


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
        redis_store.incr_ord_sync(get_redis_client(), user_id, float(cost_usd))
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
    "POINTS_PER_USD",
    "QuotaExceeded",
    "QuotaUnavailable",
    "PlanInactive",
    "PLAN_INACTIVE_AR",
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
