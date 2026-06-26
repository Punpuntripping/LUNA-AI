"""Per-user usage quota gate — subscription-plan + points based.

Currency: 1 USD = 100 points. LLM spend is tracked internally in USD
(llm_calls.cost_usd); limits are defined in points on the ``plans`` catalog
(migration 068/076) and converted at the gate.

Single source of truth:
  * IDENTITY  → the ``user_subscriptions`` row (migration 079): plan_id,
    expires_at, and the per-user *_override columns. plan_id NULL = LOCKED.
  * USAGE     → the ``llm_calls`` ledger, read via the get_user_usage_windows
    RPC. Every window is a plain rolling SUM, so the gate and the dialog
    compute identical numbers from the same source. Redis is no longer on the
    quota path (the old fixed-accumulator drift is gone).

Meters and windows (all ROLLING):

    ord (الاستخدام)  — last 5h *session* (points) + last 7d *weekly* (points)
    ocr (الاستخراج)  — last 30d (pages)

Limits resolve per user:

    user_subscriptions.plan_id → plans row  (NULL = LOCKED → PlanInactive)
    expired time-boxed plan (expires_at in the past) → falls back to ``free``
    per-user override columns (NULL = inherit plan; for dev limit-testing)

A NULL plan limit = unlimited (window not read); 0 = feature not included.

The gate fires once per message, before OCR + router, from
backend.app.services.message_service. The same module exposes a read-only
report consumed by GET /api/v1/usage → the frontend Usage limits dialog.

resets_at for a rolling window = oldest-call-in-window + window-length (the
soonest the used figure drops), not a calendar boundary.

Public API:
    check(redis, supabase, user_id, *, needs_ocr=..., est_ocr_pages=..., ...)
        Raises PlanInactive (no plan assigned) or QuotaExceeded on the first
        failing (meter, period).
    current_usage_report(redis, supabase, user_id) -> dict
        Read-only snapshot: plan block + every meter+period the UI renders.
    settle_ord / settle_ocr / settle_web (async + _sync variants)
        Retained no-op shims — the llm_calls ledger is now authoritative for
        usage, so there is no Redis counter to settle. Kept so existing
        callers (agents.utils.usage_sink) need no import changes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

from shared.quota.redis_store import Meter

logger = logging.getLogger(__name__)

POINTS_PER_USD = 100.0

# Rolling window lengths. Usage is measured directly from the llm_calls ledger
# (the usage SSoT) via the get_user_usage_windows RPC — every window is a plain
# rolling SUM over the trailing interval, so the gate and the dialog always agree.
SESSION_WINDOW_S = 5 * 3_600      # last 5 hours
WEEK_WINDOW_S = 86_400 * 7        # last 7 days
MONTH_WINDOW_S = 86_400 * 30      # last 30 days (ocr meter)


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
_AR_MONTHLY = {
    "ord": "تم تجاوز الحدّ الشهري للاستخدام.",
    "ocr": "تم تجاوز الحدّ الشهري لاستخراج النص.",
    "web": "تم تجاوز الحدّ الشهري للبحث على الإنترنت.",
}


def _arabic_message(meter: Meter, period: Period, limit: float) -> str:
    if limit <= 0:
        return f"باقتك الحالية لا تشمل {_AR_METER.get(meter, meter)}."
    if period == "session":
        return "تم تجاوز حدّ الاستخدام لكل ٥ ساعات."
    if period == "weekly":
        return "تم تجاوز حدّ الاستخدام الأسبوعي (٧ أيام)."
    if period == "monthly":
        return _AR_MONTHLY.get(meter, _AR_MONTHLY["ord"])
    return f"تم تجاوز حدّ {_AR_METER.get(meter, meter)}."


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
    """Resolve the user's effective limits from ``user_subscriptions`` (identity
    SSoT): plan row → expiry fallback to free → per-user overrides. Raises on DB
    failure — ``check`` translates that into QuotaUnavailable (fail closed); the
    report lets it propagate as a 500 so the dialog shows its error state."""
    result = (
        supabase.table("user_subscriptions")
        .select(
            "plan_id,expires_at,"
            "points_monthly_override,points_weekly_override,points_session_override,"
            "ocr_pages_monthly_override,web_calls_monthly_override"
        )
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(result, "data", None) or []
    if not rows:
        # No subscription row — treat as locked rather than open.
        return EffectiveLimits(
            plan_id=None, plan_name_ar=None, expires_at=None, expired=False,
            **_LOCKED,
        )
    row = rows[0]
    plan_id = row.get("plan_id")
    expires_at = row.get("expires_at")

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
        ocr_pages_monthly=_ov("ocr_pages_monthly_override", "ocr_pages_monthly"),
        web_calls_monthly=_ov("web_calls_monthly_override", "web_calls_monthly"),
    )


# ── rolling usage source (llm_calls ledger via RPC) ──────────────────────────

async def _usage_windows(supabase: SupabaseClient, user_id: str) -> dict[str, Any]:
    """One rolling-usage read shared by the gate and the report. Calls the
    get_user_usage_windows RPC (migration 079) → a single indexed scan of the
    user's last 30 days of llm_calls. Returns the RPC row dict; raises on DB
    failure (callers decide fail-closed vs fail-soft)."""
    def _call() -> dict[str, Any]:
        res = supabase.rpc("get_user_usage_windows", {"p_user_id": user_id}).execute()
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else {}
    return await asyncio.to_thread(_call)


def _rolling_reset(oldest_iso: Any, window_seconds: int) -> datetime:
    """When the used figure first drops for a rolling window: the oldest call in
    the window ages out at ``oldest + window_length``. Falls back to
    ``now + window_length`` when the window is empty / the timestamp is unknown."""
    now = datetime.now(timezone.utc)
    if oldest_iso:
        try:
            o = datetime.fromisoformat(str(oldest_iso).replace("Z", "+00:00"))
            r = o + timedelta(seconds=window_seconds)
            if r > now:
                return r
        except Exception:
            pass
    return now + timedelta(seconds=window_seconds)


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
    """Raises ``PlanInactive`` (no plan assigned), ``QuotaExceeded`` on the first
    failing (meter, period), or ``QuotaUnavailable`` when the usage RPC is
    unreachable (fail closed — blocking unknowable spend is the gate's job).

    Ord windows are checked shortest-first (session → weekly) so the user sees
    the soonest-to-recover limit. A NULL plan limit skips the window entirely
    (unlimited). OCR is *projected* (``current + est > limit``); the ord meter is
    checked against current spend only — LLM token cost can't be forecast before
    the call. ``redis``/``est_web_calls`` are kept for call-site compatibility but
    unused: usage now comes solely from the llm_calls ledger, and the gate and
    the dialog read the SAME rolling windows so a block is always what's shown.
    """
    try:
        lim = await asyncio.to_thread(_user_limits, supabase, user_id)
    except Exception as e:
        logger.warning("quota._user_limits failed (fail closed): %s", e)
        raise QuotaUnavailable("ord", "weekly")

    if lim.locked:
        raise PlanInactive()

    # Single rolling-usage read from the ledger, shared with the report.
    try:
        w = await _usage_windows(supabase, user_id)
    except Exception as e:
        logger.warning("quota usage RPC failed (fail closed): %s", e)
        raise QuotaUnavailable("ord", "weekly")

    if needs_ord:
        # Session — rolling last 5 hours.
        if lim.points_session is not None:
            used = float(w.get("session_cost") or 0.0) * POINTS_PER_USD
            if used >= float(lim.points_session):
                resets = _rolling_reset(w.get("session_oldest"), SESSION_WINDOW_S)
                raise QuotaExceeded("ord", "session", used, float(lim.points_session), resets)

        # Weekly — rolling last 7 days.
        if lim.points_weekly is not None:
            used = float(w.get("weekly_cost") or 0.0) * POINTS_PER_USD
            if used >= float(lim.points_weekly):
                resets = _rolling_reset(w.get("weekly_oldest"), WEEK_WINDOW_S)
                raise QuotaExceeded("ord", "weekly", used, float(lim.points_weekly), resets)

    if needs_ocr and lim.ocr_pages_monthly is not None:
        m_limit = int(lim.ocr_pages_monthly)
        ocr_resets = _rolling_reset(w.get("ocr_oldest"), MONTH_WINDOW_S)
        if m_limit <= 0:
            raise QuotaExceeded("ocr", "monthly", 0, 0, ocr_resets)
        used_pages = int(w.get("ocr_pages") or 0)
        if used_pages + est_ocr_pages > m_limit:       # projected overage
            raise QuotaExceeded("ocr", "monthly", used_pages + est_ocr_pages, m_limit, ocr_resets)

    if needs_web and lim.web_calls_monthly is not None and int(lim.web_calls_monthly) <= 0:
        # Internet search is not a live feature — any plan that lists it is 0.
        raise QuotaExceeded("web", "monthly", 0, 0, _rolling_reset(None, MONTH_WINDOW_S))


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
    """Snapshot for the Settings → حدود الاستخدام dialog. Reads the SAME rolling
    windows as the gate (get_user_usage_windows), so what's shown is exactly
    what's enforced — no hidden binding window.

    Fails SOFT on the usage read — if the RPC is unreachable the bars render 0
    with ``"approximate": true`` rather than 500ing. A limits-resolution failure
    still propagates (the dialog has an error state).

    Shape::

        {
          "locked": false,
          "plan": {"plan_id", "name_ar", "expires_at", "expired",
                   "effective_plan_id", "effective_name_ar"} | null,
          "points": {                      # ord meter, in points (1$ = 100)
            "session": {"used", "limit"|null, "pct", "resets_at", "approximate"},
            "weekly":  {...},
            "monthly": null                # retired window — kept null for contract
          },
          "ocr": {"monthly": {...}},       # pages
          "web": {"monthly": null}         # retired feature — kept null for contract
        }

    ``limit: null`` = unlimited; ``limit: 0`` = feature not in the plan.
    ``locked: true`` → plan is null and the bars are omitted (frontend shows the
    activation notice). resets_at is the rolling relief time (oldest + window).
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

    try:
        w = await _usage_windows(supabase, user_id)
        approximate = False
    except Exception as e:
        logger.warning("quota.current_usage_report usage RPC failed (soft): %s", e)
        w, approximate = {}, True

    def _points_bar(used_cost: Any, limit: int | None, oldest: Any, window_s: int) -> dict:
        used = round(float(used_cost or 0.0) * POINTS_PER_USD, 2)
        return {
            "used": used,
            "limit": limit,
            "pct": _pct(used, limit),
            "resets_at": _rolling_reset(oldest, window_s).isoformat(),
            "approximate": approximate,
        }

    def _count_bar(used_pages: Any, limit: int | None, oldest: Any, window_s: int) -> dict:
        used = int(used_pages or 0)
        return {
            "used": used,
            "limit": limit,
            "pct": _pct(used, limit),
            "resets_at": _rolling_reset(oldest, window_s).isoformat(),
            "approximate": approximate,
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
            "session": _points_bar(
                w.get("session_cost"), lim.points_session,
                w.get("session_oldest"), SESSION_WINDOW_S,
            ),
            "weekly": _points_bar(
                w.get("weekly_cost"), lim.points_weekly,
                w.get("weekly_oldest"), WEEK_WINDOW_S,
            ),
            "monthly": None,   # retired window — kept null for the frontend contract
        },
        "ocr": {"monthly": _count_bar(
            w.get("ocr_pages"), lim.ocr_pages_monthly, w.get("ocr_oldest"), MONTH_WINDOW_S,
        )},
        "web": {"monthly": None},   # retired feature — kept null for the frontend contract
    }


# ── settle hooks (retired no-ops) ─────────────────────────────────────────────
# Usage is now read directly from the llm_calls ledger (the SSoT) via the
# get_user_usage_windows RPC, so there is no Redis counter to settle. These shims
# are kept — same signatures — so existing callers (agents.utils.usage_sink) and
# any in-flight imports need no change. Remove once all callers drop the calls.

async def settle_ord(redis: AsyncRedis | None, user_id: str, cost_usd: float) -> None:  # noqa: ARG001
    return None


async def settle_ocr(redis: AsyncRedis | None, user_id: str, pages: int) -> None:  # noqa: ARG001
    return None


async def settle_web(redis: AsyncRedis | None, user_id: str, calls: int = 1) -> None:  # noqa: ARG001
    return None


def settle_ord_sync(user_id: str, cost_usd: float) -> None:  # noqa: ARG001
    return None


def settle_ocr_sync(user_id: str, pages: int) -> None:  # noqa: ARG001
    return None


def settle_web_sync(user_id: str, calls: int = 1) -> None:  # noqa: ARG001
    return None


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
