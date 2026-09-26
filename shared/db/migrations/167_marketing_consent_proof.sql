-- 167_marketing_consent_proof.sql
-- Date: 2026-09-24 (applied 2026-09-26)
--
-- Numbering: drafted as 164; lands as 167 because 164 (public_blogs english
-- slugs), 165 (push subscriptions) and 166 (users revoke client writes) were
-- taken first.
--
-- Purpose:
--   094 made marketing_opt_in TRUE in three ways nobody chose: the column
--   default backfilled every pre-094 user, Google OAuth lands TRUE because the
--   key is absent, and the signup checkbox was pre-ticked. PDPL Art. 25 wants
--   PRIOR, SPECIFIC consent for advertising plus a way to stop — and evidence
--   of when and how it was given. This migration:
--
--     1. Flips the default to FALSE. Absence now means no.
--     2. Adds marketing_consent_at / marketing_consent_src — the evidence of
--        the LATEST consent decision (yes or no), and where it came from:
--          'signup_checkbox' | 'settings_toggle' | 'repermission_email'
--          | 'unsubscribe'   | 'manual'
--     3. A BEFORE UPDATE trigger: when the flag changes and the writer did
--        NOT supply a new marketing_consent_at, it stamps at=now() and
--        src='manual' — so a hand-written service-role UPDATE can never leave
--        a changed flag without evidence. A writer that supplies a new
--        timestamp keeps its own src (the API always sends at+src).
--     4. handle_new_user(): absent key (Google OAuth) => FALSE, unstamped; a
--        present key (the signup checkbox, ticked or not) is stamped
--        'signup_checkbox' at row creation.
--
--   ⚠ NO BACKFILL of marketing_consent_at. For the 114 existing default-TRUE
--   rows a NULL there is the signal "this TRUE is a default, not a decision" —
--   the re-permission email (marketing plans/email/00 §5 T9b) targets exactly
--   `marketing_opt_in AND marketing_consent_at IS NULL`. Backfilling it with
--   created_at would manufacture evidence we do not have.
--
--   The existing rows are NOT flipped to FALSE either: Stream A (service mail)
--   ignores the flag, and Stream B reads `marketing_consent_at IS NOT NULL`
--   alongside it, so the old TRUEs are inert until someone affirms.
--
-- Dependencies:
--   - 094_marketing_opt_in.sql (the column)
--   - handle_new_user() body below verified identical to live prod 2026-09-24
--     (the name fallback full_name_ar → name → full_name → email is prod's,
--     newer than 094's text) before this rewrite.
--
-- Idempotent: ALTER ... SET DEFAULT, ADD COLUMN IF NOT EXISTS,
-- CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS.

------------------------------------------------------------------------
-- 1. Absence means no.
------------------------------------------------------------------------
ALTER TABLE public.users ALTER COLUMN marketing_opt_in SET DEFAULT false;

------------------------------------------------------------------------
-- 2. Evidence columns.
------------------------------------------------------------------------
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS marketing_consent_at  timestamptz;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS marketing_consent_src text;

COMMENT ON COLUMN public.users.marketing_opt_in IS
    'Consent to receive promotional/marketing email (Stream B). Default FALSE '
    'since 167. A TRUE with marketing_consent_at NULL is a pre-167 default, '
    'not a decision — never mail Stream B on it.';
COMMENT ON COLUMN public.users.marketing_consent_at IS
    'When the latest marketing-consent decision (yes or no) was recorded. '
    'NULL = never decided. Never backfilled.';
COMMENT ON COLUMN public.users.marketing_consent_src IS
    'Where the latest decision came from: signup_checkbox | settings_toggle | '
    'repermission_email | unsubscribe | manual.';

------------------------------------------------------------------------
-- 3. Stamp evidence on every change of the flag.
--    Rule: the writer's timestamp is the signal, not its src.
--      * flag changed, marketing_consent_at NOT changed by the writer
--          => at := now(), src := 'manual' (a hand-written UPDATE).
--      * flag changed, writer supplied a new marketing_consent_at
--          => keep the writer's at AND src as-is (the API always sends both).
--    Comparing src to OLD.src would be wrong: Settings ON then OFF sends
--    src='settings_toggle' both times, and would have been relabelled
--    'manual' on the second write.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.stamp_marketing_consent()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.marketing_opt_in IS DISTINCT FROM OLD.marketing_opt_in THEN
        IF NEW.marketing_consent_at IS NOT DISTINCT FROM OLD.marketing_consent_at THEN
            NEW.marketing_consent_at  := now();
            NEW.marketing_consent_src := 'manual';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS users_stamp_marketing_consent ON public.users;
CREATE TRIGGER users_stamp_marketing_consent
    BEFORE UPDATE OF marketing_opt_in ON public.users
    FOR EACH ROW EXECUTE FUNCTION public.stamp_marketing_consent();

------------------------------------------------------------------------
-- 4. handle_new_user(): live-prod body, marketing default flipped + stamped.
--    The on_auth_user_created trigger is unchanged.
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_user_id uuid;
    -- Key present = the checkbox was shown and answered (yes OR no — both are
    -- decisions worth stamping). Absent = Google OAuth, no decision: FALSE, NULL.
    v_decided boolean := NEW.raw_user_meta_data ? 'marketing_opt_in';
    v_opt_in  boolean := COALESCE((NEW.raw_user_meta_data->>'marketing_opt_in')::boolean, false);
BEGIN
    INSERT INTO public.users (
        auth_id, email, full_name_ar, terms_accepted_at, terms_version,
        marketing_opt_in, marketing_consent_at, marketing_consent_src
    )
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(
            NULLIF(btrim(NEW.raw_user_meta_data->>'full_name_ar'), ''),
            NULLIF(btrim(NEW.raw_user_meta_data->>'name'), ''),
            NULLIF(btrim(NEW.raw_user_meta_data->>'full_name'), ''),
            NEW.email
        ),
        now(),
        NEW.raw_user_meta_data->>'terms_version',
        v_opt_in,
        CASE WHEN v_decided THEN now() END,
        CASE WHEN v_decided THEN 'signup_checkbox' END
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
