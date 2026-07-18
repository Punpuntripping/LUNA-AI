-- 094_marketing_opt_in.sql
-- Date: 2026-07-17
--
-- Purpose:
--   Signup gains an OPTIONAL, default-CHECKED marketing-consent checkbox
--   ("أوافق على استلام محتوى ترويجي وتحديثات عبر البريد الإلكتروني").
--   Unlike the mandatory terms checkbox (075, "option B"), this one never
--   blocks registration — it only records whether we may email the user
--   promotional content.
--
--     * marketing_opt_in — TRUE (default) = user consented to marketing email.
--                          FALSE = user unchecked the box at signup (or later
--                          opted out via a future settings toggle).
--
--   The frontend carries marketing_opt_in through supabase.auth.signUp()
--   options.data (raw_user_meta_data), same channel as terms_version — signup
--   is entirely client-side, there is NO backend register route. The
--   handle_new_user() trigger stamps it at row creation:
--     * Email/password — explicit boolean from the checkbox.
--     * Google OAuth   — key absent → COALESCE lands TRUE (consent by the
--       signup fine-print, same by-action model as terms).
--
--   Pre-existing users: NOT NULL DEFAULT true backfills them to TRUE — they
--   signed up before the checkbox existed and the product default is opt-in.
--
-- RLS: no policy change — RLS gates rows, not columns; the users table's
-- self-row policies (016/017) already cover the new column.
--
-- Dependencies:
--   - 003_users.sql    (users table)
--   - 091_derived_subscription_status.sql (current handle_new_user body —
--     verified identical to live prod 2026-07-17 before this rewrite)
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + CREATE OR REPLACE FUNCTION.

------------------------------------------------------------------------
-- 1. Marketing-consent column on public.users.
------------------------------------------------------------------------
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS marketing_opt_in BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.users.marketing_opt_in IS
    'Consent to receive promotional/marketing email. Stamped by handle_new_user() '
    'from raw_user_meta_data->>''marketing_opt_in'' (signup checkbox, default '
    'checked); TRUE when the key is absent (Google OAuth, pre-094 users).';

------------------------------------------------------------------------
-- 2. Stamp marketing consent at user-row creation.
--    Re-creates public.handle_new_user() (091 body + marketing_opt_in).
--    The on_auth_user_created trigger is unchanged.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_user_id uuid;
BEGIN
    INSERT INTO public.users (auth_id, email, full_name_ar, terms_accepted_at, terms_version, marketing_opt_in)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name_ar', NEW.email),
        now(),
        NEW.raw_user_meta_data->>'terms_version',
        COALESCE((NEW.raw_user_meta_data->>'marketing_opt_in')::boolean, true)
    )
    RETURNING user_id INTO v_user_id;

    BEGIN
        INSERT INTO public.user_subscriptions (user_id, plan_id, source)
        VALUES (v_user_id, 'free', 'signup')
        ON CONFLICT (user_id) DO NOTHING;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'handle_new_user: user_subscriptions seed failed for %: %',
            v_user_id, SQLERRM;
    END;

    RETURN NEW;
END;
$$;
