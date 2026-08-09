"""Subscription lifecycle — cancel renewal, undo, and the settings-dialog state.

Implements `.claude/plans/subscription_cancellation.md`. Deliberately a separate
module from ``payment_service``: nothing here moves money. The money path is
long, load-bearing and covered by its own tests, and 113/119 both established
the same posture — a side concern gets its own call, never an edit to the path
that grants plans.

WHAT CANCELLING MEANS IN WAVE 1 ─────────────────────────────────────────────
Wave 1 sells ONE-TIME purchases, so there is no charge to stop. Cancelling:

  * stamps ``user_subscriptions.renewal_cancelled_at`` — a declarative opt-out
    that the Wave 2 renewal job MUST honour (charge only where it IS NULL);
  * records ONE exit-survey row (``subscription_cancellations``) — the actual
    product value today: why people leave;
  * changes NOTHING about the current term. Access runs to ``expires_at`` and
    then the existing expired→free fallback takes over, exactly as it would
    have anyway.

Which is also why the copy must never promise that an automatic charge has been
stopped — there is none. «لن يُجدَّد اشتراكك» is true today and stays true after
Wave 2 ships.

THE TWO WRITE RULES ─────────────────────────────────────────────────────────
1. **Write the flag ALONE.** ``trg_user_subscriptions_assignment`` is
   BEFORE UPDATE **OF plan_id** and re-derives ``expires_at`` from
   ``plans.duration_days``. An UPDATE that touched both would silently re-stamp
   the user's term (the "set expiry ALONE" trap).
2. **The flag write is the cancellation; the survey row is bookkeeping.** The
   flag goes first and a failed survey insert is logged, never rolled back —
   losing a survey answer is a lost datapoint, losing the opt-out is a promise
   broken to the user.

DB dependency: migration ``120_subscription_cancellation.sql`` — the
``renewal_cancelled_at`` column and the ``subscription_cancellations`` table.
⚠ APPLY 120 BEFORE DEPLOYING THIS: every query below names the new column, and
a backend that ships first answers 42703 on the settings dialog.

Note there is NO ``status`` column on ``user_subscriptions`` (migration 091
dropped it — it exists only on the ``user_subscriptions_live`` VIEW, derived at
read time). Active-ness is derived here from ``expires_at``, exactly as
``payment_service._fetch_subscription`` does.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException, MSG_SERVICE_UNAVAILABLE
from backend.app.services.audit_service import write_audit_log
from shared.db.run import run_db

logger = logging.getLogger(__name__)

# ── The survey ───────────────────────────────────────────────────────────────
# Mirrors the CHECK constraint on subscription_cancellations.reason (120). The
# membership test lives HERE rather than as a Literal on the Pydantic model so
# a bad value answers with the Arabic envelope every other refusal uses, instead
# of FastAPI's English 422 (project rule 5).
CANCEL_REASONS: frozenset[str] = frozenset(
    {"expensive", "no_longer_needed", "something_wrong", "other"}
)

# The comment is optional prose from a text area. Truncated rather than refused:
# somebody who typed a long goodbye should not be told their cancellation
# failed. `text` has no length limit in Postgres — this is about sane storage,
# not validation.
COMMENT_MAX_CHARS = 2000

# Only a subscription bought with money can be cancelled. Code / marketing /
# manual / signup grants expire on their own and renew nothing, so a cancel
# button on them is noise at best and an implied refund promise at worst.
PAID_SOURCE = "payment"

# ── Arabic messages (rule 5) ─────────────────────────────────────────────────

NO_PAID_SUBSCRIPTION_AR = "لا يوجد اشتراك مدفوع فعّال لإلغائه"
ALREADY_CANCELLED_AR = "تم إلغاء تجديد اشتراكك مسبقاً"
NOT_CANCELLED_AR = "لا يوجد إلغاء يمكن التراجع عنه"
TERM_ENDED_AR = "انتهت مدة اشتراكك، لا يمكن التراجع عن الإلغاء"
INVALID_REASON_AR = "يرجى اختيار سبب الإلغاء"


# ═══════════════════════════════════════════════════════════════════════════
# 1. Small helpers
# ═══════════════════════════════════════════════════════════════════════════


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_ts(value: Any) -> Optional[datetime]:
    """PostgREST timestamptz → aware datetime (UTC-assumed). Same parser shape
    as ``payment_service._parse_ts`` — both read the same columns."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _service_unavailable() -> LunaHTTPException:
    return LunaHTTPException(
        status_code=503,
        code=ErrorCode.SERVICE_UNAVAILABLE,
        detail=MSG_SERVICE_UNAVAILABLE,
        headers={"Retry-After": "5"},
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2. DB access — sync helpers, always called through run_db()
#
# Service-role client with an explicit user_id filter in every query: the
# filter is the authorization, exactly as everywhere else in this backend.
# ═══════════════════════════════════════════════════════════════════════════

_SUBSCRIPTION_COLUMNS = "plan_id, source, started_at, expires_at, renewal_cancelled_at"


def _fetch_subscription(supabase: SupabaseClient, user_id: str) -> Optional[dict]:
    # limit(1) rather than maybe_single(), for the reason spelled out in
    # payment_service._fetch_subscription: PostgREST answers 406 for a
    # single-object request matching no row, and a user with no subscription
    # must read as "no plan", not as a 500 on the settings dialog.
    res = (
        supabase.table("user_subscriptions")
        .select(_SUBSCRIPTION_COLUMNS)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _fetch_plan_name(supabase: SupabaseClient, plan_id: Optional[str]) -> Optional[str]:
    """``plans.name_ar`` — the dialog shows the plan by its Arabic name, and the
    catalog is the only place that knows it."""
    if not plan_id:
        return None
    res = (
        supabase.table("plans")
        .select("plan_id, name_ar")
        .eq("plan_id", plan_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return (rows[0] or {}).get("name_ar") if rows else None


def _write_renewal_flag(
    supabase: SupabaseClient, user_id: str, when: Optional[str]
) -> bool:
    """Set (or clear) ``renewal_cancelled_at`` and NOTHING ELSE.

    ``updated_at`` rides along deliberately: the assignment trigger is the only
    thing that maintains it and it does not fire for this write, so without it
    the operator view would show a stale timestamp next to a fresh opt-out.
    Neither column is ``plan_id``, so the trigger stays asleep — which is the
    entire point (see the module docstring, rule 1).

    Returns True when a row was actually written.
    """
    res = (
        supabase.table("user_subscriptions")
        .update({"renewal_cancelled_at": when, "updated_at": _now_iso()})
        .eq("user_id", user_id)
        .execute()
    )
    return bool(getattr(res, "data", None) or [])


def _insert_survey(
    supabase: SupabaseClient,
    user_id: str,
    *,
    plan_id: str,
    reason: str,
    comment: Optional[str],
    expires_at: Optional[str],
) -> Optional[dict]:
    """One append-only row per cancel action.

    ``plan_id`` and ``expires_at_snapshot`` are COPIED rather than resolved
    later: a subsequent grant, refund or expiry rewrites the subscription row,
    and the survey has to keep saying what was true when the answer was given.
    """
    payload = {
        "user_id": user_id,
        "plan_id": plan_id,
        "reason": reason,
        "comment": comment,
        "expires_at_snapshot": expires_at,
    }
    res = supabase.table("subscription_cancellations").insert(payload).execute()
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _revoke_newest_survey(supabase: SupabaseClient, user_id: str) -> Optional[str]:
    """Stamp ``revoked_at`` on the caller's newest un-revoked survey row.

    Newest-un-revoked rather than "all of them": a user who cancelled in March,
    undid it, and cancelled again in August has two true answers on file, and an
    undo today only takes back the August one.
    """
    res = (
        supabase.table("subscription_cancellations")
        .select("id, created_at")
        .eq("user_id", user_id)
        .is_("revoked_at", "null")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    if not rows:
        return None
    row_id = rows[0].get("id")
    if not row_id:
        return None
    (
        supabase.table("subscription_cancellations")
        .update({"revoked_at": _now_iso()})
        .eq("id", row_id)
        .execute()
    )
    return str(row_id)


# ═══════════════════════════════════════════════════════════════════════════
# 3. State — what the settings dialog renders from
# ═══════════════════════════════════════════════════════════════════════════


def _term_is_running(row: Optional[dict]) -> bool:
    """Is this a plan with a future end date?

    A NULL ``expires_at`` (dev / manual non-expiring grant) reads as NOT
    running here even though the plan is live: there is no end date to promise
    the user in «تبقى باقتك فعّالة حتى …», and nothing that could renew. Those
    grants are never ``source='payment'`` anyway, so this only ever matters as
    a second wall.
    """
    if not row or not row.get("plan_id"):
        return False                      # no row, or a locked account
    expires_at = _parse_ts(row.get("expires_at"))
    return expires_at is not None and expires_at > _now()


def _is_cancellable(row: Optional[dict]) -> bool:
    """Is this a PAID subscription with a term still running?

    Describes the SUBSCRIPTION, not the button: a term that has already been
    cancelled stays ``cancellable: true``, because after an undo it can be
    cancelled again. The dialog decides which control to show by reading
    ``renewal_cancelled_at`` alongside this.
    """
    return bool(row and row.get("source") == PAID_SOURCE and _term_is_running(row))


def _state_payload(row: Optional[dict], plan_name_ar: Optional[str]) -> dict:
    return {
        "plan_id": (row or {}).get("plan_id"),
        "plan_name_ar": plan_name_ar,
        "expires_at": (row or {}).get("expires_at"),
        "source": (row or {}).get("source"),
        "cancellable": _is_cancellable(row),
        "renewal_cancelled_at": (row or {}).get("renewal_cancelled_at"),
    }


async def get_subscription(supabase: SupabaseClient, user_id: str) -> dict:
    """Current subscription state for إعدادات الحساب.

    A separate endpoint from ``GET /usage`` on purpose: the quota report has no
    ``source``, and bolting one on would put a money-shaped field on the surface
    every message-send path reads.
    """
    row = await run_db(_fetch_subscription, supabase, user_id)
    plan_name_ar = await run_db(_fetch_plan_name, supabase, (row or {}).get("plan_id"))
    return _state_payload(row, plan_name_ar)


# ═══════════════════════════════════════════════════════════════════════════
# 4. Cancel
# ═══════════════════════════════════════════════════════════════════════════


def _normalize_comment(comment: Optional[str]) -> Optional[str]:
    text = (comment or "").strip()
    if not text:
        return None
    return text[:COMMENT_MAX_CHARS]


async def cancel_renewal(
    supabase: SupabaseClient,
    user_id: str,
    *,
    reason: str,
    comment: Optional[str] = None,
) -> dict:
    """Record the user's opt-out of renewal + their exit-survey answer.

    Guards, in order: a known reason → a paid subscription with a running term →
    not already cancelled. Then the flag, then the survey.

    Returns the same payload as ``get_subscription`` so the dialog can render
    the cancelled state straight from the response.
    """
    if reason not in CANCEL_REASONS:
        raise LunaHTTPException(
            status_code=400, code=ErrorCode.VALIDATION_ERROR, detail=INVALID_REASON_AR
        )

    row = await run_db(_fetch_subscription, supabase, user_id)
    if not _is_cancellable(row):
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.SUBSCRIPTION_NOT_CANCELLABLE,
            detail=NO_PAID_SUBSCRIPTION_AR,
        )
    assert row is not None                      # _is_cancellable proved it

    if row.get("renewal_cancelled_at"):
        # Idempotency is a REFUSAL here, not a silent no-op: a second survey row
        # would double-count one departure in the only data this feature
        # produces.
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.SUBSCRIPTION_ALREADY_CANCELLED,
            detail=ALREADY_CANCELLED_AR,
        )

    cancelled_at = _now_iso()
    try:
        written = await run_db(_write_renewal_flag, supabase, user_id, cancelled_at)
    except Exception as exc:  # noqa: BLE001 — includes 42703 if 120 is unapplied
        logger.exception("cancel: renewal flag write failed for user=%s: %s", user_id, exc)
        raise _service_unavailable()
    if not written:
        logger.error("cancel: renewal flag write matched no row for user=%s", user_id)
        raise _service_unavailable()

    plan_id = str(row.get("plan_id"))
    expires_at = row.get("expires_at")
    comment = _normalize_comment(comment)

    # THE FLAG IS THE CANCELLATION. From here on nothing may raise the user's
    # request: the opt-out they asked for is recorded, and a lost survey answer
    # must not be reported to them as a failed cancellation.
    try:
        await run_db(
            _insert_survey,
            supabase,
            user_id,
            plan_id=plan_id,
            reason=reason,
            comment=comment,
            expires_at=expires_at,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "cancel: SURVEY ROW LOST for user=%s plan=%s reason=%s — the renewal "
            "flag IS set and the cancellation stands; only the answer was not "
            "recorded: %s",
            user_id, plan_id, reason, exc,
        )

    await run_db(
        write_audit_log,
        supabase,
        user_id=user_id,
        action="update",
        resource_type="subscription",
        metadata={
            "event": "renewal_cancelled",
            "plan_id": plan_id,
            "reason": reason,
            "has_comment": comment is not None,
            "expires_at": expires_at,
        },
    )

    logger.info(
        "renewal cancelled: user=%s plan=%s reason=%s expires=%s",
        user_id, plan_id, reason, expires_at,
    )

    plan_name_ar = await run_db(_fetch_plan_name, supabase, plan_id)
    return _state_payload({**row, "renewal_cancelled_at": cancelled_at}, plan_name_ar)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Reactivate (undo)
# ═══════════════════════════════════════════════════════════════════════════


async def reactivate_renewal(supabase: SupabaseClient, user_id: str) -> dict:
    """Undo a cancellation. Free by construction — no money moved either way.

    Guards: the flag is set, and the term has not already ended. Reactivating a
    term that already lapsed would be a lie — the plan is gone and only a new
    purchase brings it back.
    """
    row = await run_db(_fetch_subscription, supabase, user_id)
    if not row or not row.get("renewal_cancelled_at"):
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.SUBSCRIPTION_NOT_CANCELLABLE,
            detail=NOT_CANCELLED_AR,
        )
    if not _term_is_running(row):
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.SUBSCRIPTION_NOT_CANCELLABLE,
            detail=TERM_ENDED_AR,
        )

    try:
        written = await run_db(_write_renewal_flag, supabase, user_id, None)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "reactivate: renewal flag clear failed for user=%s: %s", user_id, exc
        )
        raise _service_unavailable()
    if not written:
        logger.error("reactivate: renewal flag clear matched no row for user=%s", user_id)
        raise _service_unavailable()

    # Best-effort for the same reason the insert is: the user's renewal is back
    # on, which is what they asked for. A survey row left un-revoked is a
    # reporting inaccuracy, not a broken promise.
    try:
        revoked_id = await run_db(_revoke_newest_survey, supabase, user_id)
    except Exception as exc:  # noqa: BLE001
        revoked_id = None
        logger.error(
            "reactivate: could not stamp revoked_at for user=%s (renewal IS back "
            "on): %s", user_id, exc,
        )

    await run_db(
        write_audit_log,
        supabase,
        user_id=user_id,
        action="update",
        resource_type="subscription",
        metadata={
            "event": "renewal_reactivated",
            "plan_id": row.get("plan_id"),
            "survey_row_revoked": revoked_id,
        },
    )

    logger.info(
        "renewal reactivated: user=%s plan=%s survey=%s",
        user_id, row.get("plan_id"), revoked_id,
    )

    plan_name_ar = await run_db(_fetch_plan_name, supabase, row.get("plan_id"))
    return _state_payload({**row, "renewal_cancelled_at": None}, plan_name_ar)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Re-purchase clears the flag (called from the paid-fulfilment path)
