-- Migration 081: multi-use (global) plan activation codes.
--
-- Migration 069 made plan_codes STRICTLY single-use: one `redeemed_by` UUID that
-- flips from NULL exactly once. This migration generalizes that to N-use codes so
-- a single shared "global" code can activate the first N distinct users — e.g.
-- hand one code to a cohort and let the first 100 redeemers get the plan.
--
-- MODEL (kept on the plan_codes table itself — no separate ledger):
--   max_uses          how many distinct users may redeem this code (1 = the old
--                     single-use behavior; this is the DEFAULT, so existing codes
--                     and the unchanged generator path are untouched).
--   uses_count        how many have redeemed so far. A code is AVAILABLE while
--                     uses_count < max_uses AND not expired.
--   redeemed_by_users the set of users who redeemed (dedup + audit). A user may
--                     redeem a given code AT MOST ONCE — re-entering it never
--                     consumes a second slot ('code_already_redeemed').
--   redeemed_by /     kept for back-compat + admin readability: the MOST-RECENT
--   redeemed_at       redeemer + time. For a single-use code that is THE redeemer.
--
-- ATOMICITY: redeem_plan_code() takes `FOR UPDATE` on the plan_codes row, so the
-- check-capacity-then-consume sequence is serialized per code — N concurrent
-- redemptions can never overshoot max_uses, and the same user can't double-spend.
--
-- The endpoint's per-user 5-fails/24h brute-force wall is unchanged. A duplicate
-- redemption by an already-redeemed user raises 'code_already_redeemed' (mapped
-- to a friendly 409, NOT counted as a brute-force guess).
--
-- ⚠️  APPLY ORDER: this REDEFINES redeem_plan_code() against user_subscriptions
--     (migrations 079) WITHOUT the legacy users mirror (dropped in 080). Apply in
--     numeric order — 079 → 080 → 081 — and only after the SSoT backend is live
--     (same deploy gate as 080). Applying 080 AFTER 081 would clobber this
--     multi-use function with 080's single-use version.
--
-- Dependencies: 069_plan_codes.sql, 079_user_subscriptions.sql,
-- 080_drop_legacy_subscription_columns.sql. Idempotent.

------------------------------------------------------------------------
-- 1. Capacity columns on plan_codes (additive; defaults preserve single-use).
------------------------------------------------------------------------
ALTER TABLE public.plan_codes
    ADD COLUMN IF NOT EXISTS max_uses          integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS uses_count        integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS redeemed_by_users uuid[]  NOT NULL DEFAULT '{}';

COMMENT ON COLUMN public.plan_codes.max_uses IS
    'How many distinct users may redeem this code. 1 = single-use (default).';
COMMENT ON COLUMN public.plan_codes.uses_count IS
    'Redemptions so far. Code is available while uses_count < max_uses.';
COMMENT ON COLUMN public.plan_codes.redeemed_by_users IS
    'Set of users who redeemed (dedup + audit). One redemption per user per code.';
COMMENT ON COLUMN public.plan_codes.redeemed_by IS
    'Most-recent redeemer (back-compat/admin). For single-use codes = THE redeemer; '
    'the full set lives in redeemed_by_users.';

-- Sanity constraints (guarded — ADD CONSTRAINT has no IF NOT EXISTS).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'plan_codes_max_uses_chk') THEN
        ALTER TABLE public.plan_codes
            ADD CONSTRAINT plan_codes_max_uses_chk CHECK (max_uses >= 1);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'plan_codes_uses_count_chk') THEN
        ALTER TABLE public.plan_codes
            ADD CONSTRAINT plan_codes_uses_count_chk
            CHECK (uses_count >= 0 AND uses_count <= max_uses);
    END IF;
END$$;

------------------------------------------------------------------------
-- 2. Backfill existing single-use redemptions into the new counter/array so the
--    capacity model is consistent with history (a 069-redeemed code = full).
------------------------------------------------------------------------
UPDATE public.plan_codes
   SET uses_count        = 1,
       redeemed_by_users = ARRAY[redeemed_by]
 WHERE redeemed_by IS NOT NULL
   AND uses_count = 0
   AND cardinality(redeemed_by_users) = 0;

