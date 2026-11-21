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
read time). Active-ness is derived here from ``expires_at`` — extended by the
renewal grace (``renewal_grace_until``, migration ``196_renewal_grace.sql``)
with exactly the expression ``get_user_quota_state`` uses (``_access_ends_at``).
⚠ APPLY 196 BEFORE DEPLOYING: ``_fetch_subscription`` names the column, and a
backend that ships first answers 42703 on the settings dialog — 120's trap.

تغيير الباقة — THE SCHEDULED DOWNGRADE (migration ``226_scheduled_downgrade.sql``,
`.claude/plans/pro_plus_tier_and_downgrade.md` §4) ────────────────────────────
Section 8 lets a paying user move to a LOWER renewable plan at the next
renewal: ``user_subscriptions.renewal_plan_id`` (+ ``renewal_plan_set_at``),
which ``renewal_service`` reads. Nothing moves money here either — the current
term runs out untouched, there is no refund and no proration (D6), and the
renewal charges the target's fresh-purchase price (D7,
``payment_service.fresh_purchase_quote``). The same two write rules apply:
the column is written ALONE (never beside ``plan_id``), and it is cleared in
Python — by a cancel, an undo, and every user-initiated paid grant — never
inside ``grant_plan``. Cancel wins over a pending change, and an undo of the
cancel does not bring the change back.
⚠ APPLY 226 BEFORE DEPLOYING: ``_fetch_subscription`` names both columns.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client as SupabaseClient

from backend.app.errors import ErrorCode, LunaHTTPException, MSG_SERVICE_UNAVAILABLE
from backend.app.services import payment_method_service
from backend.app.services.audit_service import write_audit_log
from shared.db.run import run_db

logger = logging.getLogger(__name__)


def _payments():
    """``payment_service``, imported at CALL time: it imports this module at
    load time (the seat wrappers below), so a top-level import here would be a
    cycle. Only section 8 needs it — for ``PLAN_RANK``, the money formatting
    and the fresh-purchase quote."""
    from backend.app.services import payment_service

    return payment_service

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
# A seat is a pro/pro_plus/max concern: `basic` is discounted for EVERYONE while
# seats remain and enrols nobody (§1.9), so a basic purchase must never claim a
# seat and — the case that actually bites — a basic refund or a basic
# cancellation must never RELEASE the pro seat the same user is still holding.
# Every wrapper in section 7 self-guards on this set, so no call site has to
# remember. `pro_plus` bears seats from 225 on (batch `pro_plus_t1_a`).
EARLY_ADOPTER_PLAN_IDS = frozenset({"pro", "pro_plus", "max"})

# Basic's one-time offer (migration 204). Its seats live in `basic_offer_seats`,
# never in `early_adopter_seats`, so a Basic purchase cannot touch a Pro/Max seat.
BASIC_OFFER_PLAN_ID = "basic"

# `early_adopter_seats.release_reason` CHECK (138). Named so the two callers
# cannot drift into a string the constraint rejects.
RELEASE_REASON_REFUND = "refund"
RELEASE_REASON_CANCELLED = "cancelled"
# 226: the seat on the plan a scheduled downgrade LEAVES ends at the switch. It
# counts as used (D12). Before 226 the RPC refuses the reason (NOTICE + false),
# which leaves the seat live — the pre-feature state, never an error.
RELEASE_REASON_DOWNGRADED = "downgraded"

# ── Arabic messages (rule 5) ─────────────────────────────────────────────────

NO_PAID_SUBSCRIPTION_AR = "لا يوجد اشتراك مدفوع فعّال لإلغائه"
ALREADY_CANCELLED_AR = "تم إلغاء تجديد اشتراكك مسبقاً"
NOT_CANCELLED_AR = "لا يوجد إلغاء يمكن التراجع عنه"
TERM_ENDED_AR = "انتهت مدة اشتراكك، لا يمكن التراجع عن الإلغاء"
INVALID_REASON_AR = "يرجى اختيار سبب الإلغاء"
# تغيير الباقة (section 8)
NO_PAID_SUBSCRIPTION_TO_CHANGE_AR = "لا يوجد اشتراك مدفوع فعّال لتغيير باقته"
CHANGE_AFTER_CANCEL_AR = (
    "تم إلغاء تجديد اشتراكك. تراجع عن الإلغاء أولاً، ثم اختر الباقة الجديدة."
)
DOWNGRADE_INVALID_AR = (
    "لا يمكن التحويل إلى هذه الباقة. يمكنك التحويل إلى باقة أقل من باقتك الحالية فقط."
)
DOWNGRADE_PRICE_CHANGED_AR = (
    "تغيّر سعر هذه الباقة قبل التأكيد. راجع السعر الجديد ثم أكّد مرة أخرى."
)
DOWNGRADE_NO_METHOD_AR = "أضف بطاقة دفع أولاً حتى نتمكن من تحويل باقتك عند التجديد."

# ``downgrade_blocked_reason`` on the GET payload: the options are shown, but a
# change cannot be scheduled until this is resolved.
DOWNGRADE_BLOCKED_NO_METHOD = "no_payment_method"


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


