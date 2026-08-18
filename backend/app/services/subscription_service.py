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

المشتركون الأوائل (migration ``138_early_adopters.sql``) ────────────────────
Section 7 owns the four service-role RPCs that move an early-adopter SEAT, and
every other module reaches them through it — ``payment_service`` claims on the
paid path and releases on a refund, exactly the way it already calls
``clear_renewal_cancellation``. They live here rather than next to the money
because a seat is subscription bookkeeping: it decides what the NEXT charge
costs, never what this one did.

Two rules from `.claude/plans/early_adopters.md` are enforced by the call sites
in this module:

* **cancelling forfeits the price** (§1.6) — the seat is released AFTER the flag
  write, best-effort, because the flag IS the cancellation (rule 2 above) and a
  seat-bookkeeping failure must never surface as a failed cancel;
* **undo restores it unconditionally** — ``reactivate_renewal`` mirrors the
  release with a restore, under the same posture.

Every wrapper here is written to be UNAPPLIED-MIGRATION SAFE: a missing RPC is
caught, logged and answered as "no seat", so a backend that lands ahead of 138
sells and cancels exactly as it does today.

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

# Plans that never represent a customer purchase, whatever `source` says. `dev`
# is an internal grant AND a plan the owner runs real test checkouts against —
# both of which must stay invisible to anything that reacts to "this account
# just bought in". See `resolve_paid_activated_at`.
NON_CUSTOMER_PLAN_IDS: frozenset[str] = frozenset({"free", "dev"})

# ── المشتركون الأوائل (early_adopters.md) ────────────────────────────────────
# A seat is a pro/max concern: `basic` is discounted for EVERYONE while seats
# remain and enrols nobody (§1.9), so a basic purchase must never claim a seat
# and — the case that actually bites — a basic refund or a basic cancellation
# must never RELEASE the pro seat the same user is still holding. Every wrapper
# in section 7 self-guards on this set, so no call site has to remember.
EARLY_ADOPTER_PLAN_IDS = frozenset({"pro", "max"})

# `early_adopter_seats.release_reason` CHECK (138). Named so the two callers
# cannot drift into a string the constraint rejects.
RELEASE_REASON_REFUND = "refund"
RELEASE_REASON_CANCELLED = "cancelled"

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


def resolve_paid_activated_at(row: Optional[dict]) -> Optional[datetime]:
    """WHEN this account last activated a paid plan **by paying for it** — or
    None, which is the answer for the overwhelming majority of accounts.

    Read by ``GET /auth/me`` and, through it, by the «اتعرف على ريحان» intro
    tour: the tour is meant to arrive right after a purchase (A2 in
    `.claude/plans/edu_series.md` §8), and the frontend cannot decide that on
    ``plan_id`` alone. It used to try — ``plan_id !== "free"`` — which made
    every dev grant, every comp and every long-since-expired term read as
    "just paid", so the tour opened for internal test accounts on sight and
    re-opened for paying customers who had held their plan for months.

    Three walls, all of which must hold:

    * ``source == 'payment'`` — money actually moved. Deliberately the SAME
      constant the cancel flow gates on: code / marketing / manual / signup
      grants are not purchases there and are not purchases here either.
    * the plan is a customer plan — ``dev`` is excluded by name, because the
      owner does test the real checkout against it and a test purchase must
      not look like a customer's first buy.
    * the term is still running (``_term_is_running``) — an expired paid plan
      has already fallen back to free everywhere that enforces quota, so it
      must not read as paid here either.

    The timestamp is ``GREATEST(started_at, usage_reset_at)``: the same anchor
    ``get_user_quota_state`` uses (137). ``grant_plan`` PRESERVES ``started_at``
    when it extends a live same-plan term, so a renewal moves only
    ``usage_reset_at`` — stamped with the payment's ``paid_at``. Taking the
    later of the two makes this "the last time money landed on this
    subscription" rather than "the first time".
    """
    if not row or row.get("source") != PAID_SOURCE:
        return None
    if row.get("plan_id") in NON_CUSTOMER_PLAN_IDS:
        return None
    if not _term_is_running(row):
        return None
    candidates = [
        ts
        for ts in (_parse_ts(row.get("started_at")), _parse_ts(row.get("usage_reset_at")))
        if ts is not None
    ]
    return max(candidates) if candidates else None