------------------------------------------------------------------------
-- 3. Rewrite redeem_plan_code() — capacity-counter model with per-user dedup.
--    Same return shape (plan_id, name_ar, expires_at) and same existing errors;
--    adds 'code_already_redeemed'. Writes the SSoT (user_subscriptions) only —
--    the legacy users mirror is gone (dropped by 080).
--
--    Raises:
--      'plan_already_active'   — caller holds an active basic/pro/max/dev plan
--                                (code NOT consumed).
--      'code_already_redeemed' — caller already redeemed THIS code (no slot used).
--      'code_invalid_or_used'  — unknown code, expired, or capacity exhausted.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.redeem_plan_code(p_code TEXT, p_user_id UUID)
RETURNS TABLE(plan_id TEXT, name_ar TEXT, expires_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_norm      TEXT := upper(regexp_replace(coalesce(p_code, ''), '[^A-Za-z0-9]', '', 'g'));
    v_cur       TEXT;
    v_curexp    TIMESTAMPTZ;
    v_plan      TEXT;
    v_max       INTEGER;
    v_used      INTEGER;
    v_redeemers UUID[];
    v_codeexp   TIMESTAMPTZ;
    v_dur       INTEGER;
    v_newexp    TIMESTAMPTZ;
BEGIN
    -- 1. Lock the subscription row; block if an ACTIVE paid/dev plan would be
    --    downgraded by a (typically 7-day marketing) code.
    SELECT s.plan_id, s.expires_at
      INTO v_cur, v_curexp
      FROM public.user_subscriptions s
     WHERE s.user_id = p_user_id
       FOR UPDATE;

    IF v_cur IN ('basic', 'pro', 'max', 'dev')
       AND (v_curexp IS NULL OR v_curexp > now()) THEN
        RAISE EXCEPTION 'plan_already_active';   -- code NOT burned
    END IF;

    -- 2. Lock the code row and validate existence → shelf-life → dedup → capacity.
    --    The FOR UPDATE serializes concurrent redeemers of the same code, so the
    --    capacity check below sees the latest committed uses_count.
    SELECT c.plan_id, c.max_uses, c.uses_count, c.redeemed_by_users, c.expires_at
      INTO v_plan, v_max, v_used, v_redeemers, v_codeexp
      FROM public.plan_codes c
     WHERE c.code = v_norm
       FOR UPDATE;

    IF v_plan IS NULL THEN
        RAISE EXCEPTION 'code_invalid_or_used';            -- unknown code
    END IF;
    IF v_codeexp IS NOT NULL AND v_codeexp <= now() THEN
        RAISE EXCEPTION 'code_invalid_or_used';            -- code past its shelf life
    END IF;
    IF p_user_id = ANY(v_redeemers) THEN
        RAISE EXCEPTION 'code_already_redeemed';           -- this user already used it
    END IF;
    IF v_used >= v_max THEN
        RAISE EXCEPTION 'code_invalid_or_used';            -- capacity exhausted
    END IF;

    -- 3. Consume one slot (atomic under the row lock held above).
    UPDATE public.plan_codes c
       SET uses_count        = c.uses_count + 1,
           redeemed_by_users = array_append(c.redeemed_by_users, p_user_id),
           redeemed_by       = p_user_id,   -- most-recent redeemer (back-compat)
           redeemed_at       = now()
     WHERE c.code = v_norm;

    -- 4. Grant. Compute expiry EXPLICITLY (renewal trap: the assignment trigger
    --    only fires on a plan_id CHANGE, so renewing the same plan must set it).
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
    'Atomically redeem an N-use plan code → activate user_subscriptions (SSoT). '
    'One redemption per user per code; blocks downgrade of active paid/dev plans; '
    'stamps expiry explicitly. Raises plan_already_active / code_already_redeemed '
    '/ code_invalid_or_used. Called by POST /api/v1/plans/redeem (service role).';
