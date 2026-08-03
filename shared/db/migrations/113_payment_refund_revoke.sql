-- ════════════════════════════════════════════════════════════════════════════
-- 113 — payment refund / revoke + VAT + proration columns (Moyasar Wave 1)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Spec: .claude/plans/moyasar_payments.md §4 Phase B (+ §0 decisions 2026-08-03).
-- Companions: .claude/plans/financial_integration.md (092 money layer),
--             .claude/plans/subscription_system_state.md (grant/quota semantics).
-- Depends on: 079 (user_subscriptions + handle_subscription_assignment),
--             091 (status dropped, trigger rewritten), 092 (payment_transactions,
--             plans.price_sar/billing_cycle, grant_plan), 093 (EXECUTE grants).
-- Idempotent: ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE / guarded DO blocks.
--
-- WHAT ─────────────────────────────────────────────────────────────────────────
--   1. payment_transactions gains the columns Wave 1 needs to be honest about
--      money: revoked_at (the mirror of fulfilled_at), the VAT split stamped at
--      purchase, what a refund actually paid back, and the upgrade-proration
--      snapshot that makes an upgrade refund RESTORE instead of DESTROY.
--   2. revoke_plan_grant(payment_id) — the symmetric counterpart of grant_plan.
--   3. stamp_payment_prior_snapshot(payment_id) — records the subscription being
--      replaced, immediately before a payment-backed grant runs (see WHY below).
--   4. Catalog: all three paid plans become one_time (Wave 1 has no auto-renew —
--      a billing_cycle that lies is the exact bug 091 was written to kill), and
--      prices are repriced to 49.90 / 89.90 / 189.90 (VAT-inclusive, 15%).
--
-- WHY grant_plan IS NOT TOUCHED ────────────────────────────────────────────────
-- moyasar_payments.md §4 Phase B.5b says the prior-subscription snapshot is
-- "stamped when the grant executes". The cleanest place for that is inside
-- grant_plan itself — it already holds both row locks and reads the pre-grant
-- state. It is NOT done there, deliberately:
--
--   * grant_plan is the live money path, verified E2E on prod 2026-07-15. The
--     project rule (memory: project_migration_drift) is that migration files are
--     NOT the prod schema — and the live pg_get_functiondef could not be read
--     while writing this file (no Supabase MCP access in that session). A blind
--     CREATE OR REPLACE from 092's local text would silently revert any prod
--     drift in the one function that moves money. Additive beats destructive.
--
-- So the snapshot is stamped by stamp_payment_prior_snapshot(), which the backend
-- calls from _mark_paid_and_grant IMMEDIATELY BEFORE grant_plan(...), inside the
-- same request:
--
--     stamp_payment_prior_snapshot(payment_id)      -- reads the CURRENT sub
--     grant_plan(user_id, plan_id, 'payment', payment_id)
--
-- It is guarded on `fulfilled_at IS NULL`, exactly like grant_plan's retry
-- no-op — so a webhook retry can never overwrite the snapshot with POST-grant
-- state (which would make a refund "restore" the plan being refunded). The only
-- gap versus doing it inside grant_plan is a millisecond-wide race (the user
-- redeeming a code between the two calls); the failure mode is a restore to a
-- slightly stale prior plan, operator-recoverable, and is judged cheaper than
-- blind-replacing the grant path. Folding this into grant_plan is a clean
-- follow-up ONCE the live definition has been diffed against 092.
--
-- TRAP (subscription_system_state.md §6) ──────────────────────────────────────
-- handle_subscription_assignment is BEFORE UPDATE OF plan_id and recomputes
-- expires_at = now() + duration_days whenever plan_id changes. Therefore every
-- expires_at write below is its OWN statement, and the restore path is TWO
-- statements: plan_id first (trigger recomputes), then expires_at alone.

-- ── 1. payment_transactions — refund, VAT and proration columns ──────────────

