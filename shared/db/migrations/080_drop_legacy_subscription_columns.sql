-- Migration 080: drop the legacy subscription columns from public.users.
--
-- ⚠️  DO NOT APPLY until the backend that reads `user_subscriptions` (migration
--     079) is CONFIRMED LIVE in production. This migration removes the
--     compatibility mirror + the legacy columns the OLD backend still reads
--     (users.plan_id / subscription_expires_at). Applying it before the new
--     backend deploys would break quota + /me on the running app.
--
-- WHY: migration 079 made `user_subscriptions` the single source of truth for
-- subscription identity and kept the old `users` columns in sync (the redeem
-- RPC mirror) purely for deploy-order safety. Once the new backend is live,
-- nothing reads those columns (verified: shared/quota/_user_limits, /auth/me,
-- and the login path all read user_subscriptions or hardcode the dead field),
-- so they become dead weight. Same cleanup pattern as the agent_runs drop (062).
--
-- ORDER inside this migration matters:
--   1. Rewrite redeem_plan_code() to DROP the `UPDATE users …` mirror first —
--      otherwise step 3 would leave the RPC writing to a column that no longer
--      exists.
--   2. Drop the old users-side assignment trigger + function (superseded by
--      handle_subscription_assignment on user_subscriptions).
--   3. Drop the columns.
--
-- The subscription_tier_enum / subscription_status_enum TYPES are left in place
-- (orphaned but harmless) to avoid assuming nothing else references them.
--
-- Dependencies: 079_user_subscriptions.sql. Idempotent.

------------------------------------------------------------------------
-- 1. redeem_plan_code() — same as 079 minus the legacy users mirror.
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
    SELECT s.plan_id, s.expires_at
      INTO v_cur, v_curexp
      FROM public.user_subscriptions s
     WHERE s.user_id = p_user_id
       FOR UPDATE;

    IF v_cur IN ('basic', 'pro', 'max', 'dev')
       AND (v_curexp IS NULL OR v_curexp > now()) THEN
        RAISE EXCEPTION 'plan_already_active';
    END IF;

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

    SELECT p.duration_days INTO v_dur FROM public.plans p WHERE p.plan_id = v_plan;
    v_newexp := CASE WHEN v_dur IS NULL THEN NULL
                     ELSE now() + make_interval(days => v_dur) END;

    INSERT INTO public.user_subscriptions
        (user_id, plan_id, status, source, started_at, expires_at, redeemed_code)
    VALUES
        (p_user_id, v_plan, 'active', 'code', now(), v_newexp, v_norm)
    ON CONFLICT (user_id) DO UPDATE SET
        plan_id       = EXCLUDED.plan_id,
        status        = 'active',
        source        = 'code',
        started_at    = now(),
        expires_at    = EXCLUDED.expires_at,
        redeemed_code = EXCLUDED.redeemed_code,
        updated_at    = now();

    RETURN QUERY
        SELECT v_plan,
               (SELECT p.name_ar FROM public.plans p WHERE p.plan_id = v_plan),
               v_newexp;
END;
$$;

COMMENT ON FUNCTION public.redeem_plan_code(TEXT, UUID) IS
    'Atomically redeem a single-use plan code → activate user_subscriptions '
    '(the only SSoT; legacy users mirror removed in 080). Blocks downgrade of '
    'active paid/dev plans; stamps expiry explicitly.';

------------------------------------------------------------------------
-- 2. Drop the old users-side assignment trigger + function.
------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_users_plan_assignment ON public.users;
DROP FUNCTION IF EXISTS public.handle_plan_assignment();

------------------------------------------------------------------------
-- 3. Drop the legacy columns (identity now lives entirely in
--    user_subscriptions; usage in the llm_calls ledger).
------------------------------------------------------------------------
ALTER TABLE public.users
    DROP COLUMN IF EXISTS plan_id,
    DROP COLUMN IF EXISTS subscription_expires_at,
    DROP COLUMN IF EXISTS subscription_tier,
    DROP COLUMN IF EXISTS subscription_status,
    DROP COLUMN IF EXISTS ocr_pages_monthly_limit,
    DROP COLUMN IF EXISTS web_calls_monthly_limit,
    DROP COLUMN IF EXISTS points_monthly_override,
    DROP COLUMN IF EXISTS points_weekly_override,
    DROP COLUMN IF EXISTS points_session_override;
