-- ════════════════════════════════════════════════════════════════════════════
-- 092 — financial-integration-ready subscriptions (provider-agnostic)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Prepares the subscription layer for a real payment provider (Moyasar / Tap /
-- Stripe — not chosen yet) without wiring any provider today. Three pieces:
--
--   1. plans.price_sar + billing_cycle — SERVER-authoritative prices. The
--      frontend lib/pricing.ts stays the display copy; checkout must read the
--      amount from here, never from the client.
--   2. payment_transactions — append-only money ledger. One row per checkout
--      attempt: initiated → paid | failed | refunded. UNIQUE(provider,
--      provider_ref) makes webhook processing idempotent at the DB level.
--      RLS: self SELECT only; all writes come from the backend (service role).
--   3. grant_plan(user, plan, source, payment_id) — the ONE entry point that
--      assigns/renews a plan. The future payment webhook calls this after
--      verifying the provider event; operators can call it for manual grants.
--      Guarantees:
--        * payment must exist, belong to (user, plan), and be status='paid'
--        * fulfilled_at stamps the grant — a webhook RETRY becomes a no-op
--          (no double-extension of expires_at)
--        * early renewal of the SAME plan STACKS: new expiry = current expiry
--          + duration (paid-for days are never lost); plan change or expired
--          sub gets a fresh window from now()
--
-- Deliberately NOT here: provider webhooks/endpoints (come with the provider
-- choice), auto-renewal scheduling (provider-side recurring billing → each
-- cycle fires the same webhook → same grant_plan call).
--
-- See .claude/plans/financial_integration.md for the full integration contract.

-- ── 1. Server-side price catalog ─────────────────────────────────────────────

ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS price_sar numeric(10,2)
        CHECK (price_sar IS NULL OR price_sar >= 0),
    ADD COLUMN IF NOT EXISTS billing_cycle text
        CHECK (billing_cycle IN ('one_time', 'recurring_monthly'));

COMMENT ON COLUMN public.plans.price_sar IS
    'Checkout amount in SAR — the server-authoritative price (092). NULL = not '
    'purchasable (free/dev/marketing plans). lib/pricing.ts is display copy; '
    'keep in sync by hand.';
COMMENT ON COLUMN public.plans.billing_cycle IS
    'one_time (basic: pay, lasts duration_days, no renewal) | recurring_monthly '
    '(pro/max: provider-side auto-renew, each cycle re-grants via grant_plan).';

UPDATE public.plans SET price_sar = 49,  billing_cycle = 'one_time'          WHERE plan_id = 'basic';
UPDATE public.plans SET price_sar = 89,  billing_cycle = 'recurring_monthly' WHERE plan_id = 'pro';
UPDATE public.plans SET price_sar = 189, billing_cycle = 'recurring_monthly' WHERE plan_id = 'max';