def _state_payload(
    row: Optional[dict],
    plan_name_ar: Optional[str],
    early_adopter: Optional[dict] = None,
) -> dict:
    return {
        "plan_id": (row or {}).get("plan_id"),
        "plan_name_ar": plan_name_ar,
        "expires_at": (row or {}).get("expires_at"),
        "source": (row or {}).get("source"),
        "cancellable": _is_cancellable(row),
        "renewal_cancelled_at": (row or {}).get("renewal_cancelled_at"),
        # المشتركون الأوائل — the caller's OWN membership and nothing else. There
        # is no seat count, no seat total and no closing date on this payload (or
        # on any other): the remaining count never leaves the server (§1.10). The
        # dialog needs exactly these two facts to render the forfeiture warning
        # and its undo deadline.
        "early_adopter": early_adopter or _no_seat(),
    }


async def get_subscription(supabase: SupabaseClient, user_id: str) -> dict:
    """Current subscription state for إعدادات الحساب.

    A separate endpoint from ``GET /usage`` on purpose: the quota report has no
    ``source``, and bolting one on would put a money-shaped field on the surface
    every message-send path reads.
    """
    row = await run_db(_fetch_subscription, supabase, user_id)
    plan_name_ar = await run_db(_fetch_plan_name, supabase, (row or {}).get("plan_id"))
    seat = await run_db(early_adopter_status, supabase, user_id)
    return _state_payload(row, plan_name_ar, seat)


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

    # CANCELLING FORFEITS THE PRICE (early_adopters.md §1.6). The seat goes back
    # to the pool and the promo dies with it; only an undo brings it back. Same
    # best-effort posture as the survey insert and for the same reason — the flag
    # is the cancellation, and a user who asked to cancel must never be told it
    # failed because a seat row would not move.
    released = await run_db(
        release_early_adopter_seat,
        supabase,
        user_id,
        reason=RELEASE_REASON_CANCELLED,
        plan_id=plan_id,
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
            "early_adopter_seat_released": released,
        },
    )

    logger.info(
        "renewal cancelled: user=%s plan=%s reason=%s expires=%s",
        user_id, plan_id, reason, expires_at,
    )

    plan_name_ar = await run_db(_fetch_plan_name, supabase, plan_id)
    # Re-READ rather than assumed: if the release failed above, the user still
    # holds the seat and the dialog must not claim otherwise.
    seat = await run_db(early_adopter_status, supabase, user_id)
    return _state_payload(
        {**row, "renewal_cancelled_at": cancelled_at}, plan_name_ar, seat
    )


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

    # UNDO RESTORES THE SEAT UNCONDITIONALLY (early_adopters.md §1.6) — the
    # cancellation that released it has been taken back, so the price comes back
    # with it, on the ORIGINAL 90-day window (the RPC restores the seat, it does
    # not issue a new one, so no clock is reset). Best-effort, after the flag,
    # exactly like the release it mirrors.
    restored = await run_db(
        restore_early_adopter_seat, supabase, user_id, plan_id=row.get("plan_id")
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
            "early_adopter_seat_restored": restored,
        },
    )

    logger.info(
        "renewal reactivated: user=%s plan=%s survey=%s",
        user_id, row.get("plan_id"), revoked_id,
    )

    plan_name_ar = await run_db(_fetch_plan_name, supabase, row.get("plan_id"))
    seat = await run_db(early_adopter_status, supabase, user_id)
    return _state_payload({**row, "renewal_cancelled_at": None}, plan_name_ar, seat)


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