# ═══════════════════════════════════════════════════════════════════════════


def clear_renewal_cancellation(supabase: SupabaseClient, user_id: str) -> bool:
    """Buying again IS re-opting in — clear the flag after a successful grant.

    SYNC, and called through ``run_db`` from ``payment_service`` right after
    ``grant_plan`` returns. Two deliberate choices:

    * **Not inside ``grant_plan``.** 113's precedent: the live money-path RPC is
      never edited for a side concern. ``grant_plan``'s ON CONFLICT DO UPDATE
      lists its columns explicitly, so it leaves ``renewal_cancelled_at``
      standing — which is exactly why this call has to exist.
    * **Never raises.** The plan is already granted and the money is already in.
      A stale opt-out flag is a Wave 2 reporting bug; a 500 here would be a
      customer who paid and saw an error.

    The survey rows are deliberately NOT revoked: the user really did cancel,
    and returning later does not un-say why they left. Only an explicit undo
    stamps ``revoked_at``.

    Returns True when a flag was actually cleared (for the log line / tests).
    """
    try:
        row = _fetch_subscription(supabase, user_id)
        if not row or not row.get("renewal_cancelled_at"):
            return False                       # the common case: nothing to do
        _write_renewal_flag(supabase, user_id, None)
        logger.info("renewal opt-out cleared by a new purchase: user=%s", user_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "could not clear renewal_cancelled_at for user=%s after a paid grant "
            "(the plan IS granted; the stale flag would only matter to the Wave 2 "
            "renewal job): %s",
            user_id, exc,
        )
        return False