def _iso_or_none(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


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

_SUBSCRIPTION_COLUMNS = (
    "plan_id, source, started_at, expires_at, renewal_cancelled_at, renewal_grace_until, "
    # 226 — the scheduled downgrade (section 8) and the price PROMISED when it
    # was scheduled (owner 2026-11-20). ⚠ 226 strictly before deploy.
    "renewal_plan_id, renewal_plan_set_at, renewal_plan_price_sar, "
    "renewal_plan_batch_id, renewal_plan_promo_months"
)

# The five 226 columns that describe ONE scheduled change. Set together by
# schedule_downgrade and nulled together by every clearing path (226's CHECK:
# all NULL when renewal_plan_id is NULL).
_PENDING_COLUMNS: tuple[str, ...] = (
    "renewal_plan_id", "renewal_plan_set_at", "renewal_plan_price_sar",
    "renewal_plan_batch_id", "renewal_plan_promo_months",
)


def _no_pending() -> dict:
    return {col: None for col in _PENDING_COLUMNS}


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
    supabase: SupabaseClient,
    user_id: str,
    when: Optional[str],
    *,
    end_grace: bool = False,
    clear_downgrade: bool = False,
) -> bool:
    """Set (or clear) ``renewal_cancelled_at`` and NOTHING ELSE.

    ``updated_at`` rides along deliberately: the assignment trigger is the only
    thing that maintains it and it does not fire for this write, so without it
    the operator view would show a stale timestamp next to a fresh opt-out.
    Neither column is ``plan_id``, so the trigger stays asleep — which is the
    entire point (see the module docstring, rule 1).

    ``end_grace`` — a cancel DURING the renewal grace (196) ends the grace in the
    same statement (``renewal_grace_until = when``): the opt-out and the lapse
    are one write, so neither can land without the other.

    ``clear_downgrade`` (226) — a cancel (or its undo) also drops a pending
    scheduled downgrade, in the SAME statement: cancel wins, and an opt-out
    must never land while a plan change it supersedes stays armed. Only passed
    when one is actually pending, so the common write names no 226 column.

    Returns True when a row was actually written.
    """
    patch: dict[str, Any] = {"renewal_cancelled_at": when, "updated_at": _now_iso()}
    if end_grace:
        patch["renewal_grace_until"] = when
    if clear_downgrade:
        patch.update(_no_pending())          # all five, together (226 CHECK)
    res = (
        supabase.table("user_subscriptions")
        .update(patch)
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


def _grace_extends_access(row: Optional[dict]) -> Optional[datetime]:
    """``renewal_grace_until`` when it extends access past ``expires_at``, else None.

    The SAME predicate ``get_user_quota_state`` applies (196): a payment-sourced
    row whose grace ends LATER than its term. A grace on any other source (a code
    or store grant that replaced the plan mid-ladder) is inert, and so is one
    that ends before the term — "lapse now" on a declined LAST tick before
    expiry must never cut into paid time.
    """
    if not row or row.get("source") != PAID_SOURCE:
        return None
    grace = _parse_ts(row.get("renewal_grace_until"))
    expires_at = _parse_ts(row.get("expires_at"))
    if grace is None or expires_at is None or grace <= expires_at:
        return None
    return grace


def _access_ends_at(row: Optional[dict]) -> Optional[datetime]:
    """When the user stops having this plan: the term's end, or the grace's."""
    return _grace_extends_access(row) or _parse_ts((row or {}).get("expires_at"))


def _in_renewal_grace(row: Optional[dict]) -> bool:
    """The term has ended but a declined renewal is still being retried."""
    grace = _grace_extends_access(row)
    expires_at = _parse_ts((row or {}).get("expires_at"))
    now = _now()
    return bool(grace and expires_at and expires_at <= now < grace)


def _term_is_running(row: Optional[dict]) -> bool:
    """Is this a plan with a future end date?

    A NULL ``expires_at`` (dev / manual non-expiring grant) reads as NOT
    running here even though the plan is live: there is no end date to promise
    the user in «تبقى باقتك فعّالة حتى …», and nothing that could renew. Those
    grants are never ``source='payment'`` anyway, so this only ever matters as
    a second wall.

    The renewal grace counts as running (renewal_retry_grace.md §4): the user
    still has Pro/Max, so the dialog must not show an ended plan, and a cancel
    must be possible — it is how they stop the retries.
    """
    if not row or not row.get("plan_id"):
        return False                      # no row, or a locked account
    ends = _access_ends_at(row)
    return ends is not None and ends > _now()


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

    Read by ``GET /auth/me``. Its only web consumer was the «اتعرف على ريحان»
    intro tour, RETIRED 2026-10-21 (profession_intro_offer.md §4.7) — the field
    stays because it is additive and shipped app builds may read it. The tour
    was meant to arrive right after a purchase (A2 in
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
    downgrade: Optional[dict] = None,
) -> dict:
    return {
        "plan_id": (row or {}).get("plan_id"),
        "plan_name_ar": plan_name_ar,
        "expires_at": (row or {}).get("expires_at"),
        "source": (row or {}).get("source"),
        "cancellable": _is_cancellable(row),
        "renewal_cancelled_at": (row or {}).get("renewal_cancelled_at"),
        # 196 — ADDITIVE, older app builds ignore it. Set ONLY while it extends
        # access past `expires_at` (`_grace_extends_access`), so a client reads
        # "the plan runs to the later of the two" without re-deriving the rule.
        "renewal_grace_until": _iso_or_none(_grace_extends_access(row)),
        # المشتركون الأوائل — the caller's OWN membership and nothing else. There
        # is no seat count, no seat total and no closing date on this payload (or
        # on any other): the remaining count never leaves the server (§1.10). The
        # dialog needs exactly these two facts to render the forfeiture warning
        # and its undo deadline.
        "early_adopter": early_adopter or _no_seat(),
        # 226 — تغيير الباقة (section 8). ADDITIVE: older clients ignore them.
        # `pending_*` describe a change scheduled for the next renewal;
        # `downgrade_options` the plans one may be scheduled to. See
        # `_downgrade_state` for when each is populated.
        **(downgrade or _no_downgrade()),
    }


async def _state(
    supabase: SupabaseClient, user_id: str, row: Optional[dict]
) -> dict:
    """The full ``GET /subscription`` payload for ``row`` (as read, or as just
    written by the caller). Every route on this surface answers with it, so the
    dialog can re-render from any response."""
    plan_name_ar = await run_db(_fetch_plan_name, supabase, (row or {}).get("plan_id"))
    seat = await run_db(early_adopter_status, supabase, user_id)
    downgrade = await _downgrade_state(supabase, user_id, row)
    return _state_payload(row, plan_name_ar, seat, downgrade)


async def get_subscription(supabase: SupabaseClient, user_id: str) -> dict:
    """Current subscription state for إعدادات الحساب.

    A separate endpoint from ``GET /usage`` on purpose: the quota report has no
    ``source``, and bolting one on would put a money-shaped field on the surface
    every message-send path reads.
    """
    row = await run_db(_fetch_subscription, supabase, user_id)
    return await _state(supabase, user_id, row)


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
    # CANCEL DURING THE RENEWAL GRACE LAPSES NOW (renewal_retry_grace.md §4).
    # The paid term already ended at expires_at; the grace was access lent while
    # the card was retried, and an opt-out means there is nothing left to retry.
    # /terms §3 («تبقى باقتك فعّالة حتى نهاية المدة المدفوعة») holds: no paid
    # time is cut. The dialog says so before the user confirms.
    end_grace = _in_renewal_grace(row)
    # CANCEL WINS over a scheduled downgrade (pro_plus_tier_and_downgrade.md
    # §4.1): there is no next renewal for it to apply to. Dropped in the same
    # write as the opt-out, and an undo does not bring it back.
    pending_plan_id = row.get("renewal_plan_id") or None
    try:
        written = await run_db(
            _write_renewal_flag, supabase, user_id, cancelled_at,
            end_grace=end_grace, clear_downgrade=bool(pending_plan_id),
        )
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
            "renewal_grace_ended": end_grace,
            "early_adopter_seat_released": released,
            "scheduled_downgrade_cleared": pending_plan_id,
        },
    )

    logger.info(
        "renewal cancelled: user=%s plan=%s reason=%s expires=%s grace_ended=%s "
        "downgrade_cleared=%s",
        user_id, plan_id, reason, expires_at, end_grace, pending_plan_id,
    )

    # The seat is re-READ inside _state rather than assumed: if the release
    # failed above, the user still holds the seat and the dialog must not claim
    # otherwise.
    written_row = {**row, "renewal_cancelled_at": cancelled_at}
    if end_grace:
        written_row["renewal_grace_until"] = cancelled_at
    if pending_plan_id:
        written_row.update(_no_pending())
    return await _state(supabase, user_id, written_row)


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

    # An undo restores the RENEWAL, not a plan change: the cancel already
    # dropped any scheduled downgrade and this does not bring it back (the user
    # picks again). A pending value here could only be a hand-made row; it is
    # cleared in the same write so "renewal back on" never resurrects it.
    stray_downgrade = row.get("renewal_plan_id") or None
    try:
        written = await run_db(
            _write_renewal_flag, supabase, user_id, None,
            clear_downgrade=bool(stray_downgrade),
        )
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

    return await _state(
        supabase,
        user_id,
        {**row, "renewal_cancelled_at": None, **_no_pending()},
    )


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


