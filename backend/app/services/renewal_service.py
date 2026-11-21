"""The auto-renewal job — charge stored cards for pro/pro_plus/max terms coming due.

Implements `.claude/plans/subscription_auto_renewal.md` §7 (the job) and
`.claude/plans/renewal_retry_grace.md` (the ladder, which supersedes that plan's
§8 dunning): charge AT expiry; on a soft decline keep Pro/Max running for 24h
(the grace, migration 196) while retrying at +12h and +24h from the first
attempt, then lapse to free; a hard decline gives up at once. Registered as a
``CronTrigger`` every 15 minutes from ``backend.app.main``'s lifespan — **and
only when ``settings.SUBSCRIPTION_AUTO_RENEWAL_ENABLED`` is True.** With the
flag down the job is never added to the scheduler at all, so there is no tick,
no query, and no code path that could reach a card.

THIS MODULE MOVES REAL MONEY WITH NOBODY WATCHING. Everything below is written
for that. The five rules, in the order they matter:

1. **Idempotency lives in the DATABASE.** Migration 132's
   ``uniq_payment_renewal_period`` is a partial unique index over
   ``(user_id, plan_id, period_start)`` where ``initiated_by = 'renewal' AND
   status <> 'failed'``. The insert is attempted and a 23505 is the EXPECTED
   "already handled" path — not an error. Two ticks, a redeploy mid-run, or a
   second worker cannot produce two charges for one period, because the second
   INSERT loses at the database rather than the second CHARGE succeeding
   quietly.

   Read the predicate carefully, because the whole design follows from it: the
   slot for a period is released by EXACTLY ONE transition — a row going
   ``failed``. ``initiated`` (unresolved), ``paid``, ``refunded`` and ``expired``
   all keep the period closed. So the dunning ladder works (each decline frees
   the slot for the next attempt) while nothing else can re-charge a period,
   and — the important one — an ``initiated`` row whose outcome we never saw
   blocks at the DATABASE, not merely in the pre-filter below.
2. **The ledger row is written BEFORE the provider is called.** Same rule as
   "the user's message is saved before the AI call": if this process dies
   between the insert and the response, the row is there, it is ``initiated``,
   and rule 3 makes that state fail-closed.
3. **Ambiguity is fail-closed.** A charge whose outcome we do not know leaves
   the row ``initiated``. Any renewal row for the period in ``initiated`` OR
   ``paid`` blocks every further attempt for that period. A renewal that needs a
   human is infinitely better than one that charges twice.
4. **The EFFECTIVE price is the price, read at charge time.** Never from the
   last payment, never from a cached figure, and there is no client anywhere
   near this path. Since migration 138 that price is
   ``effective_plan_price(user, plan)`` and not ``plans.price_sar`` — one
   definition shared with checkout and the upgrade credit
   (`.claude/plans/early_adopters.md` §2). **This is the line that keeps the
   90-day promise made to المشتركون الأوائل:** the job re-reads the price at
   every charge, so quoting the catalog would step an early adopter up to the
   list price on a saved card the moment the campaign closed — automatically,
   with no warning, which is exactly the failure the promotion was designed not
   to have.
5. **The term extends from the OLD ``expires_at``, never from ``now()``.** That
   arithmetic is not re-implemented here: ``grant_plan`` (092) already does
   exactly it — a same-plan grant on a still-live term sets
   ``expires_at + duration_days`` and keeps ``started_at`` — under a
   ``FOR UPDATE`` lock, with ``fulfilled_at`` as its own idempotency anchor.
   Re-deriving the term in Python would be a second copy of the money-critical
   arithmetic, and the copies would drift. The ONE bounded exception is a
   success inside the renewal grace, where grant_plan cannot stack (its term is
   no longer live): ``_keep_the_billing_date`` writes grant_plan's own stacking
   value — old expiry + ``duration_days`` — and only by compare-and-set on the
   row grant_plan just wrote.

WHAT IS DELIBERATELY REUSED FROM ``payment_service`` ───────────────────────────
The whole "money landed, record it and grant" half: ``_assert_matches`` (the
provider must have charged what our row says), ``_mark_paid_and_grant`` (paid →
prior snapshot → grant → usage reset → clear opt-out → audit → **receipt
email**) and ``_mark_failed``. A renewal is not a different kind of payment; it
is the same payment with a different initiator, and giving it a parallel
fulfilment path would be how the two drift apart.

WHY ``grant_plan`` AND NOT A HAND-WRITTEN ``expires_at`` UPDATE ────────────────
132's §4 header suggests the success write be
``SET expires_at = expires_at + make_interval(days => …)``. This module
deliberately does not do that, and the reason is not style:

* ``grant_plan`` computes the identical value for the day-0 case (same plan,
  live term → stack onto ``expires_at``), so nothing is lost;
* it stamps ``payment_transactions.fulfilled_at``. Without that stamp
  ``revoke_plan_grant`` answers ``not_fulfilled`` and a REFUNDED renewal would
  return the money **and leave the extra 30 days standing** (plan §10, refunds
  apply per renewal charge now). A hand-written UPDATE silently breaks the
  refund path;
* it takes ``FOR UPDATE`` on both the payment and the subscription, and
  re-checks user/plan/status — locks and cross-checks this module would
  otherwise have to reimplement.

The one case grant_plan does not cover: a retry that lands INSIDE the grace
finds the term already past its ``expires_at``, and ``grant_plan`` only stacks
onto a live term — it opens a fresh window from ``now()``, which would slip the
billing date by up to a day on every declined-once card. ``_keep_the_billing_date``
moves the row back to ``period_start + duration_days`` in a separate write
(renewal_retry_grace.md §3 option B2 — grant_plan itself is not edited). See
that function for why the write is safe against the assignment trigger.

DB dependency: migrations ``132_subscription_auto_renewal.sql`` and
``196_renewal_grace.sql`` (⚠ APPLY 196 BEFORE DEPLOYING THIS: the selection
queries and the bookkeeping write name ``renewal_grace_until``; ahead of it the
sweep logs a 42703 every tick and charges nobody). Columns this module names:

* ``payment_transactions`` (132): ``initiated_by`` ('user'|'renewal', default
  'user'), ``renewal_attempt`` (int, default 0), ``period_start`` (timestamptz,
  NOT NULL for a renewal by CHECK), ``payment_method_id`` (uuid, ON DELETE SET
  NULL);
* ``uniq_payment_renewal_period`` (132) — the guard in rule 1;
* ``user_subscriptions``: ``renewal_attempt_at`` (timestamptz),
  ``renewal_failed_count`` (int, default 0) — 132; ``renewal_grace_until``
  (timestamptz, NULL) — 196, written by this module and nothing else except the
  cancel / re-purchase paths that END a grace;
* ``plans.billing_cycle`` = 'recurring_30d' for pro, pro_plus and max.

THE SCHEDULED DOWNGRADE (migration ``226_scheduled_downgrade.sql``,
`.claude/plans/pro_plus_tier_and_downgrade.md` §4) ──────────────────────────
When ``user_subscriptions.renewal_plan_id`` names a LOWER renewable plan
(``subscription_service.pending_downgrade_of``), the renewal charges and grants
THAT plan instead of the current one, never at the current plan's 'current'
price. It charges the LOWER of the price PROMISED when the change was scheduled
(``renewal_plan_price_sar`` / ``_batch_id`` / ``_promo_months``, owner
2026-11-20) and a fresh-purchase quote taken now
(``payment_service.fresh_purchase_quote``, D7) — see ``_price_downgrade``. The
promise holds even if the target has lost its list price or its offer batch
has filled or closed: the change is never skipped for that.

* the ledger row is for the target plan, stamped ``promo_batch_id`` +
  ``intro_offer_terms`` when the price came from a batch (today's or the
  promised one), so the shared fulfilment claims that seat — over capacity or
  switched off included — then releases the left plan's seat ``'downgraded'``
  and clears the change;
* the period key is unchanged (the old ``expires_at``), and the pre-filter
  looks at EVERY plan's rows for that period, so a change scheduled or undone
  mid-ladder can never open a second charge for one period;
* ``plan_changed_mid_charge`` still compares against the subscription's
  ORIGINAL plan — the one selected — never the target;
* every retry rung on the grace ladder charges what rung 0 chose, verbatim;
* ``_keep_the_billing_date`` puts the new plan's term at old expiry +
  ``duration_days`` (grant_plan opens a plan change from ``now()``).

⚠ APPLY 226 BEFORE DEPLOYING: the selection names ``renewal_plan_id`` and
``renewal_plan_set_at``; ahead of it every tick 42703s and charges nobody
(196's trap, same rule).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from supabase import Client as SupabaseClient

from backend.app.services import payment_method_service as pm
from backend.app.services import payment_service as ps
from backend.app.services import subscription_service as ss
from backend.app.services.audit_service import write_audit_log
from backend.app.services.receipt_service import (
    send_payment_receipt,
    send_renewal_failed_notice,
)
from shared.config import get_settings
from shared.db.run import run_db

logger = logging.getLogger(__name__)

# ── Selection window (renewal_retry_grace.md §0.1: charge AT expiry) ─────────
# The job ticks every 15 minutes, so a 15-minute horizon puts the first attempt
# on the last tick BEFORE the term lapses — at expiry, not up to a day early. In
# the normal case that attempt still sees a LIVE term, which is what makes
# grant_plan stack the renewal onto the old expiry; one that lands late (a
# missed tick, a retry inside the grace) is put back by _keep_the_billing_date.
DUE_HORIZON = timedelta(minutes=15)

# ── Dunning ladder (renewal_retry_grace.md §0.2): 0, +12h, +24h, then lapse ──
# Expressed as the delay from the FIRST attempt. Attempt N is eligible once
# LADDER_HOURS[N] − LADDER_HOURS[N−1] hours have passed since the last attempt…
LADDER_HOURS: tuple[int, ...] = (0, 12, 24)
MAX_ATTEMPTS = len(LADDER_HOURS)

# …less this slack. `renewal_attempt_at` is stamped seconds AFTER the tick that
# ran the attempt, so "12h since the last attempt" first becomes true seconds
# after the tick 12h later — and without slack every rung slipped a whole tick.
# (The daily job slipped a whole DAY that way: prod 2026-10-28/29.) Well under
# the 15-minute tick, so a rung can never fire a tick early either.
LADDER_SLACK = timedelta(minutes=10)

# ── The grace (renewal_retry_grace.md §0.1, §3 — migration 196) ─────────────
# A soft decline keeps Pro/Max running while the ladder retries: the first one
# writes renewal_grace_until = GREATEST(expires_at, now) + GRACE_PERIOD — 24h
# for the ladder plus 30 min for the last attempt to finish — and
# get_user_quota_state reads the term as live until then. Anchored on now() as
# well as expires_at so a first attempt that ran late still gets its whole
# ladder inside the grace.
GRACE_PERIOD = timedelta(hours=24, minutes=30)

# No charge runs later than this after ACCESS ended: past expires_at for a first
# attempt (a missed tick), past renewal_grace_until for a retry (a late tick).
# Beyond it the user has been on the free plan long enough that a charge would
# be a surprise, not a renewal — and a silent charge is a chargeback.
RETRY_GRACE = timedelta(hours=2)

# The retry query's coarse SQL bound on expires_at: the latest a grace can end
# (a first attempt up to RETRY_GRACE late, then GRACE_PERIOD) plus RETRY_GRACE.
# The exact gate is per row, in _retry_is_due.
RETRY_LOOKBACK = RETRY_GRACE + GRACE_PERIOD + RETRY_GRACE

# ── Hard declines (renewal_retry_grace.md §0.3) ──────────────────────────────
# Bank response codes, read from the provider payload's
# `source.response_code`. A retry cannot fix any of these, so the ladder gives
# up at once: no retries, no grace, the final email. 12 invalid transaction ·
# 14 invalid card number · 41 lost · 43 stolen · 54 expired card · 57 not
# permitted to the cardholder · 62 restricted card. Everything else — 51
# insufficient funds, 05 do not honour, an unknown code, or no code at all —
# is SOFT and gets the whole ladder.
HARD_DECLINE_CODES: frozenset[str] = frozenset({"12", "14", "41", "43", "54", "57", "62"})

# Ticks are cheap; a runaway is not. Caps the blast radius of a bad query and
# keeps one tick inside a sane wall-clock (each renewal is one provider call).
BATCH_CAP = 200

# The ONE status that releases a period's slot, mirroring the predicate of
# 132's uniq_payment_renewal_period (`status <> 'failed'`). Every other status —
# initiated (unresolved), paid, refunded, expired — keeps the period closed.
#
# The pre-filter below and the DB index must agree exactly, or the pre-filter
# would wave through a case the index then rejects with a 23505 that reads like
# a bug. One constant, both users.
SLOT_RELEASING_STATUS = "failed"

_SUBSCRIPTION_COLUMNS = (
    "user_id, plan_id, source, started_at, expires_at, renewal_cancelled_at, "
    "renewal_attempt_at, renewal_failed_count, renewal_grace_until, "
    # 226 — the scheduled downgrade AND its promise (all five pending columns:
    # ss.promise_of reads the last three — without them the promise would never
    # be honoured). ⚠ 226 strictly before deploy.
    "renewal_plan_id, renewal_plan_set_at, renewal_plan_price_sar, "
    "renewal_plan_batch_id, renewal_plan_promo_months"
)

# "Leave this column alone" for _write_renewal_bookkeeping — None means "clear".
_KEEP: Any = object()

# Skip / blocked lines are logged ONCE per (outcome, user, period) per process.
# Every term is re-selected on each 15-minute tick of its window, so a blocked
# or card-less renewal would otherwise log the same line ~10 times (and a daily
# reader would have to dedupe it by hand). Later repeats go to DEBUG.
_LOGGED_ONCE: set[tuple[str, str, str]] = set()
_LOGGED_ONCE_CAP = 10_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_ts(value: Any) -> Optional[datetime]:
    """PostgREST timestamptz → aware datetime (UTC-assumed). Same parser shape as
    ``payment_service._parse_ts`` — they read the same columns."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _log_once(level: int, key: tuple[str, str, str], msg: str, *args: Any) -> None:
    """``logger.log`` the first time ``key`` is seen in this process, DEBUG after."""
    if key in _LOGGED_ONCE:
        logger.debug(msg, *args)
        return
    if len(_LOGGED_ONCE) >= _LOGGED_ONCE_CAP:
        _LOGGED_ONCE.clear()                 # a bound, not a cache policy
    _LOGGED_ONCE.add(key)
    logger.log(level, msg, *args)


