"""Per-user usage quota gate — subscription-plan + points based.

Currency: 1 USD = 100 points. LLM spend is tracked internally in USD
(llm_calls.cost_usd); limits are defined in points on the ``plans`` catalog
(migration 068/076) and converted at the gate.

Single source of truth — the ``get_user_quota_state`` RPC (migration 093).
One call returns the whole quota picture, computed server-side:

  * IDENTITY  → the ``user_subscriptions`` row: plan_id, expires_at, and the
    per-user *_override columns. plan_id NULL (or no row) = LOCKED. An expired
    time-boxed plan falls back to the ``free`` plan's limits — the fallback and
    the override resolution happen inside the RPC, so there is exactly one
    definition of "effective limits" (SQL), shared with the operator view
    ``user_subscriptions_live``.
  * USAGE     → the ``llm_calls`` ledger. Every window is derived at read time
    (never materialized — stored counters drift; that was the pre-079 Redis
    bug), so the gate, the dialog, and the operator view always agree. Redis
    is no longer on the quota path.

Meters and windows:

    ord (الاستخدام)  — fixed 5h *session* block anchored at the first message
                       (migration 083; points) + rolling last-7d *weekly* (points)
    ocr (الاستخراج)  — rolling last-30d (pages)
    library (فتح المصادر)
                     — per-period allowance, counted as SUM(library_unlocks.cost)
                       for the CURRENT period_key. Both the key and its reset
                       instant come from the RPC (migration 105) — never
                       re-derived in Python. Read via ``library_state()``;
                       enforced by backend.app.services.library_service
                       (Layer B), not by ``check()``.

A NULL limit = unlimited (window not enforced); 0 = feature not included.

The gate fires once per message, before OCR + router, from
backend.app.services.message_service. The same module exposes a read-only
report consumed by GET /api/v1/usage → the frontend Usage limits dialog. Both
read the SAME RPC row, so what's shown is exactly what's enforced.

resets_at for a window = oldest-call/anchor + window-length (the soonest the
used figure drops), not a calendar boundary.

Public API:
    check(redis, supabase, user_id, *, needs_ocr=..., est_ocr_pages=..., ...)
        Raises PlanInactive (no plan assigned) or QuotaExceeded on the first
        failing (meter, period).
    current_usage_report(redis, supabase, user_id) -> dict
        Read-only snapshot: plan block + every meter+period the UI renders.
    settle_ord / settle_ocr / settle_web (async + _sync variants)
        Retained no-op shims — the llm_calls ledger is authoritative for
        usage, so there is no Redis counter to settle. Kept so existing
        callers (agents.utils.usage_sink) need no import changes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from redis.asyncio import Redis as AsyncRedis
from supabase import Client as SupabaseClient

from shared.quota.redis_store import Meter

logger = logging.getLogger(__name__)

POINTS_PER_USD = 100.0

# Window lengths. Usage comes from the llm_calls ledger via the
# get_user_quota_state RPC (one row: identity + effective limits + windows).
# The session is a FIXED 5h block anchored at the first message (migration
# 083 — session_oldest carries the active anchor); weekly + ocr are plain
# rolling SUMs over their trailing interval. ``resets_at`` = oldest/anchor +
# window in either case.
SESSION_WINDOW_S = 5 * 3_600      # fixed 5h session block (anchor + 5h)
WEEK_WINDOW_S = 86_400 * 7        # rolling last 7 days
MONTH_WINDOW_S = 86_400 * 30      # rolling last 30 days (ocr meter)


# ── exceptions ──────────────────────────────────────────────────────────────

Period = str  # "session" | "weekly" | "monthly"


@dataclass
class QuotaExceeded(Exception):
    meter: Meter
    period: Period
    used: float    # ord: points; ocr: pages; web: calls
    limit: float
    resets_at: datetime
    # The plan the block was enforced against — the EFFECTIVE one, so an expired
    # paid subscription that fell back to `free` reports "free". The frontend
    # keys the upgrade dialog off this: only a free-plan block offers plans to
    # buy; a paid user who ran their window down gets the reset banner instead.
    plan_id: str | None = None

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
            "plan_id": self.plan_id,
        }


PLAN_INACTIVE_AR = "حسابك غير مفعّل بعد. تواصل معنا لتفعيل اشتراكك."


@dataclass
class PlanInactive(Exception):
    """User has no plan assigned (no subscription row, plan_id NULL, or the
    assigned plan is missing from the catalog) — the account is locked until
    the operator assigns a plan. Emitted on the same ``quota_exceeded`` SSE
    event as QuotaExceeded so the frontend banner renders it without a new
    code path."""

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
            # No plan assigned at all — deliberately NOT "free". An unactivated
            # account is an operator problem, not something buying a plan fixes,
            # so this must never open the upgrade dialog.
            "plan_id": None,
        }


_AR_METER = {
    "ocr": "استخراج النص",
    "ord": "الاستخدام",
    "web": "البحث على الإنترنت",
    "library": "فتح المصادر",
}
_AR_MONTHLY = {
    "ord": "تم تجاوز الحدّ الشهري للاستخدام.",
    "ocr": "تم تجاوز الحدّ الشهري لاستخراج النص.",
    "web": "تم تجاوز الحدّ الشهري للبحث على الإنترنت.",
}

# The library meter's single period is the subscription/calendar window carried
# on library_unlocks.period_key (D8/D9) — one label, hence period == "period".
LIBRARY_QUOTA_EXHAUSTED_AR = "تم استهلاك رصيد فتح المصادر لهذه الفترة."


def _arabic_message(meter: Meter, period: Period, limit: float) -> str:
    if limit <= 0:
        return f"باقتك الحالية لا تشمل {_AR_METER.get(meter, meter)}."
    if meter == "library":
        return LIBRARY_QUOTA_EXHAUSTED_AR
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
    """Raised by ``check`` when the quota state is genuinely unknowable — the
    get_user_quota_state RPC failed. The gate fails CLOSED here: blocking new
    spend on an unknowable answer is the gate's entire job.
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