def reset_renewal_ladder(supabase: SupabaseClient, user_id: str) -> bool:
    """A new paid grant restarts the dunning ladder — 132's promise, kept here.

    SYNC, called through ``run_db`` from ``payment_service`` right after
    ``clear_renewal_cancellation``, for the same two reasons: not inside
    ``grant_plan`` (113's rule), and it never raises.

    Without it, a user who re-buys at checkout while a declined renewal is still
    mid-ladder keeps ``renewal_failed_count > 0``; the renewal job then reads
    their NEW term as a due retry and charges the period after it ~30 days
    early. Found on prod 2026-10-30 (renewal_retry_grace.md §2).

    ``renewal_grace_until`` (196) is cleared too: the new grant IS the access,
    and a grace left standing would outlive a refund of that grant.
    ``renewal_attempt_at`` is left alone — it is history, and a successful
    renewal keeps it too. Returns True when anything was reset.

    ``select("*")`` on purpose: this runs on every paid purchase, and naming
    ``renewal_grace_until`` would make it 42703 — and so stop resetting the
    count, the §2 fix — on a backend that lands before 196. ``*`` returns the
    column when it exists and simply omits it when it does not.
    """
    try:
        res = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        row = rows[0] if rows else {}
        patch: dict[str, Any] = {}
        if int(row.get("renewal_failed_count") or 0):
            patch["renewal_failed_count"] = 0
        if row.get("renewal_grace_until"):
            patch["renewal_grace_until"] = None
        if not patch:
            return False                       # the common case: nothing to do
        patch["updated_at"] = _now_iso()
        (
            supabase.table("user_subscriptions")
            .update(patch)
            .eq("user_id", user_id)
            .execute()
        )
        logger.info(
            "renewal ladder reset by a new purchase: user=%s (%s)",
            user_id, sorted(k for k in patch if k != "updated_at"),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "could not reset renewal_failed_count for user=%s after a paid grant "
            "(the plan IS granted; the renewal job's expires_at bound still stops "
            "an early charge): %s",
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
    return {"is_member": False, "promo_ends_at": None, "returnable": False,
            "tier_rules": False}


def ladder_v2_enabled(supabase: SupabaseClient) -> bool:
    """``early_adopter_campaign.ladder_v2`` (192) — are the price-tier rules in
    force? Read for ``early_adopter.tier_rules``: the cancel dialog's note
    differs (v2: cancelling loses the seat and its tier, never-used tiers stay
    open; before: the promo price is forfeited for good). Never raises; any
    failure reads False, i.e. today's note."""
    try:
        res = (
            supabase.table("early_adopter_campaign")
            .select("ladder_v2")
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception as exc:  # noqa: BLE001 — includes 192 not being applied
        logger.warning("early_adopter_campaign.ladder_v2 unreadable (reading False): %s", exc)
        return False
    if isinstance(rows, dict):
        rows = [rows]
    return bool(rows) and isinstance(rows[0], dict) and rows[0].get("ladder_v2") is True


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
    payment_id: Optional[str] = None,
) -> bool:
    """``release_early_adopter_seat(user, reason)`` — refund or cancellation.

    ``payment_id`` (226) is sent only when given — today only by the
    ``'downgraded'`` release, with the downgrade renewal's payment, so the
    released seat records which payment superseded it and a refund of that
    payment can give it back, as for a promo-priced switch. Every other call's
    wire params are unchanged (two keys).

    ``reason='refund'`` (§1.5) voids the status and returns the seat to the pool;
    they may buy back in while seats remain. ``reason='cancelled'`` (§1.6) is the
    permanent forfeiture — only ``restore_early_adopter_seat`` undoes it.

    192 (V5), same signature: on a 'refund' release whose released seat was
    claimed by a payment that is now ``refunded``, the SQL also gives back the
    seat that payment superseded ('upgraded', ``released_by_payment_id``) while
    its window is open — refunding a Pro → Max switch returns the Pro seat. It
    identifies the switch from the payment row's status, which is why BOTH
    refund paths stamp ``status='refunded'`` before calling this.

    Never raises, for both callers' reasons: on the refund path the money is
    already back with the customer, and on the cancel path the flag write IS the
    cancellation.

    Returns True when a live seat was actually released.
    """
    if not _seat_applies(user_id, plan_id):
        return False
    params: dict[str, Any] = {"p_user_id": user_id, "p_reason": reason}
    if payment_id:
        params["p_payment_id"] = str(payment_id)
    try:
        released = _rpc_bool(
            supabase.rpc("release_early_adopter_seat", params).execute()
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


def claim_basic_offer_seat(
    supabase: SupabaseClient, user_id: str, payment_id: str
) -> Optional[dict]:
    """``claim_basic_offer_seat(user, payment)`` — marks the offer used.

    Called after ``grant_plan`` for a Basic sale stamped with a batch. Idempotent
    on the payment (the webhook and ``/verify`` both reach it), and it never
    raises: the plan is granted and the money is in. A failed claim is logged at
    ERROR because of what it means: this user could buy at the offer price again.

    Returns the RPC row (``seat_id``/``over_capacity``/``action``) or None.
    """
    if not user_id:
        return None
    try:
        row = _rpc_row(
            supabase.rpc(
                "claim_basic_offer_seat",
                {"p_user_id": user_id, "p_payment_id": payment_id},
            ).execute()
        )
    except Exception as exc:  # noqa: BLE001 — includes "function does not exist"
        logger.error(
            "claim_basic_offer_seat FAILED for user=%s payment=%s. The plan IS "
            "granted, but the offer is not marked used. Reconcile by hand: %s",
            user_id, payment_id, exc,
        )
        return None

    action = str(row.get("action") or "unknown")
    if action not in (CLAIM_ACTION_CLAIMED, CLAIM_ACTION_ALREADY_CLAIMED):
        logger.error(
            "claim_basic_offer_seat returned action=%r for user=%s payment=%s — the "
            "offer is not marked used for an offer-priced sale",
            action, user_id, payment_id,
        )
        return row
    if row.get("over_capacity"):
        logger.warning(
            "basic offer seat claimed OVER CAPACITY: user=%s payment=%s — an open "
            "quote settled after the last seat sold",
            user_id, payment_id,
        )
    logger.info(
        "basic offer seat %s: user=%s payment=%s", action, user_id, payment_id
    )
    return row


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


def _is_intro_offer_member(supabase: SupabaseClient, user_id: str) -> bool:
    """Has this user ever paid an عرض الشهر الأول first month? (migration 183)

    Such a user is exempt from cancellation forfeiture: after a lapsed cancel
    they may buy pro again at the seat price with a fresh 90 days (owner,
    2026-10-21). The exemption itself is enforced in SQL; this only tells the
    cancel dialog which warning to show. Never raises — any failure reads as
    False, i.e. the stricter, ordinary warning.
    """
    try:
        return _rpc_bool(
            supabase.rpc("is_intro_offer_member", {"p_user_id": user_id}).execute()
        )
    except Exception as exc:  # noqa: BLE001 — includes 183 not being applied
        logger.warning(
            "is_intro_offer_member unavailable for user=%s (reading as False): %s",
            user_id, exc,
        )
        return False


def early_adopter_status(supabase: SupabaseClient, user_id: str) -> dict:
    """``early_adopter_status(user)`` → ``{is_member, promo_ends_at, returnable,
    tier_rules}``.

    ``returnable`` (183): the caller started on عرض الشهر الأول, so cancelling
    does NOT forfeit the price for good — the dialog shows the "you can come
    back at the seat price" wording instead of the forfeiture warning.

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
    tier_rules = ladder_v2_enabled(supabase)
    try:
        row = _rpc_row(
            supabase.rpc("early_adopter_status", {"p_user_id": user_id}).execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "early_adopter_status unavailable for user=%s (reading as 'not a "
            "member'): %s", user_id, exc,
        )
        return {**_no_seat(), "returnable": _is_intro_offer_member(supabase, user_id),
                "tier_rules": tier_rules}
    return {
        "is_member": bool(row.get("has_seat")),
        "promo_ends_at": row.get("promo_ends_at") if row.get("has_seat") else None,
        "returnable": _is_intro_offer_member(supabase, user_id),
        # 192: which cancellation note applies (ladder_v2). A boolean about the
        # RULES, never about capacity — still no count on this payload.
        "tier_rules": tier_rules,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 8. تغيير الباقة — the scheduled downgrade (migration 226)
#
# `.claude/plans/pro_plus_tier_and_downgrade.md` §4 + §8. A paying user may move
# to a LOWER renewable plan at the NEXT renewal (D6): the current term runs out
# untouched, nothing is refunded or prorated, and the renewal job charges the
# target's fresh-purchase price (D7) and grants it. Basic / free are never
# targets (D8 — the dialog points at «إلغاء التجديد» instead).
#
# State lives in five columns on user_subscriptions (``_PENDING_COLUMNS``) —
# ``renewal_plan_id``, ``renewal_plan_set_at`` and THE PROMISE (owner
# 2026-11-20): ``renewal_plan_price_sar`` / ``renewal_plan_batch_id`` /
# ``renewal_plan_promo_months``, the fresh-purchase quote taken when the change
# was scheduled. The renewal charges the LOWER of that promise and a fresh quote
# at charge time, so what the user confirmed is a ceiling that holds even if the
# offer's batch fills or closes, or the target loses its list price, meanwhile.
# Written ALONE (rule 1: never beside plan_id) through the service role, set and
# nulled as one. Who clears them, all in Python, never inside grant_plan:
#   * cancel_renewal (cancel wins) and reactivate_renewal (an undo does not
#     bring a change back);
#   * every user-initiated paid grant, and the downgrade renewal itself
#     (``payment_service._mark_paid_and_grant`` → ``clear_scheduled_downgrade``);
#   * the user, via ``cancel_scheduled_downgrade``.
#
# Not modelled on this branch: store-billed subscriptions (Phase D). There is no
# store-managed state on master to refuse on; when it lands, ``_change_allowed``
# is the one place to add it.
# ═══════════════════════════════════════════════════════════════════════════


def _no_downgrade() -> dict:
    """No change pending and none on offer — a FRESH dict every time (it is
    embedded in an API payload)."""
    return {
        "pending_plan_id": None,
        "pending_plan_name_ar": None,
        "pending_price_sar": None,
        "pending_promo_months": None,
        "downgrade_options": [],
        "downgrade_blocked_reason": None,
    }


def _has_chargeable_method(supabase: SupabaseClient, user_id: str) -> Optional[bool]:
    """Is there a card the renewal job could charge? Sync. ``None`` when the
    read itself failed — the dialog then shows no block (the POST re-checks)."""
    try:
        return payment_method_service.get_chargeable_method(supabase, user_id) is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("chargeable method unreadable for user=%s: %s", user_id, exc)
        return None


def _plan_rank(plan_id: Optional[str]) -> Optional[int]:
    """``payment_service.PLAN_RANK`` — the SAME ranking checkout's downgrade
    guard uses, so «lower» means one thing on both sides."""
    return _payments().PLAN_RANK.get(str(plan_id or ""))


def _change_allowed(row: Optional[dict]) -> bool:
    """May this subscription have a plan change scheduled (or shown) right now?

    The cancel button's own rule — a PAID term still running, the renewal grace
    included — and renewal still ON: a cancelled term has no next renewal for a
    change to apply to, so the undo comes first."""
    return _is_cancellable(row) and not (row or {}).get("renewal_cancelled_at")


def _is_downgrade_target(current: Optional[dict], target: Optional[dict]) -> bool:
    """Both plans renew, the target is purchasable today (``price_sar`` NOT
    NULL — pro_plus ships dark with a NULL price), and it ranks STRICTLY lower
    than the current plan."""
    if not current or not target:
        return False
    if not payment_method_service.plan_renews(current):
        return False
    if not payment_method_service.plan_renews(target) or target.get("price_sar") is None:
        return False
    cur, tgt = _plan_rank(current.get("plan_id")), _plan_rank(target.get("plan_id"))
    return cur is not None and tgt is not None and tgt < cur


def pending_downgrade_of(row: Optional[dict]) -> Optional[str]:
    """The plan ``row`` is scheduled to move to at its next renewal, or None.

    Shared by the dialog and the renewal job, so the change the user sees and
    the change their card is charged for cannot disagree.

    A STALE value reads as None: one set before the current plan started
    (``renewal_plan_set_at < started_at``) was scheduled from a plan the user has
    since left — grant_plan stamps a fresh ``started_at`` on every plan change —
    and must never move them off a plan they bought afterwards. Every user
    purchase also clears the column; this is the second wall for when that
    best-effort clear failed.
    """
    if not row:
        return None
    target = row.get("renewal_plan_id")
    if not target or target == row.get("plan_id"):
        return None
    set_at = _parse_ts(row.get("renewal_plan_set_at"))
    started = _parse_ts(row.get("started_at"))
    if set_at is not None and started is not None and set_at < started:
        return None
    return str(target)


def promise_of(row: Optional[dict]) -> Optional[dict]:
    """The price PROMISED when the pending change was scheduled →
    ``{"price_sar": Decimal, "batch_id": str | None, "months": int | None}``,
    or None when there is no usable promise (no change pending, or a row from
    before the promise columns — 226's CHECK makes that impossible once
    applied). Shared by the dialog and the renewal job."""
    if pending_downgrade_of(row) is None:
        return None
    ps = _payments()
    raw = (row or {}).get("renewal_plan_price_sar")
    if raw is None:
        return None
    price = ps.q2(raw)
    if price < ps.MIN_CHARGE_SAR:
        return None
    batch_id = (row or {}).get("renewal_plan_batch_id") or None
    return {
        "price_sar": price,
        "batch_id": str(batch_id) if batch_id else None,
        "months": ps._quote_months((row or {}).get("renewal_plan_promo_months")) if batch_id else None,
    }


def _fetch_change_catalog(
    supabase: SupabaseClient, current_plan_id: Optional[str]
) -> dict[str, dict]:
    """The renewable plans plus the current one, keyed by ``plan_id``. One read:
    the catalog is a handful of rows."""
    ids = set(payment_method_service.RENEWABLE_PLAN_IDS)
    if current_plan_id:
        ids.add(str(current_plan_id))
    res = (
        supabase.table("plans")
        .select("plan_id, name_ar, price_sar, duration_days, billing_cycle")
        .in_("plan_id", sorted(ids))
        .execute()
    )
    return {
        str(r["plan_id"]): r
        for r in (getattr(res, "data", None) or [])
        if isinstance(r, dict) and r.get("plan_id")
    }


def _quote_target(
    supabase: SupabaseClient, user_id: str, plan: dict
) -> Optional[dict]:
    """``payment_service.fresh_purchase_quote`` for the dialog. Sync, never
    raises: an unreadable quote shows the LIST price with no offer (the dialog
    must not break on a promotion), and an unpurchasable plan shows nothing."""
    ps = _payments()
    try:
        return ps.fresh_purchase_quote(supabase, user_id, plan)
    except Exception as exc:  # noqa: BLE001 — PricingUnavailable included
        if plan.get("price_sar") is None:
            return None
        logger.warning(
            "downgrade quote unreadable for user=%s plan=%s — showing the list "
            "price: %s", user_id, plan.get("plan_id"), exc,
        )
        list_sar = ps.q2(plan["price_sar"])
        return {"price_sar": list_sar, "list_sar": list_sar, "batch": None,
                "intro_group": None}


def _option(plan: dict, quote: Optional[dict]) -> dict:
    """One entry of ``downgrade_options``. ``price_sar`` is the LIST price;
    ``promo_price_sar`` is what the first renewal would charge when that is
    below list (an offer this user could take as a fresh buyer), with
    ``promo_months`` when it comes from a batch. Never a batch id, tier or seat
    count (pricing v2 §1.4-10)."""
    ps = _payments()
    list_sar = ps.q2(plan.get("price_sar"))
    price = (quote or {}).get("price_sar")
    promo = price if price is not None and ps.q2(price) < list_sar else None
    batch = (quote or {}).get("batch") or None
    return {
        "plan_id": plan.get("plan_id"),
        "name_ar": plan.get("name_ar"),
        "price_sar": ps._money(list_sar),
        "promo_price_sar": ps._money(promo) if promo is not None else None,
        "promo_months": batch.get("months") if (promo is not None and batch) else None,
    }


async def _downgrade_state(
    supabase: SupabaseClient, user_id: Optional[str], row: Optional[dict]
) -> dict:
    """The five 226 keys of the ``GET /subscription`` payload.

    Empty (nulls + ``[]``) unless the subscription may change plan
    (``_change_allowed``). Then:

    * ``downgrade_options`` — every renewable, purchasable plan ranked below the
      current one, nearest first (max → pro_plus, pro; pro_plus → pro; pro →
      none), each quoted as a fresh buyer NOW;
    * ``pending_*`` — the scheduled change, if any, as PROMISED when it was
      scheduled (the stored columns, never re-quoted on read):
      ``pending_price_sar`` is the most the next renewal will charge (it charges
      less only if a fresh quote at that moment is lower), and
      ``pending_promo_months`` how many months that price lasts when it is an
      offer. Never null while a change is pending — the promise stands even if
      the target has since lost its list price.

    Never raises: a catalog or quote failure costs the options, never the
    dialog — and never the pending change, which is read from the row.
    """
    out = _no_downgrade()
    if not user_id or not _change_allowed(row):
        return out
    assert row is not None                      # _change_allowed proved it
    current_id = str(row.get("plan_id"))
    try:
        catalog = await run_db(_fetch_change_catalog, supabase, current_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("downgrade catalog unreadable for user=%s: %s", user_id, exc)
        catalog = None

    quotes: dict[str, Optional[dict]] = {}
    if catalog is not None:
        current = catalog.get(current_id)
        targets = sorted(
            (p for p in catalog.values() if _is_downgrade_target(current, p)),
            key=lambda p: -(_plan_rank(p.get("plan_id")) or 0),
        )
        for plan in targets:
            quotes[str(plan["plan_id"])] = await run_db(_quote_target, supabase, user_id, plan)
        out["downgrade_options"] = [_option(p, quotes[str(p["plan_id"])]) for p in targets]
        # The options stay listed so the dialog can show what could be picked;
        # the reason says why nothing can be scheduled yet. Without a card the
        # renewal that applies the change cannot run (skipped_no_method) and
        # the term would simply lapse.
        if targets and await run_db(_has_chargeable_method, supabase, user_id) is False:
            out["downgrade_blocked_reason"] = DOWNGRADE_BLOCKED_NO_METHOD

    pending_id = pending_downgrade_of(row)
    if pending_id:
        promise = promise_of(row)
        plan = (catalog or {}).get(pending_id) or {}
        name_ar = plan.get("name_ar")
        if name_ar is None:
            name_ar = await run_db(_fetch_plan_name, supabase, pending_id)
        if promise is not None:
            price, months = promise["price_sar"], promise["months"]
        else:
            # Defensive only: a pending row with no stored promise cannot exist
            # once 226's CHECK is applied. Show today's quote rather than null.
            quote = quotes.get(pending_id)
            if quote is None and plan:
                quote = await run_db(_quote_target, supabase, user_id, plan)
            price = (quote or {}).get("price_sar")
            months = ((quote or {}).get("batch") or {}).get("months")
        out.update({
            "pending_plan_id": pending_id,
            "pending_plan_name_ar": name_ar,
            "pending_price_sar": _payments()._money(price) if price is not None else None,
            "pending_promo_months": months,
        })
    return out


def _same_months(expected: Any, stored: Optional[int]) -> bool:
    """The months the dialog showed (``expected_promo_months``) vs the months
    about to be promised. null ↔ null; an int ↔ the same int; anything else
    (a string, a bool, a float with a fraction) is "not the same"."""
    if expected is None or stored is None:
        return expected is None and stored is None
    if isinstance(expected, bool) or not isinstance(expected, int):
        return False
    return expected == int(stored)


def _write_scheduled_plan(
    supabase: SupabaseClient,
    user_id: str,
    pending: Optional[dict],
) -> bool:
    """Set the five ``_PENDING_COLUMNS`` from ``pending`` — or null all five with
    ``None`` — and NOTHING ELSE: never ``plan_id`` (the assignment trigger, rule
    1), never ``expires_at``. ``updated_at`` rides along for the operator view.
    Returns True when a row was written."""
    patch: dict[str, Any] = {
        **_no_pending(),
        **{k: v for k, v in (pending or {}).items() if k in _PENDING_COLUMNS},
        "updated_at": _now_iso(),
    }
    res = (
        supabase.table("user_subscriptions")
        .update(patch)
        .eq("user_id", user_id)
        .execute()
    )
    return bool(getattr(res, "data", None) or [])


async def schedule_downgrade(
    supabase: SupabaseClient,
    user_id: str,
    plan_id: str,
    expected_price_sar: Any,
    expected_promo_months: Any,
) -> dict:
    """«تغيير الباقة» — move to ``plan_id`` at the next renewal.

    Guards, in order: a paid running term (409 SUBSCRIPTION_NOT_CANCELLABLE) →
    renewal not cancelled (409 SUBSCRIPTION_ALREADY_CANCELLED — undo the cancel
    first) → the target is renewable, purchasable and ranked strictly lower
    (409 SUBSCRIPTION_DOWNGRADE_INVALID) → a chargeable stored card (409
    SUBSCRIPTION_DOWNGRADE_NO_METHOD — the renewal that applies the change needs
    one) → the price AND months about to be promised are the ones the user
    confirmed, ``expected_price_sar`` / ``expected_promo_months`` (409
    SUBSCRIPTION_DOWNGRADE_PRICE_CHANGED — the quote moved between the dialog
    and the tap; nothing is written). Choosing the pending plan again is a
    no-op that keeps the original promise and ``renewal_plan_set_at`` (the
    expected values are not compared then — the stored promise stands and is
    what the response shows); choosing another lower plan replaces both.

    THE PROMISE (owner 2026-11-20): the target is quoted as a fresh buyer NOW
    (``payment_service.fresh_purchase_quote`` — the same call the options were
    priced with) and the price, its batch and months are stored with the
    change. That is the most the renewal will ever charge for it. A quote that
    cannot be read is a 503 — no promise is made that cannot be stated.

    Returns the ``GET /subscription`` payload with the change pending.
    """
    row = await run_db(_fetch_subscription, supabase, user_id)
    if not _is_cancellable(row):
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.SUBSCRIPTION_NOT_CANCELLABLE,
            detail=NO_PAID_SUBSCRIPTION_TO_CHANGE_AR,
        )
    assert row is not None                      # _is_cancellable proved it
    if row.get("renewal_cancelled_at"):
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.SUBSCRIPTION_ALREADY_CANCELLED,
            detail=CHANGE_AFTER_CANCEL_AR,
        )

    target_id = str(plan_id or "").strip()
    current_id = str(row.get("plan_id"))
    try:
        catalog = await run_db(_fetch_change_catalog, supabase, current_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("downgrade: catalog read failed for user=%s: %s", user_id, exc)
        raise _service_unavailable()
    if not _is_downgrade_target(catalog.get(current_id), catalog.get(target_id)):
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.SUBSCRIPTION_DOWNGRADE_INVALID,
            detail=DOWNGRADE_INVALID_AR,
        )

    if pending_downgrade_of(row) == target_id and promise_of(row) is not None:
        return await _state(supabase, user_id, row)       # idempotent

    try:
        has_method = (
            await run_db(payment_method_service.get_chargeable_method, supabase, user_id)
        ) is not None
    except Exception as exc:  # noqa: BLE001
        logger.exception("downgrade: card read failed for user=%s: %s", user_id, exc)
        raise _service_unavailable()
    if not has_method:
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.SUBSCRIPTION_DOWNGRADE_NO_METHOD,
            detail=DOWNGRADE_NO_METHOD_AR,
        )

    ps = _payments()
    try:
        quote = await run_db(
            ps.fresh_purchase_quote, supabase, user_id, catalog.get(target_id)
        )
    except Exception as exc:  # noqa: BLE001 — PricingUnavailable included
        logger.error("downgrade: could not price %s for user=%s: %s", target_id, user_id, exc)
        raise _service_unavailable()
    batch = quote.get("batch") or None
    if batch is not None and ps.q2(quote["price_sar"]) >= ps.q2(catalog[target_id]["price_sar"]):
        # A batch priced at (or above) list is no offer: the dialog shows it as
        # the list price with no months (``_option``), so it is promised as
        # exactly that — otherwise the months it would store could never match
        # what the user was shown, and the confirm would 409 forever.
        batch = None
    months = batch["months"] if batch else None
    # The user confirmed a price for a number of months. If the promise we are
    # about to store differs in either, refuse and let the dialog re-render —
    # never store a promise the user did not see. Unparseable input is simply
    # "not equal".
    if (ps.q2(ps._dec(expected_price_sar, default="-1")) != ps.q2(quote["price_sar"])
            or not _same_months(expected_promo_months, months)):
        logger.info(
            "downgrade: quote moved for user=%s → %s (expected %r × %r, now %s × %r)",
            user_id, target_id, expected_price_sar, expected_promo_months,
            quote["price_sar"], months,
        )
        raise LunaHTTPException(
            status_code=409,
            code=ErrorCode.SUBSCRIPTION_DOWNGRADE_PRICE_CHANGED,
            detail=DOWNGRADE_PRICE_CHANGED_AR,
        )
    pending = {
        "renewal_plan_id": target_id,
        "renewal_plan_set_at": _now_iso(),
        "renewal_plan_price_sar": ps._money(quote["price_sar"]),
        "renewal_plan_batch_id": batch["batch_id"] if batch else None,
        "renewal_plan_promo_months": months,
    }
    try:
        written = await run_db(_write_scheduled_plan, supabase, user_id, pending)
    except Exception as exc:  # noqa: BLE001 — includes 42703 if 226 is unapplied
        logger.exception("downgrade: write failed for user=%s: %s", user_id, exc)
        raise _service_unavailable()
    if not written:
        logger.error("downgrade: write matched no row for user=%s", user_id)
        raise _service_unavailable()

    await run_db(
        write_audit_log,
        supabase,
        user_id=user_id,
        action="update",
        resource_type="subscription",
        metadata={
            "event": "downgrade_scheduled",
            "plan_id": current_id,
            "renewal_plan_id": target_id,
            "promised_price_sar": pending["renewal_plan_price_sar"],
            "promised_batch_id": pending["renewal_plan_batch_id"],
            "promised_months": pending["renewal_plan_promo_months"],
            "replaced_renewal_plan_id": row.get("renewal_plan_id"),
            "expires_at": row.get("expires_at"),
        },
    )
    logger.info(
        "downgrade scheduled: user=%s %s → %s at expires=%s promised=%s batch=%s",
        user_id, current_id, target_id, row.get("expires_at"),
        pending["renewal_plan_price_sar"], pending["renewal_plan_batch_id"],
    )
    return await _state(supabase, user_id, {**row, **pending})


async def cancel_scheduled_downgrade(supabase: SupabaseClient, user_id: str) -> dict:
    """«تراجع» — drop a pending plan change. The renewal goes back to the
    current plan at its current price.

    A no-op 200 when nothing is pending (a double tap, a stale dialog). Returns
    the ``GET /subscription`` payload.
    """
    row = await run_db(_fetch_subscription, supabase, user_id)
    pending = (row or {}).get("renewal_plan_id") or None
    if not pending:
        return await _state(supabase, user_id, row)

    try:
        written = await run_db(_write_scheduled_plan, supabase, user_id, None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("downgrade undo: write failed for user=%s: %s", user_id, exc)
        raise _service_unavailable()
    if not written:
        logger.error("downgrade undo: write matched no row for user=%s", user_id)
        raise _service_unavailable()

    await run_db(
        write_audit_log,
        supabase,
        user_id=user_id,
        action="update",
        resource_type="subscription",
        metadata={
            "event": "downgrade_cancelled",
            "plan_id": (row or {}).get("plan_id"),
            "renewal_plan_id": pending,
        },
    )
    logger.info("downgrade undone: user=%s (was → %s)", user_id, pending)
    return await _state(supabase, user_id, {**(row or {}), **_no_pending()})


# ── called from the paid-fulfilment path (payment_service) ──────────────────
# SYNC, through run_db, and NEVER raising — the same contract as
# clear_renewal_cancellation: by the time they run the plan is granted and the
# money is in. The read uses select("*") so a backend that lands ahead of 226
# degrades to "nothing pending" instead of failing every purchase.


def read_pending_downgrade(supabase: SupabaseClient, user_id: Optional[str]) -> Optional[dict]:
    """``{"plan_id", "renewal_plan_id"}`` when a change is pending, else None.
    Read BEFORE ``grant_plan`` rewrites ``plan_id``."""
    if not user_id:
        return None
    try:
        res = (
            supabase.table("user_subscriptions")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("pending downgrade unreadable for user=%s: %s", user_id, exc)
        return None
    row = rows[0] if rows else None
    if not isinstance(row, dict) or not row.get("renewal_plan_id"):
        return None
    return {"plan_id": row.get("plan_id"), "renewal_plan_id": row.get("renewal_plan_id")}


def clear_scheduled_downgrade(supabase: SupabaseClient, user_id: Optional[str]) -> bool:
    """Drop ``renewal_plan_id`` after a paid grant (an upgrade, a re-purchase, or
    the downgrade renewal itself). Never raises. Returns True when cleared."""
    if not user_id:
        return False
    try:
        written = _write_scheduled_plan(supabase, user_id, None)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "could not clear renewal_plan_id for user=%s after a paid grant (the "
            "plan IS granted; the renewal job ignores a change set before the "
            "current plan started, so a stale value cannot move them): %s",
            user_id, exc,
        )
        return False
    if written:
        logger.info("scheduled downgrade cleared by a paid grant: user=%s", user_id)
    return written


def release_seat_for_downgrade(
    supabase: SupabaseClient,
    user_id: Optional[str],
    *,
    from_plan_id: Optional[str],
    to_plan_id: Optional[str],
    payment_id: Optional[str] = None,
) -> bool:
    """The seat on the plan a scheduled downgrade LEAVES ends at the switch,
    released ``'downgraded'`` — that tier counts as used (D12). ``payment_id``
    (the downgrade renewal's) is stamped as the payment that superseded it, so
    refunding it can give the seat back exactly as for a promo-priced switch.

    Called AFTER ``grant_plan`` and the claim on the same payment — 226's order
    (S2). A promo-priced downgrade's claim has already superseded the old seat
    'downgraded' with the payment's id (a refund then gives it back, V5); this
    then finds the NEW term's own seat and leaves it. A list-priced downgrade
    claims nothing, so this is what ends the old seat. Two cases release
    nothing here, before the RPC is even asked:

    * no live seat;
    * the live seat is a batch seat on the TARGET plan — the new term's own
      seat, or one that keeps pricing the target exactly as it would for
      someone buying that plan fresh (D7).

    The RPC applies the same rule itself (a live seat on the plan the user is
    on NOW is never released 'downgraded'), legacy seats included; this check
    only saves the round trip and keeps the rule readable here.

    Never raises. Returns True when a seat was released.
    """
    if not _seat_applies(user_id, from_plan_id):
        return False
    try:
        res = (
            supabase.table("early_adopter_seats")
            .select("seat_id, plan_id, batch_id")
            .eq("user_id", str(user_id))
            .is_("released_at", "null")
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "downgrade: live seat unreadable for user=%s (%s → %s) — the seat on "
            "the plan they left is NOT released; release it by hand: %s",
            user_id, from_plan_id, to_plan_id, exc,
        )
        return False
    if not rows or not isinstance(rows[0], dict):
        return False
    live = rows[0]
    if live.get("batch_id") and live.get("plan_id") == to_plan_id:
        return False
    return release_early_adopter_seat(
        supabase, user_id, reason=RELEASE_REASON_DOWNGRADED, plan_id=from_plan_id,
        payment_id=payment_id,
    )