# ═══════════════════════════════════════════════════════════════════════════
# 7. المشتركون الأوائل — the seat (migration 138)
#
# Four service-role RPCs, four sync wrappers, all called through ``run_db``.
# Shared rules, so they are stated once here rather than four times below:
#
# * **Nothing in this section may raise.** A seat decides what the NEXT charge
#   costs. Every caller has already done the thing the user asked for — granted
#   a plan, returned money, recorded a cancellation — so an exception here would
#   convert a completed action into an error the user sees.
# * **Every wrapper self-guards on the plan** (``EARLY_ADOPTER_PLAN_IDS``) and on
#   a present ``user_id`` (117 leaves purged payments with a NULL one), the same
#   discipline ``_stamp_prior_snapshot`` uses: no condition for a call site to
#   get wrong.
# * **A missing RPC is not a failure.** Deployed ahead of 138, every call here
#   answers "no seat" and the product behaves exactly as it did before the
#   campaign existed.
# * **No count, ever.** These wrappers never receive, log or return a remaining
#   seat count — the RPCs do not expose one, and §1.10 says nothing that reaches
#   a user may reveal one.
# ═══════════════════════════════════════════════════════════════════════════

# ── claim_early_adopter_seat's vocabulary (138) ──────────────────────────────
#
# THE INVARIANT THE WHOLE SET SERVES:
#
#     A seat holder is precisely someone who was charged the promotional price.
#
# 138 gates EVERY claim on ``amount_sar + upgrade_credit_sar <= promo_price_sar
# + 0.01`` — not just the over-capacity branch — so a payment quoted at the list
# price never earns a seat, whatever the campaign is doing at the moment it
# settles. Without that, "grant the seat anyway" leaks in both directions: at the
# closing instant every later payer takes a seat forever, and at the OPENING
# instant a quote priced at 89.90 the day before collects 90 days of promo
# renewals it never paid for.
#
# THE FULL VOCABULARY (8 values, read out of 138). ``seat_id IS NOT NULL``
# discriminates all of them, so nothing here decides whether a seat exists — it
# only decides what to say in the log:
#
#   claimed            seat  a new seat (check `over_capacity`)
#   already_claimed    seat  this payment, or this user's live seat, already
#                            exists — the webhook/verify replay AND the pro→max
#                            upgrade carry-over land here
#   not_promo_priced   NULL  quoted at the LIST price → no seat, at ANY capacity
#   campaign_disabled  NULL  there is no `early_adopter_campaign` ROW AT ALL
#   forfeited          NULL  a standing 'cancelled' release (§1.6)
#   plan_not_eligible  NULL  the plan bears no seats (basic, free, …)
#   payment_not_found  NULL  unknown payment id
#   user_mismatch      NULL  NULL user, or not the payment's user
#
# There is deliberately no "campaign is full" answer: hoisting the gate split
# that branch in two and neither half survives as a refusal — full + list-priced
# is caught earlier and universally as `not_promo_priced`, and full +
# promo-priced is `claimed` with `over_capacity` stamped. **Past the gate,
# capacity never refuses.**
#
# The gate also sits AHEAD of the campaign check, so ``enabled = false`` does not
# refuse a claim either: the switch stops new QUOTES, not settled money, and a
# quote still outstanding when an operator flips it keeps its 90 days. That is
# why `campaign_disabled` no longer means "the flag is off" — it means the
# singleton row is MISSING, so there is no `promo_days` to anchor a window with
# and no `seat_limit` to measure against.
#
# DELIBERATELY ABSENT from the OK set, so they land on the ERROR branch:
# `plan_not_eligible` (138 disagrees with EARLY_ADOPTER_PLAN_IDS about pro/max —
# this wrapper never calls for anything else), `payment_not_found` and
# `user_mismatch` (we just granted a plan from that very row, so either answer
# means the ledger and the grant disagree). Those need a human, not a log line.
CLAIM_ACTION_CLAIMED = "claimed"
CLAIM_ACTION_ALREADY_CLAIMED = "already_claimed"
CLAIM_ACTION_CAMPAIGN_DISABLED = "campaign_disabled"
CLAIM_ACTION_NOT_PROMO_PRICED = "not_promo_priced"
CLAIM_ACTION_FORFEITED = "forfeited"

