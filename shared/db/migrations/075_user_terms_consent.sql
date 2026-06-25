-- 075_user_terms_consent.sql
-- Date: 2026-06-23
-- Related plan: .claude/plans/legal_docs_terms_privacy.md (Phase 5 — consent persistence)
--
-- Purpose:
--   The ريحان legal-docs feature captures EXPLICIT mandatory-checkbox consent
--   ("option B") at signup. A gated checkbox alone leaves no evidence, so the
--   consent must be RECORDED on the user row. This migration adds the two
--   nullable columns the backend bootstrap stamps when a user accepts:
--
--     * terms_accepted_at — when the user accepted (stamped server-side at now()).
--     * terms_version     — which legal-doc version they accepted. Matches the
--                           frontend LEGAL_VERSION constant (frontend/lib/legal.ts),
--                           currently "2026-06-22". Stored so we can re-prompt when
--                           the docs change (stored version != current LEGAL_VERSION).
--
--   The frontend already carries terms_version through supabase.auth.signUp()
--   into the user's raw_user_meta_data (see frontend/stores/auth-store.ts).
--   Signup is ENTIRELY client-side — there is NO backend register route (see
--   backend/app/api/auth.py) — and the public.users row is created by the
--   public.handle_new_user() trigger that fires on auth.users INSERT
--   (014_triggers.sql). So this migration ALSO updates that trigger to stamp
--   consent at row-creation time. That single point covers BOTH paths:
--     * Email/password — terms_version is present in metadata (explicit checkbox).
--     * Google OAuth   — terms_version is absent (consent is by-action via the
--                        signup fine print); terms_version lands NULL but
--                        terms_accepted_at is still stamped at signup time.
--
-- Both columns are NULLABLE on purpose: pre-existing users (signed up before this
-- migration) carry NULL = "no recorded consent under the new tracking" (they
-- accepted under the prior by-use model). Google users carry a timestamp with a
-- NULL version = "consented by action".
--
-- RLS: No policy change is needed. New columns inherit the users table's existing
-- row-level security policies (enabled in 016_rls.sql, self-row scoped via auth_id
-- in 017_rls_fix_users_authuid.sql) — RLS gates rows, not individual columns, so
-- the same self-row USING/WITH CHECK clauses already cover these additions.
--
-- Dependencies:
--   - 003_users.sql            (users table: PK user_id, FK auth_id -> auth.users)
--   - 014_triggers.sql         (defines public.handle_new_user(); this file
--                               re-creates it to also stamp the consent columns)
--
-- This migration is idempotent: ADD COLUMN IF NOT EXISTS is a no-op on re-run, and
-- CREATE OR REPLACE FUNCTION simply re-installs the latest function body.

------------------------------------------------------------------------
-- 1. Consent-tracking columns on public.users.
------------------------------------------------------------------------
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS terms_version     TEXT;

COMMENT ON COLUMN public.users.terms_accepted_at IS
    'When the user accepted the Terms & Privacy docs (stamped at now() by the '
    'handle_new_user() trigger on signup). NULL = no recorded consent yet.';
COMMENT ON COLUMN public.users.terms_version IS
    'Which legal-doc version the user consented to. Matches frontend LEGAL_VERSION '
    '(frontend/lib/legal.ts), e.g. "2026-06-22". Re-prompt when this != current. '
    'NULL = consented by action (Google) or no recorded consent yet.';

------------------------------------------------------------------------
-- 2. Stamp consent at user-row creation.
--    Re-creates public.handle_new_user() (original in 014_triggers.sql) so the
--    same auto-profile-creation path also records consent. The trigger
--    (on_auth_user_created, AFTER INSERT ON auth.users) is unchanged and keeps
--    pointing at this function — no need to re-create the trigger itself.
--      * terms_accepted_at = now()  -> every new signup consented at creation
--        (email via checkbox, Google via by-action fine print).
--      * terms_version = raw_user_meta_data->>'terms_version' -> the explicit
--        version for email/password; NULL for Google (no metadata key).
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.users (auth_id, email, full_name_ar, terms_accepted_at, terms_version)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name_ar', NEW.email),
        now(),
        NEW.raw_user_meta_data->>'terms_version'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
