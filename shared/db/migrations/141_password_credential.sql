-- ════════════════════════════════════════════════════════════════════════════
-- 141 — "does this account have a password?" reads the CREDENTIAL, not identities
-- ════════════════════════════════════════════════════════════════════════════
--
-- Depends on: nothing in public. Reads auth.users, which GoTrue owns.
-- Idempotent: CREATE OR REPLACE + idempotent GRANT/REVOKE.
--
-- WHY ──────────────────────────────────────────────────────────────────────────
-- `account_service.has_password_identity()` answered "can this user sign in with
-- a password?" by scanning GoTrue's identity list for provider == 'email'. That
-- is the wrong signal, and it fails in exactly the case we are about to create.
--
-- Setting a password on an OAuth-only user (admin.update_user_by_id, or the
-- recovery flow) writes `auth.users.encrypted_password` and makes
-- signInWithPassword work — but it does NOT add an `email` row to
-- auth.identities. Supabase calls this a "ghost password"; see
-- https://github.com/orgs/supabase/discussions/37737.
--
-- So a Google user who sets a password would:
--   * be able to log in with it, and
--   * still be reported as having no password → إعدادات الحساب keeps hiding
--     تغيير كلمة المرور forever, /change-password keeps 400ing, and
--     /delete-account keeps using the type-to-confirm branch.
--
-- i.e. the exact complaint the set-password feature exists to fix would survive
-- the feature. The credential column is the honest answer: it is what GoTrue
-- itself checks when verifying a password grant.
--
-- As of this migration prod holds 33 users with NULL encrypted_password
-- (Google-only), 18 with one, and 0 in the ghost state — this lands before the
-- first ghost can be created, not after.
--
-- SECURITY ────────────────────────────────────────────────────────────────────
-- SECURITY DEFINER because `auth` is not readable by the API roles, and it must
-- stay that way — this function is the ONLY hole punched into it, it returns a
-- single boolean, and it is callable by service_role alone (the backend's
-- client). Never grant it to anon/authenticated: a per-user "has a password"
-- oracle handed to anonymous callers is an account-enumeration primitive.
-- ══════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.user_has_password(p_auth_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public', 'auth'
AS $$
    -- Treat '' as absent alongside NULL: GoTrue has written both over its
    -- lifetime for "no password", and an empty hash can never verify.
    SELECT EXISTS (
        SELECT 1
        FROM auth.users u
        WHERE u.id = p_auth_id
          AND u.encrypted_password IS NOT NULL
          AND u.encrypted_password <> ''
    );
$$;

COMMENT ON FUNCTION public.user_has_password(uuid) IS
    'True when the auth user holds a usable password credential. Reads '
    'auth.users.encrypted_password rather than auth.identities because setting '
    'a password on an OAuth-only account does not create an email identity '
    '("ghost password"). service_role only — an anon-callable version would be '
    'an account-enumeration oracle. Migration 141.';

REVOKE ALL ON FUNCTION public.user_has_password(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.user_has_password(uuid) FROM anon;
REVOKE ALL ON FUNCTION public.user_has_password(uuid) FROM authenticated;
GRANT EXECUTE ON FUNCTION public.user_has_password(uuid) TO service_role;