-- ── 2. Money ledger ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.payment_transactions (
    payment_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    plan_id      text NOT NULL REFERENCES public.plans(plan_id),
    amount_sar   numeric(10,2) NOT NULL CHECK (amount_sar >= 0),
    currency     text NOT NULL DEFAULT 'SAR',
    status       text NOT NULL DEFAULT 'initiated'
                 CHECK (status IN ('initiated', 'paid', 'failed', 'refunded')),
    provider     text,          -- 'moyasar' | 'tap' | 'stripe' | ... (set at checkout)
    provider_ref text,          -- provider's charge/checkout id (webhook correlation)
    raw_payload  jsonb,         -- provider webhook snapshot, for audit/debugging
    fulfilled_at timestamptz,   -- stamped by grant_plan — the idempotency anchor
    paid_at      timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.payment_transactions IS
    'Append-only money ledger (092). One row per checkout attempt; amount_sar '
    'is copied from plans.price_sar at initiation (server price authority). '
    'status transitions are driven by verified provider webhooks. fulfilled_at '
    'is set by grant_plan — a paid row with fulfilled_at NULL is money received '
    'but plan not yet applied.';

-- Webhook idempotency: the same provider event can only ever match one row.
CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_provider_ref
    ON public.payment_transactions (provider, provider_ref)
    WHERE provider_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payment_user
    ON public.payment_transactions (user_id, created_at DESC);

ALTER TABLE public.payment_transactions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS payment_transactions_select_self ON public.payment_transactions;
CREATE POLICY payment_transactions_select_self ON public.payment_transactions
    FOR SELECT USING (
        user_id IN (SELECT u.user_id FROM public.users u WHERE u.auth_id = auth.uid())
    );
-- No INSERT/UPDATE/DELETE policies: only the backend (service role) writes.

-- ── 3. grant_plan — the single grant/renew entry point ───────────────────────

CREATE OR REPLACE FUNCTION public.grant_plan(
    p_user_id    uuid,
    p_plan_id    text,
    p_source     text DEFAULT 'manual',   -- 'payment' | 'manual' | 'code'
    p_payment_id uuid DEFAULT NULL
)
RETURNS TABLE(plan_id text, name_ar text, expires_at timestamp with time zone)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public'
AS $$
DECLARE
    v_dur      INTEGER;
    v_pay      RECORD;
    v_cur      RECORD;
    v_newexp   TIMESTAMPTZ;
    v_started  TIMESTAMPTZ := now();
BEGIN
    SELECT duration_days INTO v_dur FROM public.plans p WHERE p.plan_id = p_plan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'plan_unknown';
    END IF;

    -- Payment-backed grant: verify the money row, and make retries no-ops.
    IF p_payment_id IS NOT NULL THEN
        SELECT * INTO v_pay
          FROM public.payment_transactions t
         WHERE t.payment_id = p_payment_id
           FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'payment_not_found';
        END IF;
        IF v_pay.user_id <> p_user_id OR v_pay.plan_id <> p_plan_id THEN
            RAISE EXCEPTION 'payment_mismatch';
        END IF;
        IF v_pay.status <> 'paid' THEN
            RAISE EXCEPTION 'payment_not_paid';
        END IF;
        IF v_pay.fulfilled_at IS NOT NULL THEN
            -- Webhook retry — already applied; return current state unchanged.
            RETURN QUERY
                SELECT s.plan_id,
                       (SELECT p.name_ar FROM public.plans p WHERE p.plan_id = s.plan_id),
                       s.expires_at
                  FROM public.user_subscriptions s
                 WHERE s.user_id = p_user_id;
            RETURN;
        END IF;
    END IF;

    SELECT s.plan_id, s.expires_at, s.started_at INTO v_cur
      FROM public.user_subscriptions s
     WHERE s.user_id = p_user_id
       FOR UPDATE;

    -- Renewal semantics: early renewal of the SAME still-active plan stacks on
    -- top of the remaining time; anything else opens a fresh window from now().
    IF v_dur IS NULL THEN
        v_newexp := NULL;
    ELSIF v_cur.plan_id = p_plan_id
          AND v_cur.expires_at IS NOT NULL AND v_cur.expires_at > now() THEN
        v_newexp  := v_cur.expires_at + make_interval(days => v_dur);
        v_started := v_cur.started_at;   -- same continuous term
    ELSE
        v_newexp := now() + make_interval(days => v_dur);
    END IF;

    -- NOTE: handle_subscription_assignment (BEFORE UPDATE OF plan_id) recomputes
    -- expires_at = now() + duration only when plan_id actually CHANGES — which is
    -- exactly the fresh-window value computed above. On a same-plan stack the
    -- trigger leaves our expires_at untouched. The two agree by construction.
    INSERT INTO public.user_subscriptions
        (user_id, plan_id, source, started_at, expires_at, redeemed_code)
    VALUES
        (p_user_id, p_plan_id, p_source, v_started, v_newexp, NULL)
    ON CONFLICT (user_id) DO UPDATE SET
        plan_id       = EXCLUDED.plan_id,
        source        = EXCLUDED.source,
        started_at    = EXCLUDED.started_at,
        expires_at    = EXCLUDED.expires_at,
        redeemed_code = CASE WHEN EXCLUDED.source = 'code'
                             THEN public.user_subscriptions.redeemed_code
                             ELSE NULL END,
        updated_at    = now();

    IF p_payment_id IS NOT NULL THEN
        UPDATE public.payment_transactions t
           SET fulfilled_at = now(), updated_at = now()
         WHERE t.payment_id = p_payment_id;
    END IF;

    RETURN QUERY
        SELECT p_plan_id,
               (SELECT p.name_ar FROM public.plans p WHERE p.plan_id = p_plan_id),
               v_newexp;
END;
$$;

COMMENT ON FUNCTION public.grant_plan(uuid, text, text, uuid) IS
    'Single entry point for assigning/renewing a plan (092). Payment webhooks '
    'call it with p_payment_id after verifying the provider event (must be '
    'status=paid; fulfilled_at makes retries no-ops). Operators call it for '
    'manual grants. Early same-plan renewal stacks remaining days.';

-- CRITICAL: grant_plan has no internal authorization guard — it must never be
-- callable through PostgREST by end users (any user could grant themselves max).
-- Backend (service role) and SQL operators only.
REVOKE EXECUTE ON FUNCTION public.grant_plan(uuid, text, text, uuid)
    FROM PUBLIC, anon, authenticated;