def _decline_code(fetched: dict) -> Optional[str]:
    """The bank's response code from ``source.response_code``, or None.

    Normalised to a string ("5" and 5 both read "05"), because the hard-decline
    set is compared as text and a provider is free to send either type.
    """
    source = fetched.get("source") if isinstance(fetched.get("source"), dict) else {}
    raw = source.get("response_code")
    if raw is None:
        return None
    code = str(raw).strip()
    if code.isdigit() and len(code) == 1:
        code = code.zfill(2)
    return code or None


# ═══════════════════════════════════════════════════════════════════════════
# 1. Selection — every condition in the plan's §7 is a hard gate
# ═══════════════════════════════════════════════════════════════════════════


def _select_due_first_attempt(supabase: SupabaseClient, now: datetime) -> list[dict]:
    """Terms coming due inside the horizon that have not been attempted yet.

    Every filter here is load-bearing and every one of them is in the plan:

    * ``plan_id IN pm.RENEWABLE_PLAN_IDS`` (pro, pro_plus, max) — basic does
      not renew and says so on the card. It is the CURRENT plan that is
      selected; a scheduled downgrade's target is resolved in ``_renew_one``;
    * ``source = 'payment'`` — a code / marketing / manual / signup grant has no
      card behind it and must never be touched (plan §1 rule 1, the same
      visibility rule the cancel feature uses);
    * ``renewal_cancelled_at IS NULL`` — the opt-out the cancel UI has been
      writing since 120 becomes load-bearing HERE. This is the filter that makes
      «لن يُجدَّد اشتراكك» true;
    * ``expires_at`` inside [now − RETRY_GRACE, now + 15 min] — due at the next
      tick. The lower bound is not ``now``: a term whose last tick before expiry
      was missed (a restart, a slow sweep) is still charged by the next one,
      instead of lapsing with no attempt at all — but never more than
      RETRY_GRACE after it lapsed;
    * ``renewal_failed_count = 0`` — a term already in the ladder is picked up by
      ``_select_due_retry`` instead, on its own clock.
    """
    res = (
        supabase.table("user_subscriptions")
        .select(_SUBSCRIPTION_COLUMNS)
        .in_("plan_id", sorted(pm.RENEWABLE_PLAN_IDS))
        .eq("source", "payment")
        .is_("renewal_cancelled_at", "null")
        .eq("renewal_failed_count", 0)
        .gte("expires_at", _iso(now - RETRY_GRACE))
        .lte("expires_at", _iso(now + DUE_HORIZON))
        .limit(BATCH_CAP)
        .execute()
    )
    return list(getattr(res, "data", None) or [])


