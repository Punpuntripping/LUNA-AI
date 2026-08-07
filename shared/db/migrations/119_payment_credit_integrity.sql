-- ════════════════════════════════════════════════════════════════════════════
-- 119 — upgrade-credit integrity: a quoted discount must be consumable ONCE
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: agents_reports/security_review_2026-08-07.md H-4 (and the operator
--       surface M-1 needs).
-- Depends on: 092 (payment_transactions, grant_plan), 113 (upgrade_credit_sar,
--             revoke_plan_grant, prior-plan snapshot), 114 (receipt_no trigger),
--             117 (user_id nullable / ON DELETE SET NULL).
-- Idempotent: DROP CONSTRAINT IF EXISTS + ADD, CREATE INDEX IF NOT EXISTS,
--             NULL-safe UPDATEs that are no-ops on a re-run. Re-runnable.
--
-- WHAT ─────────────────────────────────────────────────────────────────────────
--   1. `expired` joins the status domain — the state a checkout quote lands in
--      when a newer one supersedes it.
--   2. A one-time sweep of the open-checkout backlog (14 rows on prod, the
--      oldest three days old, one of them carrying an 89.90 credit and still
--      payable).
--   3. A PARTIAL UNIQUE INDEX: at most one open CREDITED checkout per user.
--   4. Two operator surfaces: quotes left open, and money in with no plan out.
--
-- WHY ──────────────────────────────────────────────────────────────────────────
-- `upgrade_credit_sar` was computed at checkout from the caller's CURRENT
-- subscription and then never looked at again. Nothing consumed it, reserved
-- it, or expired it: no lock, no cap on outstanding `initiated` rows, no TTL,
-- no sweeper, no constraint. So the credit was not a deduction against a term —
-- it was a coupon, reprintable on demand:
--
--   * open N checkouts BEFORE paying any, and each one independently reads the
--     same untouched subscription and applies the FULL credit. Pay all N and
--     every unit after the first costs ~47% less;
--   * worse, `remaining_days / duration_days` was unclamped and same-plan
--     purchases STACK (grant_plan adds duration_days onto a live expiry), so
--     pro × 3 = 90 remaining days = a 269.70 credit against an 89.90 plan,
--     clamped only by `price - 1.00` — a 30-day `max` for 1.00 SAR, less than
--     the ~1.73 SAR the card network charges us to collect it.
--
-- The backend (backend/app/services/payment_service.py) now clamps the ratio to
-- one period, supersedes the caller's open quotes on every new checkout, and
-- RE-DERIVES the charge from live subscription state before granting. This file
-- is the half of that which has to be true even if the backend is wrong: a
-- database in which two payable discounted quotes cannot coexist for one user.
--
-- WHY NOT A TRIGGER, AND WHY grant_plan IS UNTOUCHED ───────────────────────────
-- Same posture 113 and 117 took on the money path: additive beats destructive.
-- A constraint states an invariant and cannot silently revert prod drift; a
-- CREATE OR REPLACE over the live grant path can. The re-derivation therefore
-- lives in the backend, where it can HOLD a payment for review (paid, not
-- fulfilled) instead of raising inside the one function that moves money.

-- ── 1. `expired` — the superseded / stale quote ──────────────────────────────
-- DROP-then-ADD rather than a guarded ADD: the constraint already exists with
-- the NARROWER domain, so a guarded ADD would silently leave it in place and
-- every supersede would 23514. Every existing row is in the old set, so the
-- wider CHECK validates unconditionally.

--
-- RECEIPT-NUMBER TRAP, checked before writing this: 114's trg_payment_receipt_no
-- is BEFORE UPDATE OF status, and every supersede below writes status. Its live
-- body (read 2026-08-07) assigns only `IF NEW.status = 'paid'`, so an
-- initiated → expired transition consumes no receipt_no and the sequential
-- series 117 must keep hole-free is untouched. Widening that trigger means
-- revisiting both this migration and _expire_open_checkouts.

ALTER TABLE public.payment_transactions
    DROP CONSTRAINT IF EXISTS payment_transactions_status_check;
ALTER TABLE public.payment_transactions
    ADD CONSTRAINT payment_transactions_status_check
    CHECK (status = ANY (ARRAY['initiated', 'paid', 'expired', 'failed', 'refunded']));

COMMENT ON COLUMN public.payment_transactions.status IS
    'Lifecycle of the CHECKOUT, not of the money. initiated = priced, form not '
    'yet paid. expired (119) = superseded by a newer quote from the same user, '
    'or swept as stale — it is bookkeeping about the QUOTE only: if money still '
    'lands on an expired row the backend marks it paid and fulfils it normally, '
    'because a status must never be able to keep a customer''s payment. paid / '
    'failed / refunded are terminal.';