# The one routine "no seat, and that is correct" answer. DEBUG, not INFO: it
# fires on EVERY list-priced pro/max purchase for the rest of the product's life,
# and a log line per purchase saying nothing is noise that hides the real ones.
CLAIM_ACTIONS_ROUTINE_NO_SEAT = frozenset({CLAIM_ACTION_NOT_PROMO_PRICED})

CLAIM_ACTIONS_OK = frozenset(
    {CLAIM_ACTION_CLAIMED, CLAIM_ACTION_ALREADY_CLAIMED, CLAIM_ACTION_FORFEITED}
) | CLAIM_ACTIONS_ROUTINE_NO_SEAT


def _no_seat() -> dict:
    """The "not an early adopter" answer, as a FRESH dict every time (this value
    is embedded in an API payload — a shared literal would be one mutation away
    from telling every caller the same lie)."""
    return {"is_member": False, "promo_ends_at": None}


def _rpc_row(res: Any) -> dict:
    """First row of a ``RETURNS TABLE`` RPC, or ``{}``. Same tolerant shape
    handling as ``payment_service._stamp_prior_snapshot`` — postgrest-py has
    answered both a bare object and a one-element list across versions."""
    data = getattr(res, "data", None)
    if isinstance(data, list):
        data = data[0] if data else None
    return data if isinstance(data, dict) else {}


def _rpc_bool(res: Any) -> bool:
    """A ``RETURNS boolean`` RPC's answer, unwrapped before it is tested.

    ``bool(res.data)`` is NOT the same thing and would be a lie in the one
    version that wraps the scalar: ``[{"release_early_adopter_seat": false}]`` is
    a truthy list, so a seat that was never released would be logged and audited
    as released."""
    data = getattr(res, "data", None)
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict):
        data = next(iter(data.values()), None)
    return bool(data)


def _seat_applies(user_id: Optional[str], plan_id: Optional[str]) -> bool:
    return bool(user_id) and str(plan_id or "") in EARLY_ADOPTER_PLAN_IDS