def _select_due_retry(supabase: SupabaseClient, now: datetime) -> list[dict]:
    """Terms mid-ladder whose next retry is due (renewal_retry_grace.md §4).

    The clock is ``renewal_attempt_at``; the gate is computed per row in
    ``_retry_is_due`` because the interval depends on how many attempts have
    already failed (0→12h, 1→12 more = +24h from the start) and because the
    grace that bounds it is per row.

    ``expires_at >= now − RETRY_LOOKBACK`` is the coarse half of "never
    resurrect a lapsed term" — the latest any grace can end, plus RETRY_GRACE.
    The exact half (no retry more than RETRY_GRACE after the row's own grace
    ended) is ``_retry_is_due``.

    ``expires_at <= now + DUE_HORIZON`` is the other bound: a retry is only ever
    for a term that is due or just lapsed. A term a month out means the user
    bought again at checkout mid-ladder — charging it would bill the NEXT period
    ~30 days early (prod 2026-10-30). The checkout also resets the count; this
    holds even if that write failed.
    """
    res = (
        supabase.table("user_subscriptions")
        .select(_SUBSCRIPTION_COLUMNS)
        .in_("plan_id", sorted(pm.RENEWABLE_PLAN_IDS))
        .eq("source", "payment")
        .is_("renewal_cancelled_at", "null")
        .gt("renewal_failed_count", 0)
        .lt("renewal_failed_count", MAX_ATTEMPTS)
        .gte("expires_at", _iso(now - RETRY_LOOKBACK))
        .lte("expires_at", _iso(now + DUE_HORIZON))
        .limit(BATCH_CAP)
        .execute()
    )
    rows = list(getattr(res, "data", None) or [])
    return [row for row in rows if _retry_is_due(row, now)]


def _grace_ends_at(row: dict) -> Optional[datetime]:
    """When access ends for a term mid-ladder.

    ``renewal_grace_until`` when the first decline wrote one; for a row whose
    ladder began before 196 (or whose grace write was lost), where it WOULD
    have been — so such a row still finishes its ladder and gets its final
    email instead of stalling at failed_count 1.
    """
    grace = _parse_ts(row.get("renewal_grace_until"))
    if grace is not None:
        return grace
    expires_at = _parse_ts(row.get("expires_at"))
    return expires_at + GRACE_PERIOD if expires_at is not None else None


def _retry_is_due(row: dict, now: datetime) -> bool:
    """Has the ladder's next rung come around for this subscription?"""
    failed = int(row.get("renewal_failed_count") or 0)
    if not (0 < failed < MAX_ATTEMPTS):
        return False
    ends = _grace_ends_at(row)
    if ends is not None and now > ends + RETRY_GRACE:
        # Access ended a while ago (an outage ate the last rung). The user has
        # been on the free plan since; charging them now would be a surprise.
        return False
    wait = timedelta(hours=LADDER_HOURS[failed] - LADDER_HOURS[failed - 1])
    last = _parse_ts(row.get("renewal_attempt_at"))
    if last is None:
        # Failed count with no timestamp: a partially-written state. Treat it as
        # due rather than stranding the subscription — the per-period guard
        # below still makes a double charge impossible.
        return True
    return now - last >= wait - LADDER_SLACK


def _existing_renewal_rows(
    supabase: SupabaseClient, user_id: str, plan_id: Optional[str], period_start: str
) -> list[dict]:
    """Every renewal row already written for THIS user + period (+ plan, when
    ``plan_id`` is given).

    ⚠ This is NOT the idempotency guard — the partial unique index is (rule 1).
    It is a cheap pre-filter that (a) skips work before a provider call, and
    (b) is the ONLY thing standing between an ambiguous ``initiated`` row and a
    second charge, because an ambiguous row is not a constraint violation for
    the NEXT attempt number. Both walls are needed; neither replaces the other.

    ``_renew_one`` passes ``plan_id=None`` since 226: the unique index is per
    (user, PLAN, period), and a scheduled downgrade means one period can be
    attempted under two plans — the target, then the current one after an undo
    (or the reverse). An ambiguous row under EITHER plan must block the period,
    so the pre-filter reads them all. For a term that never changes plan this is
    the same set as before: a period key is one term boundary.
    """
    query = (
        supabase.table("payment_transactions")
        .select(
            "payment_id, plan_id, status, renewal_attempt, period_start, created_at, "
            # a downgrade's retry rung repeats rung 0's price + stamps
            # (_price_downgrade) — every column here predates 226 (132/183/192)
            "amount_sar, promo_batch_id, intro_offer_terms, intro_offer_group"
        )
        .eq("user_id", user_id)
    )
    if plan_id is not None:
        query = query.eq("plan_id", plan_id)
    res = (
        query
        .eq("initiated_by", ps.INITIATED_BY_RENEWAL)
        .eq("period_start", period_start)
        .execute()
    )
    return list(getattr(res, "data", None) or [])


# ═══════════════════════════════════════════════════════════════════════════
# 2. Bookkeeping on user_subscriptions — the flag write, and NOTHING else
# ═══════════════════════════════════════════════════════════════════════════