-- ── 2. Sweep the open-checkout backlog ───────────────────────────────────────
-- Two statements, in this order, so the unique index below can never fail to
-- build on existing data.
--
-- (a) TTL. A quote older than a day cannot be honoured on its own word — the
--     subscription it was priced against has had a day to move. The backend
--     refuses to grant a credit from a quote this old for the same reason.

UPDATE public.payment_transactions
   SET status     = 'expired',
       updated_at = now()
 WHERE status = 'initiated'
   AND created_at < now() - interval '24 hours';

-- (b) Keep only the NEWEST open credited quote per user. Nothing on prod
--     violates this today (one credited open row exists), but the index must
--     not be able to fail against whatever has accumulated by the time this
--     runs — a migration that aborts halfway on the money table is worse than
--     the hole it closes.

UPDATE public.payment_transactions t
   SET status     = 'expired',
       updated_at = now()
 WHERE t.status = 'initiated'
   AND t.upgrade_credit_sar > 0
   AND t.user_id IS NOT NULL
   AND EXISTS (
        SELECT 1
          FROM public.payment_transactions n
         WHERE n.user_id = t.user_id
           AND n.status = 'initiated'
           AND n.upgrade_credit_sar > 0
           AND n.created_at > t.created_at
   );

-- ── 3. One open CREDITED checkout per user ───────────────────────────────────
-- The stockpile, made unrepresentable. Uncredited quotes are deliberately NOT
-- constrained: they carry no discount, so duplicates cost nothing but tidiness,
-- and constraining them would turn a double-mounted checkout page into a hard
-- error on a path that currently just leaves a harmless orphan row.
--
-- user_id IS NOT NULL is explicit rather than incidental: 117 nulls it on a
-- purged account, and although several NULLs never conflict in a unique index,
-- relying on that silently would make the invariant read as stronger than it is.

CREATE UNIQUE INDEX IF NOT EXISTS uniq_payment_open_credited_per_user
    ON public.payment_transactions (user_id)
    WHERE status = 'initiated'
      AND upgrade_credit_sar > 0
      AND user_id IS NOT NULL;

COMMENT ON INDEX public.uniq_payment_open_credited_per_user IS
    'At most one payable discounted quote per user (119). The backend expires '
    'the caller''s open quotes before inserting a new one; this index is what '
    'makes that true under concurrency, where two checkouts both find nothing '
    'to supersede. The loser retries once and becomes the newest quote.';

-- Supports that supersede UPDATE (user_id, status) — idx_payment_user is
-- (user_id, created_at DESC) and cannot serve the status predicate.
CREATE INDEX IF NOT EXISTS idx_payment_open_checkouts
    ON public.payment_transactions (user_id, created_at DESC)
    WHERE status = 'initiated';

-- ── 4. Operator surfaces ─────────────────────────────────────────────────────
-- Mirrors 113's idx_payment_refunded_unrevoked (money returned, term standing).
-- This one is its opposite and the alert condition for a HELD grant: money
-- taken, plan never applied. Transient for the milliseconds between the
-- mark-paid write and grant_plan, so read it with an age filter:
--
--   SELECT * FROM payment_transactions
--    WHERE status='paid' AND fulfilled_at IS NULL
--      AND paid_at < now() - interval '10 minutes';
--
-- A row surfacing there is either a crashed grant (re-runnable — /verify or the
-- webhook finishes it) or a grant deliberately held because its upgrade credit
-- was no longer owed. The audit_logs row with event='grant_held_credit_stale'
-- tells the two apart and carries what was paid vs what was owed.

CREATE INDEX IF NOT EXISTS idx_payment_paid_unfulfilled
    ON public.payment_transactions (paid_at DESC)
    WHERE status = 'paid' AND fulfilled_at IS NULL;

COMMENT ON COLUMN public.payment_transactions.upgrade_credit_sar IS
    'Prorated credit for the remaining value of the plan being replaced, '
    'deducted at checkout (0 for everything else). Credit is granted ONLY when '
    'the current subscription source = payment, and the proration ratio is '
    'CLAMPED to one plan period (119) — same-plan purchases stack, so an '
    'unclamped ratio let a 90-day pro term earn 269.70 against an 89.90 plan. '
    'The invariant is now enforced TWICE: charged amount == catalog price - '
    'stored credit at checkout, and re-derived from live subscription state '
    'before the grant. A row whose credit no longer holds is left paid + '
    'unfulfilled for an operator rather than granted or silently pocketed.';
