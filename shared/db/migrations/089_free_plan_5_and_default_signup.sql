-- Migration 089: bump the free plan to 5 points + default new signups to free.
--
-- WHY ----------------------------------------------------------------------
-- 1. The free (fallback / not-yet-paid) allowance was 3/3/3 points — too tight.
--    Raise every window to 5 points so free users get a slightly larger taste.
-- 2. New accounts were seeded LOCKED (plan_id NULL) by handle_new_user (079),
--    so every signup needed a manual operator activation before they could send
--    anything. Switch the default to the free plan (active, non-expiring) so new
--    accounts work out of the box on the free allowance.
--
-- WHAT ----------------------------------------------------------------------
--  1. UPDATE plans: free session/weekly/monthly = 5 (keeps all windows equal so
--     the sub-windows never exceed the monthly cap — free stays the fallback tier).
--  2. Rewrite handle_new_user() to seed a FREE / active / signup subscription row
--     instead of NULL / locked. Everything else (users row, terms stamping, the
--     never-break-signup EXCEPTION sub-block) is unchanged from 079.
--
-- Free has duration_days = NULL → non-expiring, so expires_at stays NULL and the
-- assignment trigger (UPDATE-only) is irrelevant on this INSERT.
--
-- Dependencies: 068 (plans), 079 (user_subscriptions + handle_new_user), 080
-- (legacy users.* columns already dropped — do NOT reference them here).
-- Idempotent.

------------------------------------------------------------------------
-- 1. Free plan → 5 points across every window.
------------------------------------------------------------------------
UPDATE public.plans
   SET points_session = 5,
       points_weekly  = 5,
       points_monthly = 5
 WHERE plan_id = 'free';

------------------------------------------------------------------------
-- 2. Default new signups to the free plan (was locked / NULL in 079).
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
DECLARE
    v_user_id uuid;
BEGIN
    INSERT INTO public.users (auth_id, email, full_name_ar, terms_accepted_at, terms_version)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name_ar', NEW.email),
        now(),
        NEW.raw_user_meta_data->>'terms_version'
    )
    RETURNING user_id INTO v_user_id;

    BEGIN
        INSERT INTO public.user_subscriptions (user_id, plan_id, status, source)
        VALUES (v_user_id, 'free', 'active', 'signup')
        ON CONFLICT (user_id) DO NOTHING;
    EXCEPTION WHEN OTHERS THEN
        -- Never let the subscription seed break account creation.
        RAISE WARNING 'handle_new_user: user_subscriptions seed failed for %: %',
            v_user_id, SQLERRM;
    END;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