ALTER TABLE public.payment_transactions
    ADD COLUMN IF NOT EXISTS revoked_at           timestamptz,
    ADD COLUMN IF NOT EXISTS vat_amount_sar       numeric(10,2)
        CHECK (vat_amount_sar IS NULL OR vat_amount_sar >= 0),
    ADD COLUMN IF NOT EXISTS net_amount_sar       numeric(10,2)
        CHECK (net_amount_sar IS NULL OR net_amount_sar >= 0),
    ADD COLUMN IF NOT EXISTS refund_fee_sar       numeric(10,2)
        CHECK (refund_fee_sar IS NULL OR refund_fee_sar >= 0),
    ADD COLUMN IF NOT EXISTS refunded_amount_sar  numeric(10,2)
        CHECK (refunded_amount_sar IS NULL OR refunded_amount_sar >= 0),
    ADD COLUMN IF NOT EXISTS upgrade_credit_sar   numeric(10,2) NOT NULL DEFAULT 0
        CHECK (upgrade_credit_sar >= 0),
    ADD COLUMN IF NOT EXISTS prior_plan_id        text,
    ADD COLUMN IF NOT EXISTS prior_expires_at     timestamptz;

-- FK added separately so it lands even if the column pre-existed without it
-- (ADD COLUMN IF NOT EXISTS skips the whole clause, constraint included).
DO $$
BEGIN
    ALTER TABLE public.payment_transactions
        ADD CONSTRAINT payment_transactions_prior_plan_id_fkey
        FOREIGN KEY (prior_plan_id) REFERENCES public.plans(plan_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
    WHEN duplicate_table  THEN NULL;
END $$;

COMMENT ON COLUMN public.payment_transactions.revoked_at IS
    'Mirror of fulfilled_at (113): stamped by revoke_plan_grant when a refund has '
    'been applied to the subscription. Non-NULL makes refund-webhook retries '
    'no-ops. A refunded row with revoked_at NULL is money returned but the term '
    'still standing — that is the alert condition.';
COMMENT ON COLUMN public.payment_transactions.vat_amount_sar IS
    'VAT portion of amount_sar, computed ONCE at initiation and stored (15% '
    'inclusive). Never recomputed at display time — a future rate change must not '
    'rewrite history.';
COMMENT ON COLUMN public.payment_transactions.net_amount_sar IS
    'amount_sar minus vat_amount_sar, stamped at initiation alongside it.';
COMMENT ON COLUMN public.payment_transactions.refund_fee_sar IS
    'Processing fee RETAINED on refund (server-side constant REFUND_FEE_SAR, '
    'never a client input). Stamped when the refund executes, so a later fee '
    'change cannot rewrite past refunds.';
COMMENT ON COLUMN public.payment_transactions.refunded_amount_sar IS
    'What was actually paid back = amount_sar - refund_fee_sar. Every refund is '
    'PARTIAL because of the fee — the provider call must always carry an explicit '
    'amount, or it refunds in full and gives the fee away.';
COMMENT ON COLUMN public.payment_transactions.upgrade_credit_sar IS
    'Prorated credit for the remaining value of the plan being replaced, deducted '
    'at checkout (0 for everything else). Credit is granted ONLY when the current '
    'subscription source = payment. The invariant becomes: charged amount == '
    'catalog price - stored credit, 100% server-computed.';
COMMENT ON COLUMN public.payment_transactions.prior_plan_id IS
    'Snapshot of the plan this grant REPLACED, stamped by '
    'stamp_payment_prior_snapshot just before grant_plan. Non-NULL = this payment '
    'changed the plan, so a refund RESTORES (revoke_plan_grant) rather than '
    'subtracting days.';
COMMENT ON COLUMN public.payment_transactions.prior_expires_at IS
    'Snapshot of expires_at at the same moment as prior_plan_id — what a refund '
    'restores. Refund means undo, not destroy.';

CREATE INDEX IF NOT EXISTS idx_payment_prior_plan
    ON public.payment_transactions (prior_plan_id)
    WHERE prior_plan_id IS NOT NULL;

-- Operator alert surface: money returned, term still standing.
CREATE INDEX IF NOT EXISTS idx_payment_refunded_unrevoked
    ON public.payment_transactions (updated_at DESC)
    WHERE status = 'refunded' AND revoked_at IS NULL;

-- ── 2. stamp_payment_prior_snapshot — record what the grant is about to replace ─
-- Called by the backend immediately BEFORE grant_plan(..., 'payment', id).
-- Safe to call before or after the row is marked paid; MUST be before the grant.
-- No-ops (never overwrites) once fulfilled_at is set — that is what makes it
-- retry-proof, mirroring grant_plan's own fulfilled_at guard.

CREATE OR REPLACE FUNCTION public.stamp_payment_prior_snapshot(p_payment_id uuid)
RETURNS TABLE(prior_plan_id text, prior_expires_at timestamp with time zone)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_pay RECORD;
    v_cur RECORD;
BEGIN
    SELECT * INTO v_pay
      FROM public.payment_transactions t
     WHERE t.payment_id = p_payment_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE NOTICE 'stamp_payment_prior_snapshot: payment % not found', p_payment_id;
        RETURN QUERY SELECT NULL::text, NULL::timestamptz;
        RETURN;
    END IF;

    -- Already granted (webhook retry) or already stamped: never re-read the
    -- subscription — it now holds POST-grant state, and stamping that would make
    -- a refund "restore" the very plan being refunded.
    IF v_pay.fulfilled_at IS NOT NULL OR v_pay.prior_plan_id IS NOT NULL THEN
        RETURN QUERY SELECT v_pay.prior_plan_id, v_pay.prior_expires_at;
        RETURN;
    END IF;

    SELECT s.plan_id, s.expires_at INTO v_cur
      FROM public.user_subscriptions s
     WHERE s.user_id = v_pay.user_id
       FOR UPDATE;

    -- Nothing to restore when there is no subscription, when the account is
    -- locked (plan_id NULL — a refund must land the user on the free fallback,
    -- not back in the locked state), or when this is a same-plan re-purchase
    -- (grant_plan STACKS; the refund path subtracts exactly one duration).
    IF NOT FOUND OR v_cur.plan_id IS NULL OR v_cur.plan_id = v_pay.plan_id THEN
        RETURN QUERY SELECT NULL::text, NULL::timestamptz;
        RETURN;
    END IF;

    UPDATE public.payment_transactions t
       SET prior_plan_id    = v_cur.plan_id,
           prior_expires_at = v_cur.expires_at,
           updated_at       = now()
     WHERE t.payment_id = p_payment_id
       AND t.fulfilled_at IS NULL;

    RETURN QUERY SELECT v_cur.plan_id, v_cur.expires_at;
END;
$$;

COMMENT ON FUNCTION public.stamp_payment_prior_snapshot(uuid) IS
    'Stamps prior_plan_id/prior_expires_at on a payment row from the CURRENT '
    'subscription, so a refund of a plan-changing payment can restore instead of '
    'destroy (113). Backend calls it immediately before grant_plan(...,payment). '
    'Guarded on fulfilled_at IS NULL → webhook retries can never overwrite the '
    'snapshot with post-grant state. Service-role only.';

REVOKE EXECUTE ON FUNCTION public.stamp_payment_prior_snapshot(uuid)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.stamp_payment_prior_snapshot(uuid) TO service_role;

-- ── 3. revoke_plan_grant — the symmetric counterpart of grant_plan ───────────
--
-- Contract (moyasar_payments.md §4 Phase B.2):
--   * requires status='refunded'; revoked_at NOT NULL → no-op (retry safety);
--   * fulfilled_at IS NULL (money taken, plan never applied) → stamp revoked_at
--     and return. Nothing to revoke, NOT an error;
--   * PLAN-MATCH GUARD: touch the subscription only while its current plan_id
--     still equals the refunded payment's plan. If the user has since switched
--     plans, that grant's window was already destroyed by grant_plan's
--     fresh-window logic — acting now would eat days of a plan they still pay
--     for (buy basic 10:00 → upgrade pro 12:00 → refund basic 13:00 must not
--     cost pro days). Stamp revoked_at only. NOTE: the guard is applied to BOTH
--     branches below, a deliberate strengthening of the plan text (which states
--     it for the subtract branch only) — a stale restore would equally destroy a
--     plan the user currently holds;
--   * prior_plan_id set (this payment CHANGED the plan) → RESTORE the snapshot;
--   * otherwise → subtract exactly plans.duration_days, so refunding one
--     purchase out of a stack leaves the others intact. Landing in the past =
--     expired = free fallback, which is correct;
--   * stamps revoked_at on EVERY path (webhook retries are no-ops);
--   * an unknown payment_id returns a row with action='payment_not_found' and
--     raises NOTHING — the refund webhook must never 500 (only 5 retries exist).
--
-- The `action` column names which branch ran; the backend logs it and operators
-- can read it back out of the audit trail.

CREATE OR REPLACE FUNCTION public.revoke_plan_grant(p_payment_id uuid)
RETURNS TABLE(plan_id text, name_ar text, expires_at timestamp with time zone, action text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_pay    RECORD;
    v_sub    RECORD;
    v_hassub BOOLEAN;
    v_dur    INTEGER;
    v_newexp TIMESTAMPTZ;
    v_action TEXT;
BEGIN
    SELECT * INTO v_pay
      FROM public.payment_transactions t
     WHERE t.payment_id = p_payment_id
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE NOTICE 'revoke_plan_grant: payment % not found', p_payment_id;
        RETURN QUERY SELECT NULL::text, NULL::text, NULL::timestamptz,
                            'payment_not_found'::text;
        RETURN;
    END IF;

    -- Symmetric with grant_plan's payment_not_paid guard: the caller marks the
    -- row refunded first. Reaching here otherwise is a caller bug, not a retry.
    IF v_pay.status <> 'refunded' THEN
        RAISE EXCEPTION 'payment_not_refunded';
    END IF;

    SELECT s.plan_id, s.expires_at INTO v_sub
      FROM public.user_subscriptions s
     WHERE s.user_id = v_pay.user_id
       FOR UPDATE;
    v_hassub := FOUND;

    IF v_pay.revoked_at IS NOT NULL THEN
        -- Retry of the refund webhook — already applied, change nothing.
        v_action := 'already_revoked';
    ELSIF v_pay.fulfilled_at IS NULL THEN
        -- Money taken but no grant ever ran. Nothing to undo.
        v_action := 'not_fulfilled';
    ELSIF NOT v_hassub OR v_sub.plan_id IS NULL THEN
        -- No subscription row, or the account was locked since. Nothing to undo.
        v_action := 'no_subscription';
    ELSIF v_sub.plan_id IS DISTINCT FROM v_pay.plan_id THEN
        -- Plan-match guard: the user moved on; this grant is no longer standing.
        v_action := 'plan_switched';
    ELSIF v_pay.prior_plan_id IS NOT NULL THEN
        -- RESTORE (prorated upgrade refunded): put the replaced plan back with
        -- its original expiry. TWO statements — the assignment trigger recomputes
        -- expires_at on any statement that touches plan_id, so setting both at
        -- once would be silently overwritten.
        UPDATE public.user_subscriptions s
           SET plan_id    = v_pay.prior_plan_id,
               updated_at = now()
         WHERE s.user_id = v_pay.user_id;

        UPDATE public.user_subscriptions s
           SET expires_at = v_pay.prior_expires_at,
               updated_at = now()
         WHERE s.user_id = v_pay.user_id;

        v_action := 'restored';
    ELSE
        SELECT p.duration_days INTO v_dur
          FROM public.plans p WHERE p.plan_id = v_pay.plan_id;

        IF v_dur IS NULL OR v_sub.expires_at IS NULL THEN
            -- Non-expiring plan: there are no granted days to take back.
            v_action := 'no_expiry';
        ELSE
            -- Subtract exactly what was granted. Its OWN statement (plan_id is
            -- untouched, so the assignment trigger does not fire at all — hence
            -- updated_at is set by hand here).
            v_newexp := v_sub.expires_at - make_interval(days => v_dur);
            UPDATE public.user_subscriptions s
               SET expires_at = v_newexp,
                   updated_at = now()
             WHERE s.user_id = v_pay.user_id;
            v_action := 'subtracted';
        END IF;
    END IF;

    -- Every path stamps revoked_at: this is the idempotency anchor. Skipped only
    -- on 'already_revoked', where the original timestamp is the honest one.
    IF v_pay.revoked_at IS NULL THEN
        UPDATE public.payment_transactions t
           SET revoked_at = now(),
               updated_at = now()
         WHERE t.payment_id = p_payment_id;
    END IF;

    -- Re-read so the caller always gets the POST-revoke truth, and always
    -- exactly one row (record fields stay NULL when the user has no
    -- subscription — the action is what matters on those paths).
    SELECT s.plan_id, s.expires_at INTO v_sub
      FROM public.user_subscriptions s
     WHERE s.user_id = v_pay.user_id;

    RETURN QUERY
        SELECT v_sub.plan_id,
               (SELECT p.name_ar FROM public.plans p WHERE p.plan_id = v_sub.plan_id),
               v_sub.expires_at,
               v_action;
END;
$$;

COMMENT ON FUNCTION public.revoke_plan_grant(uuid) IS
    'Undo a granted term after a refund (113) — the counterpart of grant_plan. '
    'Restores the prior plan when the payment carried a proration snapshot, else '
    'subtracts exactly plans.duration_days, and only while the subscription still '
    'holds the refunded payment''s plan (plan-match guard). Stamps revoked_at on '
    'every path so refund-webhook retries are no-ops. Unknown payment_id returns '
    'action=payment_not_found without raising — a webhook must never 500.';

-- Same posture as grant_plan: no internal authorization guard, so it must never
-- be reachable through PostgREST by end users.
REVOKE EXECUTE ON FUNCTION public.revoke_plan_grant(uuid)
    FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.revoke_plan_grant(uuid) TO service_role;

-- ── 4. Catalog — one_time everywhere, repriced to .90 (VAT-inclusive) ────────
-- Wave 1 sells one-time terms only: Moyasar has NO subscription engine, so
-- auto-renewal is our own scheduler (Wave 2). billing_cycle flips back to
-- recurring_monthly in the SAME wave as the copy that promises it — never before.
--
-- Prices are VAT-INCLUSIVE at 15%:
--   basic 49.90 = 43.39 net + 6.51 VAT   (4990 halalas)
--   pro   89.90 = 78.17 net + 11.73 VAT  (8990 halalas)
--   max  189.90 = 165.13 net + 24.77 VAT (18990 halalas)
-- frontend/lib/pricing.ts is DISPLAY COPY ONLY and must be updated in the same
-- commit — the DB is authoritative and the two drift silently otherwise.

UPDATE public.plans SET price_sar = 49.90,  billing_cycle = 'one_time' WHERE plan_id = 'basic';
UPDATE public.plans SET price_sar = 89.90,  billing_cycle = 'one_time' WHERE plan_id = 'pro';
UPDATE public.plans SET price_sar = 189.90, billing_cycle = 'one_time' WHERE plan_id = 'max';

COMMENT ON COLUMN public.plans.billing_cycle IS
    'one_time = pay once, access lasts duration_days, no renewal (ALL paid plans '
    'in Moyasar Wave 1 — 113). recurring_monthly is reserved for Wave 2, when our '
    'own token-charge scheduler makes auto-renew real; setting it earlier would '
    'make the field lie, which is the bug 091 was written to kill.';
COMMENT ON COLUMN public.plans.price_sar IS
    'Checkout amount in SAR — server-authoritative, VAT-INCLUSIVE at 15% (092, '
    'repriced 113). NULL = not purchasable (free/dev/marketing). Charge in '
    'halalas = price_sar * 100. lib/pricing.ts is display copy; keep in sync.';