def _select_one_subscription(supabase: SupabaseClient, user_id: str) -> Optional[dict]:
    """Re-read one subscription. Used as the just-before-grant sanity check.

    limit(1) rather than maybe_single(), for the reason spelled out in
    ``payment_service._fetch_subscription``.
    """
    res = (
        supabase.table("user_subscriptions")
        .select(_SUBSCRIPTION_COLUMNS)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = getattr(res, "data", None) or []
    return rows[0] if rows else None


def _write_renewal_bookkeeping(
    supabase: SupabaseClient,
    user_id: str,
    *,
    attempt_at: str,
    failed_count: int,
    grace_until: Any = _KEEP,
) -> bool:
    """Stamp the dunning state. NEVER ``plan_id``.

    ``renewal_attempt_at`` / ``renewal_failed_count`` always;
    ``renewal_grace_until`` only when given (an ISO string opens or ends a
    grace, None clears it, ``_KEEP`` leaves it as it is — the middle rung).

    ⚠ ``trg_user_subscriptions_assignment`` is ``BEFORE UPDATE OF plan_id`` and
    re-derives ``expires_at`` from ``plans.duration_days``. An UPDATE whose
    column list included ``plan_id`` would silently re-stamp the term — the same
    "set expiry ALONE" trap that bit 120, and here it would rewrite the very
    term this job just paid to extend.

    ``expires_at`` is likewise absent: ``grant_plan`` (and, for a success inside
    the grace, ``_keep_the_billing_date``) owns it. This function only ever
    writes dunning state.
    """
    patch: dict[str, Any] = {
        "renewal_attempt_at": attempt_at,
        "renewal_failed_count": int(failed_count),
        "updated_at": _iso(_now()),
    }
    if grace_until is not _KEEP:
        patch["renewal_grace_until"] = grace_until
    res = (
        supabase.table("user_subscriptions")
        .update(patch)
        .eq("user_id", user_id)
        .execute()
    )
    return bool(getattr(res, "data", None) or [])


def _restack_onto_period(
    supabase: SupabaseClient,
    user_id: str,
    plan_id: str,
    *,
    granted_expires_at: str,
    expires_at: str,
    started_at: Optional[str],
) -> bool:
    """Write ``expires_at`` (+ ``started_at``) back to what stacking would give.

    Compare-and-set on the expiry ``grant_plan`` just wrote AND on ``plan_id``:
    if anything moved the row in between (an upgrade, a stacked purchase, a
    refund), this matches no row and that newer state stands. Returns True
    when the row was moved.

    Neither column is ``plan_id``, so ``trg_user_subscriptions_assignment``
    (BEFORE UPDATE OF plan_id) does not fire for this statement.
    """
    patch: dict[str, Any] = {"expires_at": expires_at, "updated_at": _iso(_now())}
    if started_at:
        patch["started_at"] = started_at
    res = (
        supabase.table("user_subscriptions")
        .update(patch)
        .eq("user_id", user_id)
        .eq("plan_id", plan_id)
        .eq("expires_at", granted_expires_at)
        .execute()
    )
    return bool(getattr(res, "data", None) or [])


async def _keep_the_billing_date(
    supabase: SupabaseClient,
    *,
    subscription: dict,
    plan: dict,
    granted_expires_at: Optional[str],
    plan_id: Optional[str] = None,
) -> Optional[str]:
    """A success inside the grace keeps the billing date (renewal_retry_grace.md §3, B2).

    ``grant_plan`` stacks only onto a term that is still live
    (``expires_at > now()``). A renewal that lands after expiry — a retry in
    the grace, or a first attempt on a late tick — opens a fresh window from
    ``now()`` instead: the billing date slips by up to a day each time a card is
    declined once, and the grace hours become free. This puts the row where a
    stacked renewal would have left it — the period this charge bought starts
    at the OLD expiry (its ``period_start``), and ``started_at`` is the one the
    continuous subscription already had. On an on-time renewal grant_plan has
    already done exactly that and this writes nothing.

    WHY NOT EDIT ``grant_plan`` (option B1). 113's rule — the live money-path
    RPC is not edited for a side concern — and 194 (store purchases) asserts it
    untouched. A separate write is safe because of two facts read live on
    2026-10-30 and asserted again by 196 §0 at apply time:

    * ``trg_user_subscriptions_assignment`` is BEFORE UPDATE **OF plan_id**, and
      the write below never names plan_id, so it does not fire;
    * where it does fire — grant_plan's upsert names plan_id — it re-derives
      ``expires_at`` only when ``plan_id IS DISTINCT FROM OLD.plan_id``. A
      same-plan renewal is never re-stamped behind this function's back.

    Never raises: the money is in and the plan IS granted; the worst a failure
    here leaves is the more generous fresh window. Returns the expiry that now
    stands.

    ``plan_id`` (226) — the plan just GRANTED, when a scheduled downgrade made
    it differ from ``subscription['plan_id']``. grant_plan never stacks across
    plans: it opened the new plan from ``now()``, so even an ON-TIME downgrade
    (charged up to 15 min before the boundary) lands a few minutes short of the
    billing date. A plan change is therefore put at old expiry +
    ``duration_days`` in BOTH directions — the period this charge bought — and
    ``started_at`` is left as grant_plan stamped it (a new plan began; the old
    plan's start is not this one's).
    """
    user_id = str(subscription.get("user_id"))
    old_plan_id = str(subscription.get("plan_id"))
    plan_id = str(plan_id or old_plan_id)
    plan_changed = plan_id != old_plan_id
    old_expiry = _parse_ts(subscription.get("expires_at"))
    granted = _parse_ts(granted_expires_at)
    days = plan.get("duration_days")
    if old_expiry is None or granted is None or not days:
        return granted_expires_at
    target = old_expiry + timedelta(days=int(days))
    if granted == target or (granted < target and not plan_changed):
        return granted_expires_at          # stacked already — the on-time case
    try:
        moved = await run_db(
            _restack_onto_period,
            supabase,
            user_id,
            plan_id,
            granted_expires_at=str(granted_expires_at),
            expires_at=_iso(target),
            started_at=None if plan_changed else subscription.get("started_at"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "renewal granted from now() instead of the old expiry for user=%s "
            "(%s instead of %s) and the restack failed — the customer keeps the "
            "longer term: %s", user_id, granted_expires_at, _iso(target), exc,
        )
        return granted_expires_at
    if not moved:
        logger.warning(
            "renewal restack matched no row for user=%s — the subscription moved "
            "after the grant; keeping %s", user_id, granted_expires_at,
        )
        return granted_expires_at
    logger.info(
        "renewal inside the grace restacked onto the old expiry: user=%s %s → %s",
        user_id, granted_expires_at, _iso(target),
    )
    return _iso(target)


# ═══════════════════════════════════════════════════════════════════════════
# 3. One renewal
# ═══════════════════════════════════════════════════════════════════════════


def _charge_definitely_did_not_happen(exc: ps.MoyasarError) -> bool:
    """Can we PROVE the card was not charged? Only then may the ladder advance.

    The distinction is the difference between a customer who gets a "your card
    was declined" email and a customer who gets billed twice:

    * a 4xx (other than the two retry-ish ones) is Moyasar rejecting the request
      before taking money — a dead token, a malformed body, a declined
      authorization. Provable no-charge → ordinary decline;
    * a transport failure, a timeout, a 5xx, a non-JSON body, or a 408/429 means
      we have NO IDEA. Unprovable → ambiguous → the period is blocked.

    ``MoyasarNotFound`` is a 404 and belongs to the first group: the endpoint or
    the token does not exist for our key, so nothing was captured. It is still
    logged loudly by the caller, because a 404 on ``POST /payments`` more likely
    means the UNVERIFIED request shape is wrong than that the token died.
    """
    if isinstance(exc, ps.MoyasarUnavailable):
        return False
    status = getattr(exc, "status", None)
    if not isinstance(status, int):
        return False                      # non-JSON body / unknown shape
    if status in (408, 429):
        return False                      # timeout / throttled — outcome unknown
    return 400 <= status < 500


async def _current_price(
    supabase: SupabaseClient,
    user_id: str,
    plan_id: str,
    plan: dict,
    expires_at: Optional[datetime],
) -> Decimal:
    """What an ORDINARY renewal (same plan, no scheduled change) charges.

    `plans.price_sar` is the LIST price and this job charges what THIS user's
    RUNNING TERM costs — context 'current', which is the live-seat branch only.

    Both halves are load-bearing:
      * a seat holder inside their 90 days is charged the promo rate, and the
        first period that BEGINS after the window is full price (§1.3). This is
        the line that keeps the 90-day promise: the job re-reads the price at
        every charge, so quoting the catalog would step an early adopter up the
        moment the campaign closed, on a saved card, with no warning;
      * 'purchase' here would be worse in the other direction — while the
        campaign is open it would hand every renewing NON-member an automatic
        discount they never asked for, on a charge they did not choose to make.
        A renewal is not a purchase decision, so it can neither win the promo
        price nor enrol anybody (see the claim in payment_service, which is
        skipped for `initiated_by='renewal'`). The ONE exception is a scheduled
        downgrade, which IS a decision and is priced as one
        (``ps.fresh_purchase_quote``) — never through here.

    A failed lookup falls back to the catalog inside ``_effective_price`` — the
    pre-campaign behaviour, and the only fallback that keeps this job charging
    at all when 138 is missing.

    `at=expires_at` (migration 182): the seat is tested at the START of the
    period being bought, not at now(). The first attempt runs just before that
    boundary (up to 24h before it until 196) and a retry runs after it, so
    testing at now() would price the period by when the tick happened — the
    24h case billed the period beginning on day 90 at the promo rate, a fourth
    promo charge for every seat holder.
    """
    return await run_db(
        ps._effective_price,
        supabase,
        user_id,
        plan_id,
        plan.get("price_sar"),
        context=ps.PRICE_CONTEXT_CURRENT,
        at=expires_at,
    )


_STAMP_COLUMNS: tuple[str, ...] = ("promo_batch_id", "intro_offer_terms", "intro_offer_group")


def _row_stamp(row: dict) -> dict:
    """The batch stamps an earlier renewal row carried, verbatim."""
    return {k: row[k] for k in _STAMP_COLUMNS if row.get(k)}


def _fresh_stamp(quote: dict, plan: dict) -> dict:
    """Stamps for a fresh quote — what checkout writes for the same quote."""
    stamp: dict[str, Any] = {}
    batch = quote.get("batch")
    if batch:
        stamp["promo_batch_id"] = batch["batch_id"]
        stamp["intro_offer_terms"] = ps._batch_terms(batch, plan)
    if quote.get("intro_group"):
        stamp["intro_offer_group"] = quote["intro_group"]
    return stamp


def _promise_stamp(
    supabase: SupabaseClient, user_id: str, plan: dict, promise: dict
) -> dict:
    """Stamps for charging the PROMISED price. Sync — through ``run_db``.

    A promise from a batch stamps that batch and the promised terms (price +
    months as stored), so the claim seats it even if the batch has since
    filled (over_capacity) or been switched off — 192's claim never reads
    ``enabled``.

    No ``intro_offer_group``, ever: a promise is a fresh quote taken with
    ``p_as_new``, and a downgrade never gets Pro Tier 1 (the profession offer —
    owner 2026-11-20, 225's promo_quote), so a promised batch is never one
    whose first payment is non-refundable. Never raises.
    """
    batch_id = promise.get("batch_id")
    if not batch_id:
        return {}
    months = promise.get("months")
    if not months:
        # Defensive only: 226's CHECK stores months whenever a batch is stored.
        try:
            res = (
                supabase.table("promo_batches")
                .select("batch_id, months")
                .eq("batch_id", batch_id)
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
            if rows:
                months = ps._quote_months(rows[0].get("months"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("promised batch %s unreadable for user=%s: %s", batch_id, user_id, exc)
    stamp: dict[str, Any] = {"promo_batch_id": batch_id}
    if months:
        stamp["intro_offer_terms"] = ps._batch_terms(
            {"price_sar": promise["price_sar"], "months": int(months)}, plan
        )
    return stamp


def _fresh_beats_promise(fresh: dict, promise: dict, plan: dict) -> bool:
    """Is today's fresh quote STRICTLY cheaper than the promise, in total?

    Comparing only the first charge loses months: a promised 19.90 × 2 against
    a fresh 19.90 × 1 is a tie on the first charge and 75.00 dearer over two
    months. So both are costed over the same horizon H = the longer of the two
    offers (at least one month), each paying its offer price for its own months
    and the target's LIST price for the rest:

        total = price × months + list × (H − months)

    A quote without a batch counts as one month at its price. The fresh quote
    wins only when its total is strictly lower — a tie keeps the promise (its
    batch, its months). A target with no list price has no fresh quote at all
    (``fresh_purchase_quote`` refuses it), so this is never asked then; the
    guard below says the same thing defensively.

    AND the first charge never exceeds the promise: ``pending_price_sar`` is
    shown as the most the next renewal will charge, so a fresh quote whose
    first charge is ABOVE the promised one loses whatever its total (promise
    79.90 × 1 vs fresh 99.90 × 2 → the promise, though the offer would be
    cheaper over two months).
    """
    list_price = plan.get("price_sar")
    if list_price is None:
        return False
    if ps.q2(fresh["price_sar"]) > ps.q2(promise["price_sar"]):
        return False
    lst = ps.q2(list_price)
    fresh_months = int((fresh.get("batch") or {}).get("months") or 1)
    promise_months = int(promise.get("months") or 1)
    horizon = max(fresh_months, promise_months, 1)

    def total(price: Any, months: int) -> Decimal:
        return ps.q2(price) * months + lst * (horizon - months)

    return total(fresh["price_sar"], fresh_months) < total(promise["price_sar"], promise_months)


async def _price_downgrade(
    supabase: SupabaseClient,
    *,
    subscription: dict,
    current_plan: Optional[dict],
    plan: dict,
    attempt: int,
    period_start: str,
) -> Optional[dict]:
    """What a scheduled downgrade's renewal charges → ``{"price": Decimal,
    "stamp": dict, "source": "first_rung" | "fresh" | "promise"}``, or None
    when there is neither a promise nor a fresh quote.

    THE RULE (owner 2026-11-20), in order:

    1. **A retry rung repeats rung 0.** On the grace ladder (attempt ≥ 1) the
       price and stamps of the earliest renewal row for this period AND this
       target plan are charged again, verbatim. Simplest correct rule: the
       first dunning email already told the user that amount, and re-deciding
       could RAISE it between rungs (a fresh offer that filled after rung 0).
       If the user changed the target mid-ladder, no such row exists and the
       decision is taken fresh.
    2. **The cheaper of the promise and a fresh quote, over the offer's
       horizon** (``_fresh_beats_promise``). The fresh quote is taken only while
       the target is still a renewable, purchasable lower plan
       (``fresh_purchase_quote``; an error reads as "no quote"). It wins — and
       is charged with ITS batch — only when its first charge is no higher
       than the promised one AND it is STRICTLY cheaper in total;
       otherwise the PROMISE is charged with the promised batch
       (``_promise_stamp``), even when the target has no list price any more,
       its batch is full, or it is switched off. The promise is a ceiling,
       never a floor.
    """
    user_id = str(subscription.get("user_id"))
    plan_id = str(plan.get("plan_id"))

    if attempt > 0:
        rows = await run_db(_existing_renewal_rows, supabase, user_id, plan_id, period_start)
        earlier = [
            r for r in rows
            if int(r.get("renewal_attempt") or 0) < attempt and r.get("amount_sar") is not None
        ]
        if earlier:
            first = min(earlier, key=lambda r: int(r.get("renewal_attempt") or 0))
            return {"price": ps.q2(first["amount_sar"]), "stamp": _row_stamp(first),
                    "source": "first_rung"}

    promise = ss.promise_of(subscription)
    fresh: Optional[dict] = None
    if ss._is_downgrade_target(current_plan, plan):
        try:
            fresh = await run_db(ps.fresh_purchase_quote, supabase, user_id, plan)
        except ps.PricingUnavailable as exc:
            logger.warning(
                "downgrade renewal: no fresh quote for user=%s plan=%s (%s) — "
                "charging the promise", user_id, plan_id, exc,
            )
            fresh = None

    if fresh is not None and (promise is None or _fresh_beats_promise(fresh, promise, plan)):
        return {"price": fresh["price_sar"], "stamp": _fresh_stamp(fresh, plan),
                "source": "fresh"}
    if promise is not None:
        stamp = await run_db(_promise_stamp, supabase, user_id, plan, promise)
        return {"price": promise["price_sar"], "stamp": stamp, "source": "promise"}
    return None


async def _renew_one(supabase: SupabaseClient, subscription: dict) -> str:
    """Attempt one renewal. Returns a short outcome tag for the sweep's stats.

    Outcomes: ``renewed`` · ``declined`` · ``lapsed`` (ladder exhausted) ·
    ``skipped_*`` (a gate refused) · ``ambiguous`` (provider outcome unknown —
    needs a human) · ``held`` (money in, grant deliberately withheld) ·
    ``error``.
    """
    user_id = str(subscription.get("user_id"))
    # The plan the term is ON (what selection matched, and what the
    # mid-charge guard compares against) vs the plan this renewal CHARGES and
    # GRANTS — the same one unless a downgrade is scheduled (226).
    current_plan_id = str(subscription.get("plan_id"))
    target_plan_id = ss.pending_downgrade_of(subscription)
    downgrade = target_plan_id is not None
    plan_id = target_plan_id or current_plan_id
    expires_at = _parse_ts(subscription.get("expires_at"))
    failed_count = int(subscription.get("renewal_failed_count") or 0)
    attempt = failed_count                      # 0 = first try, 1..n = retries

    if expires_at is None:
        # A non-expiring grant has no term to renew and no boundary to key on.
        return "skipped_no_expiry"
    if attempt >= MAX_ATTEMPTS:
        return "skipped_ladder_done"
    # The period key. It is the term boundary being renewed, and it is stable
    # across the whole ladder: expires_at only moves on SUCCESS (the grace is a
    # separate column for exactly this reason — renewal_retry_grace.md §6), so
    # attempts 0/1/2 all carry the same period_start and the unique index can
    # tell a retry from a duplicate.
    period_start = _iso(expires_at)

    if not (get_settings().MOYASAR_SECRET_KEY or "").strip():
        # Checked BEFORE the ledger row is opened, deliberately: an unconfigured
        # provider is OUR fault, and it must not burn a rung of the customer's
        # dunning ladder or leave an unresolvable 'initiated' row behind.
        _log_once(
            logging.ERROR, ("unconfigured", user_id, period_start),
            "renewal skipped: MOYASAR_SECRET_KEY is unset",
        )
        return "skipped_unconfigured"

    # ── the EFFECTIVE price is the price (plan trap 2) ─────────────────────
    plans = await run_db(
        ps._fetch_plans, supabase,
        [current_plan_id, plan_id] if downgrade else [plan_id],
    )
    current_plan = plans.get(current_plan_id)
    if not pm.plan_renews(current_plan):
        # Either the plan is not pro/pro_plus/max, or `plans.billing_cycle` does
        # not say 'recurring_30d'. Trap 6: the column is finally READ, and it is
        # a gate. (The CURRENT plan: a term that does not renew renews into
        # nothing, scheduled change or not.)
        _log_once(
            logging.INFO, ("not_recurring", user_id, period_start),
            "renewal skipped: plan=%s does not renew (billing_cycle)", current_plan_id,
        )
        return "skipped_plan_not_recurring"
    plan = plans.get(plan_id)

    # ── a scheduled downgrade: the TARGET, at the price PROMISED or less ────
    # (owner 2026-11-20, see _price_downgrade). Never swapped back to the
    # current plan, and never skipped because the target lost its list price or
    # its offer since — the promise was made and it is kept.
    stamp: dict[str, Any] = {}
    pricing_source: Optional[str] = None
    if downgrade:
        if not plan or not plan.get("duration_days"):
            # The catalog row itself is gone or term-less: there is nothing to
            # grant. Unreachable while plans(plan_id) is the FK target.
            _log_once(
                logging.ERROR, ("downgrade_no_plan", user_id, period_start),
                "renewal skipped: user=%s is scheduled to move %s → %s and that "
                "plan has no catalog row / duration — resolve by hand",
                user_id, current_plan_id, plan_id,
            )
            return "skipped_downgrade_unavailable"
        decided = await _price_downgrade(
            supabase,
            subscription=subscription,
            current_plan=current_plan,
            plan=plan,
            attempt=attempt,
            period_start=period_start,
        )
        if decided is None:
            # No promise on the row (a pre-promise row) AND no fresh quote.
            # Checked BEFORE the ledger row: it burns no rung; the next tick
            # re-asks.
            _log_once(
                logging.ERROR, ("downgrade_unpriced", user_id, period_start),
                "renewal skipped: the scheduled %s → %s for user=%s has no stored "
                "promise and could not be priced fresh", current_plan_id, plan_id,
                user_id,
            )
            return "skipped_pricing_unavailable"
        price, stamp, pricing_source = decided["price"], decided["stamp"], decided["source"]
    else:
        price = await _current_price(supabase, user_id, plan_id, plan, expires_at)
    if price < ps.MIN_CHARGE_SAR:
        _log_once(
            logging.ERROR, ("bad_price", user_id, period_start),
            "renewal skipped: plan=%s price %s is below the minimum", plan_id, price,
        )
        return "skipped_bad_price"

    # ── an active, CONSENTED card, or nothing happens ──────────────────────
    method = await run_db(pm.get_chargeable_method, supabase, user_id)
    if not method:
        _log_once(
            logging.INFO, ("no_method", user_id, period_start),
            "renewal skipped: user=%s has no chargeable stored card (this term "
            "will lapse into the free fallback, exactly as it does today)", user_id,
        )
        return "skipped_no_method"

    # ── pre-filter: is this period already spoken for? (rule 3) ────────────
    # Across EVERY plan (plan_id=None): a scheduled downgrade set or undone
    # mid-ladder moves the charged plan, and an ambiguous row under the other
    # plan must still block this period.
    existing = await run_db(
        _existing_renewal_rows, supabase, user_id, None, period_start
    )
    blocking = [
        r for r in existing if str(r.get("status")) != SLOT_RELEASING_STATUS
    ]
    if blocking:
        unresolved = [r for r in blocking if str(r.get("status")) == "initiated"]
        if unresolved:
            _log_once(
                logging.ERROR, ("blocked", user_id, period_start),
                "renewal BLOCKED: user=%s plan=%s period=%s has an unresolved "
                "'initiated' renewal row (%s). A charge whose outcome we never "
                "saw must never be retried blind — resolve it (check Moyasar, or "
                "wait for the webhook) before this subscription can renew.",
                user_id, plan_id, period_start,
                [r.get("payment_id") for r in unresolved],
            )
            return "ambiguous"
        _log_once(
            logging.INFO, ("settled", user_id, period_start),
            "renewal skipped: user=%s plan=%s period=%s is already settled (%s)",
            user_id, plan_id, period_start,
            sorted({str(r.get("status")) for r in blocking}),
        )
        return "skipped_already_renewed"
    if any(int(r.get("renewal_attempt") or 0) == attempt for r in existing):
        # This exact rung already ran and failed; the ladder clock will bring
        # the next one around.
        return "skipped_attempt_exists"

    # ── 1. the ledger row, BEFORE the provider call (rule 2) ───────────────
    net, vat = ps.vat_split(price)
    customer = await run_db(ps._fetch_customer_identity, supabase, user_id)
    payload = {
        "user_id": user_id,
        "plan_id": plan_id,
        "amount_sar": ps._money(price),
        "currency": ps.CURRENCY,
        "status": "initiated",
        "provider": ps.PROVIDER,
        "vat_amount_sar": ps._money(vat),
        "net_amount_sar": ps._money(net),
        # A renewal is never prorated: it is one full period at the effective
        # price (list, or the early-adopter rate while that window is open).
        "upgrade_credit_sar": ps._money(Decimal("0.00")),
        "customer_name_snapshot": customer.get("full_name_ar"),
        "customer_email_snapshot": customer.get("email"),
        "initiated_by": ps.INITIATED_BY_RENEWAL,
        "renewal_attempt": attempt,
        "period_start": period_start,
        "payment_method_id": method.get("payment_method_id"),
    }
    # A scheduled downgrade priced from a batch (today's, or the promised one)
    # is stamped EXACTLY as checkout stamps a batch sale: the batch (the
    # fulfilment claims the seat from it — even if it is now full, stamped
    # over_capacity, or switched off), the quoted terms (the claim freezes
    # price + months from them, not from a batch row an operator may edit) and,
    # for Pro Tier 1, the group (its first payment is non-refundable, as for any
    # fresh buyer). An ordinary renewal writes none of these — its INSERT is
    # unchanged.
    payload.update(stamp)
    try:
        row = await run_db(ps._insert_transaction, supabase, payload)
    except Exception as exc:  # noqa: BLE001
        if ps._is_unique_violation(exc):
            # THE EXPECTED "already renewed" PATH (rule 1). Another tick, another
            # worker, or a redeploy mid-run got here first. Nothing to do, and
            # emphatically nothing to charge. Either index lands here: 132's
            # per-plan (user, plan, period) one, and 226's cross-plan (user,
            # period) one — a scheduled downgrade can charge one period under two
            # plans, and that index stops the second charge at the database even
            # if the pre-filter above missed the first row.
            logger.info(
                "renewal already claimed by a concurrent run: user=%s plan=%s "
                "period=%s attempt=%d", user_id, plan_id, period_start, attempt,
            )
            return "skipped_already_renewed"
        logger.exception(
            "renewal ledger insert failed for user=%s plan=%s: %s", user_id, plan_id, exc
        )
        return "error"

    payment_id = str(row["payment_id"])

    # ── 2. charge the token ────────────────────────────────────────────────
    plan_name = plan.get("name_ar") or plan_id
    try:
        fetched = await ps.charge_saved_card(
            token=str(method["provider_token"]),
            amount_halalas=ps.to_halalas(price),
            description=f"ريحان — {plan_name} (تجديد تلقائي)",
            payment_id=payment_id,
            metadata={"initiated_by": ps.INITIATED_BY_RENEWAL, "renewal_attempt": attempt},
        )
    except ps.MoyasarError as exc:
        if _charge_definitely_did_not_happen(exc):
            # Moyasar answered, and its answer was "no": a dead token, a
            # rejected request, a 402. Nothing was captured, so this is an
            # ordinary decline — mark the row failed and let the ladder advance.
            logger.warning(
                "renewal charge refused by the provider: payment=%s user=%s (%s)",
                payment_id, user_id, exc,
            )
            synthetic = {
                "status": "failed",
                "source": {"message": f"provider refused: {str(exc)[:160]}"},
            }
            await ps._mark_failed(supabase, row, synthetic)
            return await _handle_decline(
                supabase,
                subscription=subscription,
                row=row,
                fetched=synthetic,
                plan=plan,
                attempt=attempt,
                period_start=period_start,
            )

        # AMBIGUOUS (rule 3): a transport failure or a 5xx means we never saw an
        # answer, and the charge may well have gone through. The row stays
        # 'initiated' and blocks this period until a human or the webhook
        # resolves it. Deliberately NOT marked failed — 'failed' would let the
        # ladder advance and charge the card a second time.
        logger.error(
            "RENEWAL OUTCOME UNKNOWN: payment=%s user=%s plan=%s — the charge "
            "request did not complete cleanly (%s). The row is left 'initiated' "
            "and this period is now BLOCKED. Check Moyasar for a payment "
            "carrying metadata.payment_id=%s and settle the row by hand if the "
            "money moved.",
            payment_id, user_id, plan_id, exc, payment_id,
        )
        await run_db(
            write_audit_log,
            supabase,
            user_id=user_id,
            action="update",
            resource_type="payment_transaction",
            resource_id=payment_id,
            metadata={
                "event": "renewal_charge_ambiguous",
                "plan_id": plan_id,
                "period_start": period_start,
                "renewal_attempt": attempt,
                "provider_error": str(exc)[:300],
            },
        )
        return "ambiguous"

    # ── 3. record the outcome, reusing the ONE fulfilment path ─────────────
    provider_status = str(fetched.get("status") or "").lower()

    if provider_status == "paid":
        # ── did the world move while the charge was in flight? ─────────────
        # The one case where granting would DESTROY something: the user
        # upgraded pro→max in the seconds between selection and this line.
        # grant_plan writes plan_id unconditionally, so granting the pro
        # renewal now would silently downgrade a customer who just paid 219.90
        # — and neither they nor support would ever work out why. A term that
        # merely MOVED (they stacked an extension) is not a problem: grant_plan
        # stacks onto whatever expires_at says, which is correct.
        #
        # Compared against the plan the term was ON at selection — never the
        # downgrade target (226): on a scheduled downgrade the row is still on
        # the higher plan here, which is exactly the "nothing moved" case.
        current = await run_db(_select_one_subscription, supabase, user_id)
        # THE WEBHOOK MAY HAVE BEEN FASTER. Moyasar's payment_paid event runs
        # the same fulfilment, and for a scheduled downgrade it grants the
        # TARGET — so by now the row can legitimately be on the target with
        # THIS payment fulfilled. That is our own success, not a plan that
        # moved: re-read the ledger row (never act on the stale insert — its
        # missing paid_at would overwrite the webhook's) and carry on; the
        # fulfilment below is idempotent and the billing-date fix runs once.
        latest = await run_db(ps._get_transaction, supabase, payment_id)
        if latest:
            row = {**row, **latest}
        on_plan = str((current or {}).get("plan_id") or "")
        settled_by_webhook = bool(downgrade and on_plan == plan_id and row.get("fulfilled_at"))
        if on_plan != current_plan_id and not settled_by_webhook:
            return await _hold_renewal_for_review(
                supabase, row=row, fetched=fetched, user_id=user_id,
                plan_id=plan_id, period_start=period_start,
                reason="plan_changed_mid_charge",
                current_plan_id=(current or {}).get("plan_id"),
            )
        if settled_by_webhook:
            logger.info(
                "renewal: payment=%s was already fulfilled by the webhook (user=%s "
                "now on %s) — completing the job's bookkeeping only",
                payment_id, user_id, plan_id,
            )
        elif downgrade and ss.pending_downgrade_of(current) != plan_id:
            # The user undid (or changed) the scheduled downgrade between
            # selection and the charge. The money that moved is the TARGET's
            # price, so the target is what it buys — granting the higher plan
            # for the lower price is a discount nobody chose. Same posture as an
            # opt-out mid-flight below: granted, refundable for 24h, loud.
            logger.error(
                "renewal charged a scheduled downgrade that was changed mid-flight: "
                "user=%s payment=%s charged=%s now_pending=%s. The charged plan is "
                "granted (that is what was paid for) and the charge is refundable "
                "for 24h.",
                user_id, payment_id, plan_id, ss.pending_downgrade_of(current),
            )
        if (current or {}).get("renewal_cancelled_at"):
            # They opted out between selection and the charge. The money is in
            # and the term IS granted below — that is what they were charged
            # for, and they can self-serve refund inside 24h. Loud, not fatal.
            logger.error(
                "renewal charged a user who opted out mid-flight: user=%s "
                "payment=%s. The term is granted (they paid for it) and the "
                "charge is refundable for 24h — check whether the cancel landed "
                "before our selection.",
                user_id, payment_id,
            )

        try:
            ps._assert_matches(row, fetched)
        except ps.MoyasarError as exc:
            # The provider charged something other than what our row says. Money
            # has moved and we will not grant against a figure we did not price.
            logger.error(
                "RENEWAL AMOUNT MISMATCH: payment=%s user=%s (%s) — money may "
                "have moved; NOT granting. Refund it and reconcile by hand.",
                payment_id, user_id, exc,
            )
            await run_db(
                ps._update_transaction, supabase, payment_id,
                {"provider_ref": fetched.get("id"), "raw_payload": fetched},
            )
            return "ambiguous"

        # On a scheduled downgrade this grants the TARGET, releases the left
        # plan's seat 'downgraded', claims the batch seat if the row carries
        # one, and clears renewal_plan_id — all inside the shared fulfilment.
        result = await ps._mark_paid_and_grant(supabase, row, fetched)
        new_expiry = result.get("expires_at")
        if result.get("granted"):
            new_expiry = await _keep_the_billing_date(
                supabase,
                subscription=subscription,
                plan=plan,
                granted_expires_at=new_expiry,
                plan_id=plan_id,
            )
        # failed_count 0 and the grace cleared: _mark_paid_and_grant's
        # reset_renewal_ladder already did both for every paid grant; this
        # write is the renewal job's own record of the attempt.
        await run_db(
            _write_renewal_bookkeeping,
            supabase,
            user_id,
            attempt_at=_iso(_now()),
            failed_count=0,
            grace_until=None,
        )
        await run_db(
            write_audit_log,
            supabase,
            user_id=user_id,
            action="update",
            resource_type="payment_transaction",
            resource_id=payment_id,
            metadata={
                "event": "renewal_charged",
                "plan_id": plan_id,
                "amount_sar": ps._money(price),
                "period_start": period_start,
                "renewal_attempt": attempt,
                "expires_at": new_expiry,
                "provider_ref": fetched.get("id"),
                # 226: present only on a scheduled downgrade, so an ordinary
                # renewal's audit row is byte-identical to the pre-226 one.
                **({"downgraded_from": current_plan_id,
                    "promo_batch_id": stamp.get("promo_batch_id"),
                    "pricing": pricing_source}
                   if downgrade else {}),
            },
        )
        logger.info(
            "renewal charged: user=%s plan=%s amount=%s period=%s attempt=%d "
            "new_expiry=%s downgraded_from=%s",
            user_id, plan_id, price, period_start, attempt, new_expiry,
            current_plan_id if downgrade else None,
        )
        return "renewed" if result.get("granted") else "ambiguous"

    # Anything that is not 'paid' is a decline for renewal purposes — including
    # 'initiated', which for a merchant-initiated charge means a 3DS challenge
    # with nobody present to answer it (plan §4 item 4).
    await ps._mark_failed(supabase, row, fetched)
    return await _handle_decline(
        supabase,
        subscription=subscription,
        row=row,
        fetched=fetched,
        plan=plan,
        attempt=attempt,
        period_start=period_start,
    )


async def _hold_renewal_for_review(
    supabase: SupabaseClient,
    *,
    row: dict,
    fetched: dict,
    user_id: str,
    plan_id: str,
    period_start: str,
    reason: str,
    current_plan_id: Optional[str] = None,
) -> str:
    """Money is in, but granting would be wrong. Record the charge, skip the grant.

    The same posture ``payment_service._hold_for_review`` takes for a stale
    upgrade credit, and for the same reasons:

    * the money MUST be recorded — a charge with our row still saying
      ``initiated`` is the worst of both worlds, and the customer gets no
      receipt;
    * ``paid`` + ``fulfilled_at IS NULL`` is not a new state. It is the one the
      schema already models for "money received, plan not applied", it is
      refundable by the customer from the receipts list for 24h, and
      ``revoke_plan_grant`` answers ``not_fulfilled`` cleanly on it;
    * the period stays closed (``paid`` is not ``failed``), so no tick retries.
    """
    payment_id = str(row.get("payment_id"))
    patch = {
        "status": "paid",
        "provider_ref": fetched.get("id"),
        "raw_payload": fetched,
        "paid_at": _iso(_now()),
    }
    updated = await run_db(ps._update_transaction, supabase, payment_id, patch) or {
        **row, **patch
    }

    logger.error(
        "RENEWAL HELD — money is in and the grant was NOT applied: payment=%s "
        "user=%s charged_plan=%s current_plan=%s period=%s reason=%s. Granting "
        "would have overwritten the plan the customer now holds. The row is "
        "paid + unfulfilled and the customer can self-serve refund it inside "
        "24h; reconcile by hand.",
        payment_id, user_id, plan_id, current_plan_id, period_start, reason,
    )

    await run_db(
        write_audit_log,
        supabase,
        user_id=user_id,
        action="update",
        resource_type="payment_transaction",
        resource_id=payment_id,
        metadata={
            "event": "renewal_held",
            "reason": reason,
            "charged_plan_id": plan_id,
            "current_plan_id": current_plan_id,
            "period_start": period_start,
            "provider_ref": fetched.get("id"),
        },
    )

    # The receipt still goes out: there is a charge on the customer's card, and
    # a charge with no receipt is worse than one whose plan is pending.
    await send_payment_receipt(supabase, updated)
    return "held"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Dunning (renewal_retry_grace.md §0, §4)
# ═══════════════════════════════════════════════════════════════════════════


async def _handle_decline(
    supabase: SupabaseClient,
    *,
    subscription: dict,
    row: dict,
    fetched: dict,
    plan: dict,
    attempt: int,
    period_start: str,
) -> str:
    """A declined card: advance the ladder, tell the customer, or let it lapse.

    Access during the ladder is the GRACE (196), and this is the only function
    that opens one:

    * a SOFT decline on the first attempt opens it —
      ``renewal_grace_until = GREATEST(expires_at, now) + GRACE_PERIOD``;
    * a soft decline on the middle rung leaves it as it is;
    * a HARD decline (any attempt — ``HARD_DECLINE_CODES``) or a soft decline on
      the last rung ends the ladder: ``renewal_failed_count = MAX_ATTEMPTS`` and
      ``renewal_grace_until = now()`` — lapse now, final email. This never cuts
      into PAID time: get_user_quota_state reads the grace only when it is
      LATER than ``expires_at``, so a term declined on its last tick before
      expiry still runs to its own end and then takes the EXISTING expired→free
      fallback, exactly as without this feature.
    """
    user_id = str(subscription.get("user_id"))
    plan_id = str(subscription.get("plan_id"))
    code = _decline_code(fetched)
    hard = code in HARD_DECLINE_CODES
    failed_count = MAX_ATTEMPTS if hard else attempt + 1
    final = failed_count >= MAX_ATTEMPTS

    now = _now()
    expires_at = _parse_ts(subscription.get("expires_at"))
    if final:
        grace_until: Any = _iso(now)
    elif attempt == 0:
        grace_until = _iso(max(expires_at or now, now) + GRACE_PERIOD)
    else:
        grace_until = _KEEP

    source = fetched.get("source") if isinstance(fetched.get("source"), dict) else {}
    provider_message = source.get("message") or fetched.get("status")

    await run_db(
        _write_renewal_bookkeeping,
        supabase,
        user_id,
        attempt_at=_iso(now),
        failed_count=failed_count,
        grace_until=grace_until,
    )

    await run_db(
        write_audit_log,
        supabase,
        user_id=user_id,
        action="update",
        resource_type="payment_transaction",
        resource_id=row.get("payment_id"),
        metadata={
            "event": "renewal_declined",
            "plan_id": plan_id,
            "period_start": period_start,
            "renewal_attempt": attempt,
            "failed_count": failed_count,
            "final": final,
            "decline_code": code,
            "hard_decline": hard,
            "grace_until": None if grace_until is _KEEP else grace_until,
            "provider_message": str(provider_message)[:200] if provider_message else None,
            # 226: a declined scheduled downgrade charged the TARGET plan.
            **({"charged_plan_id": row.get("plan_id")}
               if row.get("plan_id") and row.get("plan_id") != plan_id else {}),
        },
    )

    logger.warning(
        "renewal declined: user=%s plan=%s attempt=%d/%d code=%s hard=%s final=%s "
        "message=%s",
        user_id, plan_id, attempt + 1, MAX_ATTEMPTS, code, hard, final,
        provider_message,
    )

    # Email on the FIRST failure and on the LAST one (a hard decline is both).
    # The middle rung is silent on purpose — three emails in a day about the
    # same card reads as dunning spam and trains people to ignore the one that
    # matters. The first email shows the grace's end as the date access runs to;
    # the final one shows no date, because access ends now.
    if attempt == 0 or final:
        # `plan` is the plan this renewal CHARGED — on a scheduled downgrade the
        # target (226) — so the email names the plan whose price it quotes, from
        # `plans.name_ar` («الاحترافية بلس» included).
        await send_renewal_failed_notice(
            supabase,
            payment_row=row,
            plan_name_ar=plan.get("name_ar") or plan_id,
            expires_at=None if final else grace_until,
            final=final,
        )

    return "lapsed" if final else "declined"


# ═══════════════════════════════════════════════════════════════════════════
# 5. The sweep
# ═══════════════════════════════════════════════════════════════════════════


async def run_due_renewals(supabase: SupabaseClient) -> dict[str, int]:
    """Charge every pro/pro_plus/max term due by the next tick, plus every due
    retry — a scheduled downgrade charging its target instead.

    Never raises: a scheduler tick must not be able to crash the app, and one
    bad subscription must never stop the sweep (the account-purge sweep's
    per-user isolation, same reasoning — except here a stopped sweep means
    people's subscriptions silently lapse).

    Refuses to do anything at all when the feature flag is off. The scheduler
    does not even register the job in that case; this is the second wall, for
    the manual ``python -m`` invocation and for tests.

    Returns a stats dict keyed by outcome tag.
    """
    stats: dict[str, int] = {"scanned": 0}

    if not pm.auto_renewal_enabled():
        logger.info("renewal sweep skipped: SUBSCRIPTION_AUTO_RENEWAL_ENABLED is off")
        return {**stats, "disabled": 1}

    now = _now()
    try:
        due = await run_db(_select_due_first_attempt, supabase, now)
        retries = await run_db(_select_due_retry, supabase, now)
    except Exception as exc:  # noqa: BLE001
        # Includes 42703 if this backend is running ahead of migration 132/196.
        logger.exception("renewal sweep: selection query failed: %s", exc)
        return {**stats, "error": 1}

    # Retries first: they are already late, and BATCH_CAP should be spent on
    # somebody whose subscription is mid-lapse before somebody whose term still
    # has minutes on it.
    candidates = (retries + due)[:BATCH_CAP]
    stats["scanned"] = len(candidates)
    if not candidates:
        # DEBUG: the job ticks 96 times a day and almost every tick is empty.
        logger.debug("renewal sweep: nothing due")
        return stats

    for subscription in candidates:
        try:
            outcome = await _renew_one(supabase, subscription)
        except Exception as exc:  # noqa: BLE001
            outcome = "error"
            logger.exception(
                "renewal failed for user=%s: %s", subscription.get("user_id"), exc
            )
        stats[outcome] = stats.get(outcome, 0) + 1

    logger.info("renewal sweep complete: %s", stats)
    return stats


__all__ = [
    "run_due_renewals", "LADDER_HOURS", "MAX_ATTEMPTS", "DUE_HORIZON",
    "GRACE_PERIOD", "HARD_DECLINE_CODES",
]