def claim_early_adopter_seat(
    supabase: SupabaseClient,
    user_id: str,
    payment_id: str,
    *,
    plan_id: Optional[str],
) -> Optional[dict]:
    """``claim_early_adopter_seat(user, payment)`` — called AFTER ``grant_plan``.

    Three deliberate choices, all of them the same ones
    ``clear_renewal_cancellation`` above documents:

    * **Beside ``grant_plan``, never inside it.** 113/119/120 all established
      that the live money-path RPC is not edited for a side concern, and it also
      keeps this clear of ``trg_user_subscriptions_assignment``
      (BEFORE UPDATE OF plan_id) — a separate table cannot re-stamp anyone's term.
    * **Never raises.** The plan is granted and the money is in. A 500 here would
      be a customer who paid and saw an error.
    * **No idempotency guard of our own.** ``early_adopter_seats.payment_id`` is
      UNIQUE, which is what makes the webhook + ``/verify`` double-run safe —
      the same mechanism ``fulfilled_at`` gives ``grant_plan``. A Python-side
      "have they got one already?" check would race exactly where the index does
      not.

    A failure is logged at ERROR because of what it MEANS: someone paid the
    promo price and, with no seat behind it, will renew at the full one.

    **Only user-initiated purchases reach this.** The caller skips it for
    ``initiated_by='renewal'``: «المشتركون الأوائل» is the first 100 to *pay*,
    and an automatic charge on a saved card is not a decision made today. That
    gate lives at the call site because ``initiated_by`` is a property of the
    payment ROW, which this wrapper deliberately does not read.

    Returns the RPC row (``action``/``promo_ends_at``/``over_capacity``) or None.
    """
    if not _seat_applies(user_id, plan_id):
        return None
    try:
        row = _rpc_row(
            supabase.rpc(
                "claim_early_adopter_seat",
                {"p_user_id": user_id, "p_payment_id": payment_id},
            ).execute()
        )
    except Exception as exc:  # noqa: BLE001 — includes "function does not exist"
        logger.error(
            "claim_early_adopter_seat FAILED for user=%s payment=%s plan=%s. The "
            "plan IS granted and the money is in, but this purchase holds no "
            "seat: if it was priced at the promo rate the next renewal will "
            "charge the full one. Reconcile by hand: %s",
            user_id, payment_id, plan_id, exc,
        )
        return None

    action = str(row.get("action") or "unknown")
    if action in CLAIM_ACTIONS_ROUTINE_NO_SEAT:
        # `not_promo_priced` — this purchase was quoted the LIST price, so it
        # correctly earns nothing. Every pro/max purchase for the rest of the
        # product's life lands here; DEBUG on purpose, because an INFO line per
        # purchase forever is noise that hides the real ones.
        logger.debug(
            "early-adopter claim skipped (%s): payment=%s", action, payment_id
        )
        return row
    if action == CLAIM_ACTION_CAMPAIGN_DISABLED:
        # NOT a policy state — a BROKEN INSTALL. Since the promo-price gate was
        # hoisted ahead of the campaign check, ``enabled = false`` still seats a
        # promo-priced payment (the switch stops new quotes, not settled money),
        # so the only way to reach this branch is a missing
        # `early_adopter_campaign` row: no promo_days to anchor a window, no
        # seat_limit to measure against. 138 raises a WARNING for it and so do
        # we — louder, because by the time we see it the money is already in.
        logger.error(
            "early-adopter claim FAILED — no early_adopter_campaign row: user=%s "
            "payment=%s plan=%s. This customer was charged the PROMOTIONAL price "
            "and holds no seat, so their next renewal will charge the full one. "
            "Restore the singleton row and re-run the claim for this payment.",
            user_id, payment_id, plan_id,
        )
        return row
    if action == CLAIM_ACTION_FORFEITED:
        # §1.6 held: this user cancelled and let it stand, so 138 quoted them the
        # LIST price and refuses them a seat. Expected, not an error — INFO
        # because it is rare and because it explains a full-price charge from
        # someone who used to be a member.
        logger.info(
            "early-adopter claim refused (forfeited by a completed cancellation): "
            "user=%s payment=%s — this purchase was quoted at the list price",
            user_id, payment_id,
        )
        return row
    if action not in CLAIM_ACTIONS_OK:
        logger.error(
            "claim_early_adopter_seat returned action=%r for user=%s payment=%s — "
            "no seat was recorded for a purchase that may have been priced at the "
            "promo rate",
            action, user_id, payment_id,
        )
        return row

    if row.get("over_capacity"):
        # §3.5: a quote priced while seats were open settled after the campaign
        # closed. The seat is granted anyway and stamped, because the alternative
        # is charging someone the full price after quoting them the promo. Find
        # them with `WHERE over_capacity`.
        logger.warning(
            "early-adopter seat claimed OVER CAPACITY: user=%s payment=%s — an "
            "open quote settled after the campaign closed (accepted exposure, "
            "early_adopters.md §3.5/§10)",
            user_id, payment_id,
        )
    logger.info(
        "early-adopter seat %s: user=%s payment=%s plan=%s promo_ends=%s",
        action, user_id, payment_id, plan_id, row.get("promo_ends_at"),
    )
    return row


