-- Migration 069: plan activation codes — single-use redeemable codes that
-- grant a subscription plan (built for the marketing_lawyer / marketing_individual
-- launch offers, but works for any plan in the catalog).
--
-- An operator generates a batch of codes (scripts/gen_plan_codes.py), hands one
-- to a user, and the user redeems it in-app (Settings → تفعيل برمز). Redemption
-- sets users.plan_id — migration 068's machinery does the rest (the marketing
-- plans already carry their 7-day duration + point limits).
--
-- Codes are 5-char Crockford base32 (alphabet 23456789ABCDEFGHJKMNPQRSTVWXYZ —
-- no lookalike 0/O/1/I/L/U), stored NORMALIZED (uppercase, separators stripped).
-- Single-use: redeemed_by flips from NULL exactly once via an atomic UPDATE.
--
-- Safety: the redeem function is the ONLY supported write path. It (1) blocks
-- redemption when the user already holds an active paid/dev plan (so a 7-day
-- marketing code can't silently downgrade a max subscriber — the code is NOT
-- consumed), (2) claims the code atomically so two concurrent redemptions can
-- never both win, and (3) stamps subscription_expires_at EXPLICITLY (the 068
-- trigger only fires on a plan_id *change*, so re-redeeming the same plan would
-- otherwise skip renewal — the RENEWAL TRAP).
--
-- The per-user 5-attempts/24h brute-force wall lives in the backend (Redis
-- counter keyed redeem:fails:{user_id}); a wrong code raises 'code_invalid_or_used'
-- here and the endpoint increments that counter.
--
-- Dependencies: 003_users.sql, 068_subscription_plans.sql. Idempotent.

------------------------------------------------------------------------
-- 1. Codes table.
------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.plan_codes (
    code         TEXT PRIMARY KEY,                        -- normalized: UPPER, no separators
    plan_id      TEXT NOT NULL REFERENCES public.plans(plan_id),
    redeemed_by  UUID REFERENCES public.users(user_id),   -- NULL = available (single-use)
    redeemed_at  TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ,                             -- code shelf life; NULL = never expires
    batch_label  TEXT,                                    -- e.g. 'lawyers_launch_jun26'
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.plan_codes IS
    'Single-use activation codes that grant a plan on redemption. Only the '
    'redeem_plan_code() RPC + service role write here (no client RLS policies).';

CREATE INDEX IF NOT EXISTS idx_plan_codes_redeemed_by ON public.plan_codes (redeemed_by);
CREATE INDEX IF NOT EXISTS idx_plan_codes_batch ON public.plan_codes (batch_label);

ALTER TABLE public.plan_codes ENABLE ROW LEVEL SECURITY;
-- No client policies on purpose: redemption goes through the SECURITY DEFINER
-- RPC below (called with the service role), never directly from the browser.

------------------------------------------------------------------------
-- 2. Atomic redeem function.
--    Raises:
--      'plan_already_active'   — user holds a non-expired basic/pro/max/dev
--                                plan; code is NOT consumed.
--      'code_invalid_or_used'  — unknown code, already redeemed, or expired.
--    On success returns the granted plan_id, its Arabic name, and the new
--    subscription_expires_at.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.redeem_plan_code(p_code TEXT, p_user_id UUID)
RETURNS TABLE(plan_id TEXT, name_ar TEXT, expires_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_norm   TEXT := upper(regexp_replace(coalesce(p_code, ''), '[^A-Za-z0-9]', '', 'g'));
    v_cur    TEXT;
    v_curexp TIMESTAMPTZ;
    v_plan   TEXT;
    v_dur    INTEGER;
    v_newexp TIMESTAMPTZ;
BEGIN
    -- 1. Lock the user row; block if an ACTIVE paid/dev plan would be downgraded.
    SELECT u.plan_id, u.subscription_expires_at
      INTO v_cur, v_curexp
      FROM public.users u
     WHERE u.user_id = p_user_id
       FOR UPDATE;

    IF v_cur IN ('basic', 'pro', 'max', 'dev')
       AND (v_curexp IS NULL OR v_curexp > now()) THEN
        RAISE EXCEPTION 'plan_already_active';   -- code NOT burned
    END IF;

    -- 2. Atomic single-use claim (also enforces the code's own expiry). The
    --    UPDATE ... WHERE redeemed_by IS NULL ... RETURNING is the check-and-set:
    --    only one concurrent caller can flip redeemed_by, so double-redeem is
    --    impossible.
    UPDATE public.plan_codes c
       SET redeemed_by = p_user_id,
           redeemed_at = now()
     WHERE c.code = v_norm
       AND c.redeemed_by IS NULL
       AND (c.expires_at IS NULL OR c.expires_at > now())
    RETURNING c.plan_id INTO v_plan;

    IF v_plan IS NULL THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;

    -- 3. Grant. Set the expiry EXPLICITLY — the 068 assignment trigger only
    --    stamps it on a plan_id *change*, so renewing the same plan via a fresh
    --    code would otherwise leave the old (possibly past) expiry in place.
    SELECT p.duration_days INTO v_dur FROM public.plans p WHERE p.plan_id = v_plan;
    v_newexp := CASE WHEN v_dur IS NULL THEN NULL
                     ELSE now() + make_interval(days => v_dur) END;

    UPDATE public.users u
       SET plan_id = v_plan,
           subscription_expires_at = v_newexp
     WHERE u.user_id = p_user_id;

    RETURN QUERY
        SELECT v_plan,
               (SELECT p.name_ar FROM public.plans p WHERE p.plan_id = v_plan),
               v_newexp;
END;
$$;

COMMENT ON FUNCTION public.redeem_plan_code(TEXT, UUID) IS
    'Atomically redeem a single-use plan code for a user. Blocks downgrade of '
    'active paid/dev plans; stamps subscription_expires_at explicitly. Called '
    'by POST /api/v1/plans/redeem with the service role.';
