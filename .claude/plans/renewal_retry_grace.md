# Renewal retry ladder v2 — charge at expiry, 24h grace, +12h / +24h, then lapse

Status: PLANNED 2026-10-30 · Owner decisions locked 2026-10-30 · Supersedes §8 (dunning) of
`subscription_auto_renewal.md` · Migration `196_renewal_grace.sql` (NOT written yet; 195 = workspace client revoke, APPLIED 2026-10-30)

## 0. Locked decisions (owner, 2026-10-30)

1. **Charge at expiry, keep access during retries** (option B). Attempt 0 runs at the term's
   `expires_at` (not up to 24h before, as today). On a decline the user keeps Pro/Max for up
   to 24h while we retry.
2. **Ladder = 0 / +12h / +24h from attempt 0, then give up.** Lapse to free after the last fail.
3. **Hard declines give up at once** — no retries, lapse immediately, final email. Soft declines
   (e.g. `51` insufficient funds) get the full ladder.
4. Rejected: running the ladder before expiry (charges everyone ~1 day early) and pushing
   `expires_at` forward (breaks the period key → double-charge guard; see §6).

## 1. Why (evidence, prod 2026-10-28 → 10-30)

- `_retry_is_due` (`renewal_service.py:249-261`) compares `now - last >= 1 day` against a daily
  03:30 cron; `renewal_attempt_at` is stamped seconds after 03:30, so every retry slips a day.
  mido.heim: failed 10-28, skipped 10-29, paid 10-30. abdallhalsrraj + abdbnw: failed 10-29,
  skipped 10-30.
- Access lapses mid-ladder today: abdallhalsrraj declined 03:30, lapsed 21:33 the same day.
- Decline codes live in `raw_payload->source->response_code`, never branched on
  (`payment_service.py:3003-3021` `_mark_failed` stores only the text). Seen: 51, 57, 12.

## 2. Pre-existing double-charge bug — ✅ SHIPPED 2026-10-30 `38b4380`

Shipped as `subscription_service.reset_renewal_ladder` (failed_count only; `renewal_attempt_at`
kept as history) + the `expires_at <= now + DUE_HORIZON` bound. When 196 lands, extend the reset
to clear `renewal_grace_until` too.

A web checkout never resets `renewal_failed_count` (only a renewal success does,
`renewal_service.py:725-731`; 132 promised "reset on any new paid grant"). `_select_due_retry`
has no upper bound on `expires_at` (:233-244). So a user mid-ladder who re-buys manually is
picked up as a "due retry" and charged for the period starting at their NEW expiry (~30 days
early). abdallhalsrraj and abdbnw are in that state now (`renewal_failed_count = 1`).

Fix (backend only, no migration):
- In `_mark_paid_and_grant` (beside the `clear_renewal_cancellation` call,
  `payment_service.py:~2894-2903`), as its own statement: reset `renewal_failed_count = 0`,
  `renewal_attempt_at = NULL` (and `renewal_grace_until = NULL` once 196 exists) for
  `initiated_by = 'user'` purchases.
- Add `expires_at <= now + DUE_HORIZON` to `_select_due_retry`.
- Test: user mid-ladder buys via checkout → next tick charges nothing.

## 3. Schema — migration `196_renewal_grace.sql`

- `user_subscriptions.renewal_grace_until timestamptz NULL` — "access continues until here
  while a renewal is being retried". NULL = no grace. Never set by anything but the renewal job.
- `get_user_quota_state`: treat the term as live while `expires_at > now()` **OR**
  `renewal_grace_until > now()`. `CREATE OR REPLACE` with the IDENTICAL `RETURNS TABLE` — the
  live `user_subscriptions_live` view reads `is_expired` / `effective_plan_id` from it, so no view
  rebuild. ⚠ Read the LIVE `pg_get_functiondef` first (migration-drift rule), never 092/129 text.
  ⚠ Verify afterwards: `SELECT reloptions FROM pg_class WHERE relname='user_subscriptions_live'`
  → `{security_invoker=true}` (129/129a trap).
- Stacking after a late success: `grant_plan` only stacks onto the old expiry when
  `expires_at > now()`, so a success during grace would start 30 days from now (billing date
  slips; grace hours become free). Two options — builder picks after reading the live defs:
  - **B1** widen `grant_plan`'s stacking predicate to include `renewal_grace_until > now()`.
    ⚠ 194 (store purchases, unapplied) asserts `grant_plan` is untouched — its self-test must
    be updated in the same release, and 113's "the live money path is not edited for a side
    concern" rule is being broken deliberately; say so in the migration header.
  - **B2** leave `grant_plan` alone; after a successful renewal grant, a separate statement sets
    `expires_at = period_start + 30d` (expires_at ALONE — the trigger is `BEFORE UPDATE OF
    plan_id`). Verify the trigger does not re-stamp when grant_plan writes the same plan_id.
  - Recommendation: B2 if the trigger check passes (no money-path RPC edit), else B1.