def release_early_adopter_seat(
    supabase: SupabaseClient,
    # Optional because ``_mark_refunded`` passes ``row.get("user_id")`` and 117
    # leaves a purged buyer's payment row with a NULL one. ``_seat_applies``
    # turns that into a no-op rather than an RPC call with a null argument.
    user_id: Optional[str],
    *,
    reason: str,
    plan_id: Optional[str],
) -> bool:
    """``release_early_adopter_seat(user, reason)`` — refund or cancellation.

    ``reason='refund'`` (§1.5) voids the status and returns the seat to the pool;
    they may buy back in while seats remain. ``reason='cancelled'`` (§1.6) is the
    permanent forfeiture — only ``restore_early_adopter_seat`` undoes it.

    Never raises, for both callers' reasons: on the refund path the money is
    already back with the customer, and on the cancel path the flag write IS the
    cancellation.

    Returns True when a live seat was actually released.
    """
    if not _seat_applies(user_id, plan_id):
        return False
    try:
        released = _rpc_bool(
            supabase.rpc(
                "release_early_adopter_seat",
                {"p_user_id": user_id, "p_reason": reason},
            ).execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "release_early_adopter_seat(%s) FAILED for user=%s plan=%s. The %s "
            "itself stands; the seat is still held, so this user keeps the promo "
            "price they are no longer entitled to and the seat stays out of the "
            "pool: %s",
            reason, user_id, plan_id, reason, exc,
        )
        return False
    if released:
        logger.info(
            "early-adopter seat released: user=%s plan=%s reason=%s",
            user_id, plan_id, reason,
        )
    return released


def restore_early_adopter_seat(
    supabase: SupabaseClient, user_id: str, *, plan_id: Optional[str]
) -> bool:
    """``restore_early_adopter_seat(user)`` — undo a cancellation-release (§1.6).

    Unconditional by decision: a user who takes back their cancellation gets
    their price back even if the campaign has since closed, because the promise
    travels with the subscription (§1.4) and the undo says the cancellation
    never happened.

    138 clears ``released_at`` AND ``release_reason`` together, which is what
    makes that true: the forfeiture predicate reads the reason, so an undone
    cancellation leaves no trace and cannot keep quoting the list price at
    someone whose seat is live again.

    Never raises. Returns True when a seat was actually restored.
    """
    if not _seat_applies(user_id, plan_id):
        return False
    try:
        restored = _rpc_bool(
            supabase.rpc("restore_early_adopter_seat", {"p_user_id": user_id}).execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "restore_early_adopter_seat FAILED for user=%s plan=%s. The renewal "
            "IS back on, but the early-adopter price was NOT restored — the user "
            "was told the undo would bring it back, so this needs a hand: %s",
            user_id, plan_id, exc,
        )
        return False
    if restored:
        logger.info("early-adopter seat restored: user=%s plan=%s", user_id, plan_id)
    return restored


def early_adopter_status(supabase: SupabaseClient, user_id: str) -> dict:
    """``early_adopter_status(user)`` → ``{is_member, promo_ends_at}``.

    The caller's OWN membership, and deliberately nothing else: the RPC also
    reports ``campaign_open``, which is dropped here because this payload is
    per-user state for إعدادات الحساب and the campaign's openness has exactly one
    public door (``GET /payments/early-adopter``). No count crosses this
    boundary, in any shape (§1.10).

    ``has_seat`` carries the forfeiture predicate too (138 shares it across all
    three functions), so a user who cancelled and let it stand reads as "not a
    member" here — which is exactly what the dialog should say, since that is
    also the price they will be quoted.

    Never raises and never blocks the settings dialog: any failure — including
    138 not being applied — reads as "not a member", which is what every user
    is until the campaign runs.
    """
    if not user_id:
        return _no_seat()
    try:
        row = _rpc_row(
            supabase.rpc("early_adopter_status", {"p_user_id": user_id}).execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "early_adopter_status unavailable for user=%s (reading as 'not a "
            "member'): %s", user_id, exc,
        )
        return _no_seat()
    return {
        "is_member": bool(row.get("has_seat")),
        "promo_ends_at": row.get("promo_ends_at") if row.get("has_seat") else None,
    }