# ── the single quota-state read (RPC, migration 093) ─────────────────────────

async def _quota_state(supabase: SupabaseClient, user_id: str) -> dict[str, Any] | None:
    """One read shared by the gate and the report (and, server-side, the
    ``user_subscriptions_live`` operator view): the get_user_quota_state RPC
    returns plan identity, EFFECTIVE limits (expired→free fallback + overrides
    already applied), and the rolling usage windows in a single row. Returns
    None when the user has no subscription row (treated as locked). Raises on
    DB failure — callers decide fail-closed (gate) vs propagate (report →
    dialog error state)."""
    def _call() -> dict[str, Any] | None:
        res = supabase.rpc("get_user_quota_state", {"p_user_id": user_id}).execute()
        rows = getattr(res, "data", None) or []
        return rows[0] if rows else None
    return await asyncio.to_thread(_call)


def _rolling_reset(oldest_iso: Any, window_seconds: int) -> datetime:
    """When the used figure first drops for a window: the oldest call in the
    window (or the session anchor) ages out at ``oldest + window_length``.
    Falls back to ``now + window_length`` when the window is empty / the
    timestamp is unknown."""
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


def _parse_ts(value: Any) -> datetime | None:
    """Parse a PostgREST timestamptz string into an aware datetime, or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


# ── the library meter (فتح المصادر) ──────────────────────────────────────────
#
# Unlike ord/ocr this meter is NOT a rolling window over llm_calls: it is a
# per-period allowance counted as SUM(library_unlocks.cost) for the CURRENT
# period_key. Both the key and its reset instant are derived in SQL (migration
# 105) precisely so Python never re-derives a period boundary and drifts from
# what the ledger rows were stamped with. Read them off the RPC row; never
# recompute them here.


@dataclass
class LibraryQuotaState:
    """The library allowance for one user, as of one RPC read.

    ``limit``      — None = unlimited (dev); 0 = the account is locked/not
                     entitled. Never negative.
    ``used``       — SUM(cost) already charged to ``period_key``.
    ``period_key`` — the period a NEW unlock would be stamped with. None means
                     there is no chargeable period (locked account) — a ledger
                     row cannot even be written (period_key is NOT NULL).
    ``is_paid``    — the §1.2 access predicate's first clause: the EFFECTIVE
                     plan (post expired→free fallback) is not free/None.
    """

    limit: Optional[int]
    used: int
    period_key: Optional[str]
    resets_at: Optional[datetime]
    effective_plan_id: Optional[str]
    locked: bool
    is_paid: bool

    @property
    def remaining(self) -> Optional[int]:
        """Unlocks left this period; ``None`` when unlimited."""
        if self.limit is None:
            return None
        return max(0, int(self.limit) - int(self.used))

    def has_room(self, cost: int) -> bool:
        """Whether a charge of ``cost`` fits in the current period.

        Unlimited (``limit is None``) always fits. A locked account never does
        — it has no period_key to stamp a row with.
        """
        if self.locked or self.period_key is None:
            return False
        if self.limit is None:
            return True
        return int(self.used) + max(0, int(cost)) <= int(self.limit)


def _library_state_from_row(st: dict[str, Any] | None) -> LibraryQuotaState:
    """Project a get_user_quota_state row onto the library meter. Pure."""
    if st is None or st.get("locked"):
        return LibraryQuotaState(
            limit=0, used=0, period_key=None, resets_at=None,
            effective_plan_id=None, locked=True, is_paid=False,
        )
    raw_limit = st.get("library_unlocks_limit")
    limit = None if raw_limit is None else max(0, int(raw_limit))
    period_key = st.get("library_period_key") or None
    effective_plan_id = st.get("effective_plan_id") or None
    return LibraryQuotaState(
        limit=limit,
        used=int(st.get("library_unlocks_used") or 0),
        period_key=period_key,
        resets_at=_parse_ts(st.get("library_period_resets_at")),
        effective_plan_id=effective_plan_id,
        # No period_key => nothing can be charged; treat it as locked rather
        # than as "unlimited free unlocks".
        locked=period_key is None,
        is_paid=effective_plan_id not in (None, "free"),
    )


async def library_state(supabase: SupabaseClient, user_id: str) -> LibraryQuotaState:
    """The user's current library allowance — ONE read of the same RPC row the
    points/OCR gate and the usage dialog use. Raises on DB failure (the caller
    decides: Layer B refuses the reveal rather than granting it for free)."""
    return _library_state_from_row(await _quota_state(supabase, user_id))


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
    failing (meter, period), or ``QuotaUnavailable`` when the quota-state RPC is
    unreachable (fail closed — blocking unknowable spend is the gate's job).

    Ord windows are checked shortest-first (session → weekly) so the user sees
    the soonest-to-recover limit. A NULL limit skips the window entirely
    (unlimited). OCR is *projected* (``current + est > limit``); the ord meter is
    checked against current spend only — LLM token cost can't be forecast before
    the call. ``redis``/``est_web_calls`` are kept for call-site compatibility but
    unused: the gate and the dialog read the SAME RPC row, so a block is always
    what's shown.
    """
    try:
        st = await _quota_state(supabase, user_id)
    except Exception as e:
        logger.warning("quota state RPC failed (fail closed): %s", e)
        raise QuotaUnavailable("ord", "weekly")

    if st is None or st.get("locked"):
        raise PlanInactive()

    # The plan actually being enforced. `effective_plan_id` is what the RPC
    # resolves after expiry fallback, so an expired `pro` reports `free` — which
    # is exactly what the upgrade dialog needs to key on.
    plan = st.get("effective_plan_id") or st.get("plan_id")

    if needs_ord:
        # Session — fixed 5h block anchored at the first message (migration 083).
        if st.get("points_session") is not None:
            used = float(st.get("session_cost") or 0.0) * POINTS_PER_USD
            if used >= float(st["points_session"]):
                resets = _rolling_reset(st.get("session_oldest"), SESSION_WINDOW_S)
                raise QuotaExceeded(
                    "ord", "session", used, float(st["points_session"]), resets, plan
                )

        # Weekly — rolling last 7 days.
        if st.get("points_weekly") is not None:
            used = float(st.get("weekly_cost") or 0.0) * POINTS_PER_USD
            if used >= float(st["points_weekly"]):
                resets = _rolling_reset(st.get("weekly_oldest"), WEEK_WINDOW_S)
                raise QuotaExceeded(
                    "ord", "weekly", used, float(st["points_weekly"]), resets, plan
                )

    if needs_ocr and st.get("ocr_pages_monthly") is not None:
        m_limit = int(st["ocr_pages_monthly"])
        ocr_resets = _rolling_reset(st.get("ocr_oldest"), MONTH_WINDOW_S)
        if m_limit <= 0:
            raise QuotaExceeded("ocr", "monthly", 0, 0, ocr_resets, plan)
        used_pages = int(st.get("ocr_pages") or 0)
        if used_pages + est_ocr_pages > m_limit:       # projected overage
            raise QuotaExceeded(
                "ocr", "monthly", used_pages + est_ocr_pages, m_limit, ocr_resets, plan
            )

    if needs_web and st.get("web_calls_monthly") is not None and int(st["web_calls_monthly"]) <= 0:
        # Internet search is not a live feature — any plan that lists it is 0.
        raise QuotaExceeded(
            "web", "monthly", 0, 0, _rolling_reset(None, MONTH_WINDOW_S), plan
        )


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
    """Snapshot for the Settings → حدود الاستخدام dialog. Reads the SAME RPC row
    as the gate (get_user_quota_state), so what's shown is exactly what's
    enforced — no hidden binding window.

    A failed read propagates as a 500 (the dialog has an error state). With a
    single source there is no partial "limits without usage" render anymore —
    the old two-read soft path went away with the second read. ``approximate``
    is kept in the payload (always False) for the UsageReport contract.

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
          "web": {"monthly": null},        # retired feature — kept null for contract
          "library": {"period": {...}}     # فتح المصادر — unlocks, weighted cost
        }

    ``limit: null`` = unlimited; ``limit: 0`` = feature not in the plan.
    ``locked: true`` → plan is null and the bars are omitted (frontend shows the
    activation notice). resets_at = oldest/anchor + window, or null when the
    window has no usage (used == 0 → fully available, no countdown).
    """
    st = await _quota_state(supabase, user_id)

    if st is None or st.get("locked"):
        return {
            "locked": True,
            "plan": None,
            "points": {"session": None, "weekly": None, "monthly": None},
            "ocr": {"monthly": None},
            "web": {"monthly": None},
            "library": {"period": None},
        }

    def _points_bar(used_cost: Any, limit: int | None, oldest: Any, window_s: int) -> dict:
        used = round(float(used_cost or 0.0) * POINTS_PER_USD, 2)
        return {
            "used": used,
            "limit": limit,
            "pct": _pct(used, limit),
            # Nothing spent in the window → full quota is available now, so there
            # is no recovery time to show. Emitting a bogus ``now + window`` reset
            # here would also be re-diffed against the (untrusted) client clock and
            # can render wildly wrong on a skewed device — so send null and let the
            # UI say "fully available".
            "resets_at": _rolling_reset(oldest, window_s).isoformat() if used > 0 else None,
            "approximate": False,
        }

    def _count_bar(used_pages: Any, limit: int | None, oldest: Any, window_s: int) -> dict:
        used = int(used_pages or 0)
        return {
            "used": used,
            "limit": limit,
            "pct": _pct(used, limit),
            # See _points_bar: no usage → "fully available", no countdown.
            "resets_at": _rolling_reset(oldest, window_s).isoformat() if used > 0 else None,
            "approximate": False,
        }

    lib = _library_state_from_row(st)
    library_bar = {
        "used": lib.used,
        "limit": lib.limit,
        "pct": _pct(lib.used, lib.limit),
        # DELIBERATELY unlike _points_bar / _count_bar: the library period is a
        # FIXED calendar/subscription window (D8), not a rolling window anchored
        # on first use, so its reset instant is meaningful at zero usage — it is
        # what «يتجدّد رصيدك …» renders before the user has spent anything.
        "resets_at": lib.resets_at.isoformat() if lib.resets_at else None,
        "approximate": False,
    }

    return {
        "locked": False,
        "plan": {
            "plan_id": st.get("plan_id"),
            "name_ar": st.get("plan_name_ar"),
            "expires_at": st.get("expires_at"),
            "expired": bool(st.get("is_expired")),
            "effective_plan_id": st.get("effective_plan_id"),
            "effective_name_ar": st.get("effective_name_ar"),
        },
        "points": {
            "session": _points_bar(
                st.get("session_cost"), st.get("points_session"),
                st.get("session_oldest"), SESSION_WINDOW_S,
            ),
            "weekly": _points_bar(
                st.get("weekly_cost"), st.get("points_weekly"),
                st.get("weekly_oldest"), WEEK_WINDOW_S,
            ),
            "monthly": None,   # retired window — kept null for the frontend contract
        },
        "ocr": {"monthly": _count_bar(
            st.get("ocr_pages"), st.get("ocr_pages_monthly"), st.get("ocr_oldest"), MONTH_WINDOW_S,
        )},
        "web": {"monthly": None},   # retired feature — kept null for the frontend contract
        "library": {"period": library_bar},
    }


# ── settle hooks (retired no-ops) ─────────────────────────────────────────────
# Usage is read directly from the llm_calls ledger (the SSoT) via the
# get_user_quota_state RPC, so there is no Redis counter to settle. These shims
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
    "LIBRARY_QUOTA_EXHAUSTED_AR",
    "LibraryQuotaState",
    "library_state",
    "check",
    "current_usage_report",
    "settle_ord",
    "settle_ocr",
    "settle_web",
    "settle_ord_sync",
    "settle_ocr_sync",
    "settle_web_sync",
]