- Library period key (`library_period_key`, e.g. `max:20261017:0`): check it does not roll over
  into a fresh allowance at `expires_at` during grace. If it does, pin it to the old period while
  `renewal_grace_until > now()`.
- SQL self-tests in the migration: grace row reads as active/max; expired grace reads free;
  view keeps 33 columns + `security_invoker`.

## 4. Backend — `renewal_service.py` + `main.py`

- `LADDER_DAYS (0,1,3)` → `LADDER_HOURS = (0, 12, 24)`; `MAX_ATTEMPTS = 3`.
- `_retry_is_due`: hours, with slack (`>= wait − 10 min`) so a tick a few seconds early still fires.
- First attempt: select terms with `expires_at <= now + 15 min` (not `now + 24h`), `failed_count
  = 0`, not cancelled. `DUE_HORIZON` → 15 min; drop the "seen while still live" assumption
  comment (:130-134) and rewrite it for grace.
- On a soft decline at attempt 0: set `renewal_grace_until = expires_at + 24h + 30 min` (room
  for the last attempt to finish). Own statement; never touches `plan_id`.
- On a hard decline (any attempt) or a soft decline at the last attempt: set `renewal_failed_count
  = MAX_ATTEMPTS`, `renewal_grace_until = now()` (lapse now), send the final email.
- On success: `renewal_grace_until = NULL`, `failed_count = 0` (existing reset, :725-731).
- Hard-decline set = a constant `HARD_DECLINE_CODES` read from `source.response_code`:
  start with `12, 14, 41, 43, 54, 57, 62` (invalid txn, bad card no., lost, stolen, expired, not
  permitted, restricted). Unknown/absent code → treat as soft. `05` (do not honour) → soft.
- `RETRY_GRACE` (7d) → 2h past `renewal_grace_until` (no retry may run after grace ends).
- Scheduler (`main.py:287-299`, inside the `SUBSCRIPTION_AUTO_RENEWAL_ENABLED` guard):
  `CronTrigger(hour=3, minute=30)` → `CronTrigger(minute="*/15")`, keep `max_instances=1` +
  `coalesce=True`. Dedupe the per-tick skip / `ambiguous` logs (:527-541) or they log 96×/day.
- Cancel during grace: `renewal_cancelled_at` set mid-ladder → stop retrying, clear grace
  (lapse now). `_term_is_running` (`subscription_service.py:336`) must count grace as running.

## 5. Copy & UI

- `receipt_service.py:361-369` dunning email: «خلال الأيام القادمة» → retry within 24 hours;
  :356-357 / :375 must not say the plan has ended while in grace. Final email unchanged in spirit.
- `AccountSettingsDialog.tsx:450` + mobile `subscription-model.ts:64`: grace counts as active
  (no "ended" state while the user still has Pro/Max). Needs `renewal_grace_until` on
  GET /subscription (additive field — old app builds ignore it).
- Comment `renewal_service.py:904-906` assumes the old timing — rewrite.
- `terms-ar.md:62` already says renewal happens «عند انتهاء كل مدة» — B fits it. No consent
  string change (`payment_method_service.py:142` names no timing → no DISCLOSURE_VERSION bump).

## 6. Rejected: pushing `expires_at` forward

The renewal period key is `_iso(expires_at)` (:461). Moving it changes the period, so a retry no
longer sees attempt 0's row and the per-period unique index stops preventing a second charge.
It would also stack free days and distort refunds / upgrade credit.

## 7. Tests (`backend/tests/test_subscription_renewal.py`)

Update: :1791 (pins `(0,1,3)`), :1795, :1803-1827, :1839 (`RETRY_GRACE`), :1848-1872, :740
(pins the 03:30 cron). Add: exact-boundary tick (attempt stamped 03:30:11, tick at 03:30:00 +12h
fires); hard decline → no retry + immediate lapse + final email; grace keeps quota state on
max; success during grace stacks onto old expiry; checkout mid-ladder resets the ladder (§2);
cancel mid-grace lapses now.

## 8. Release order

1. **§2 hotfix** — backend only. ⚠ `renewal_service.py` and `payment_service.py` carry the
   uncommitted mobile Phase 4b (store purchases) hunks that depend on unapplied 194: stage ONLY
   the §2 hunks, or ship Phase 4b first.
2. Apply **196** (verify view + RPC live) → deploy backend (§4) → web + mobile copy (§5).
   196 before backend, or `_write_renewal_bookkeeping` hits 42703 and the ladder jams.
3. Watch the first real decline in Logfire + `payment_transactions`.

## 9. Open (non-blocking, defaults in brackets)

- ✅ abdallhalsrraj (57) + abdbnw (12): `renewal_failed_count` set 1 → 3 on prod 2026-10-30
  (owner OK) — the ladder treats them as given up. A re-buy now resets it via §2.
- A web/mobile "card declined — we'll try again" banner during grace? [not in v1].
- A sentence about retries in /terms and `product_docs.pricing`? [owner wording].
