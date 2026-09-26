-- 166_users_revoke_client_writes.sql
--
-- Closes a signup-squatting / self-privilege hole on public.users.
--
-- Verified live 2026-09-25: public.users granted INSERT/SELECT/UPDATE/DELETE/
-- TRUNCATE/REFERENCES/TRIGGER to BOTH anon and authenticated, with RLS
-- policies:
--   users_select_own  SELECT USING (auth_id = auth.uid())
--   users_update_own  UPDATE USING/CHECK (auth_id = auth.uid())
--   users_insert_own  INSERT CHECK (auth_id = auth.uid())
--
-- users_update_own only pins the ROW, not the COLUMNS, so any signed-in user
-- could, with the public anon key + their own JWT:
--
--   PATCH /rest/v1/users?auth_id=eq.<own auth id>  {"email": "victim@x.sa"}
--
-- users.email carries UNIQUE (users_email_key). When the victim later signs
-- up, handle_new_user() (SECURITY DEFINER, AFTER INSERT ON auth.users) inserts
-- public.users with that email -> unique_violation -> the whole signup
-- transaction rolls back. The victim can never register. The same PATCH also
-- let a user self-write terms_accepted_at, deletion_requested_at, retired
-- entitlement columns (can_access_blog, ord_cost_*_limit_usd), etc.
--
-- Safe to revoke — re-verified in the repo before writing this:
--   * frontend/ makes ZERO supabase-js data calls: every `supabase.` usage is
--     `supabase.auth.*` (auth-store, AuthSync, LoginForm, GoogleQuickSignup,
--     auth/callback, forgot/reset-password, SignupCompletedTracker, lib/api).
--     No `.from(`, `.rpc(`, `.storage`, or /rest/v1 anywhere in frontend/.
--     So the browser neither WRITES nor READS public.users directly (SELECT
--     is still kept for authenticated — see step 2).
--   * backend/ shared/ agents/: the only anon-key clients
--     (get_supabase_anon_client, create_isolated_anon_client) are used purely
--     for GoTrue (sign-in, refresh, OTP, password re-check). get_user_client()
--     (anon key + set_session) exists in shared/db/client.py but has ZERO
--     callers. Every public.users read/write goes through the service-role
--     client, which bypasses both grants and RLS.
--   * handle_new_user() is SECURITY DEFINER (runs as its owner), and the
--     auth.users UPDATE trigger runs as GoTrue's supabase_auth_admin — neither
--     executes as anon/authenticated, so signup/login are unaffected.
--
-- users_update_own / users_insert_own become unreachable and are dropped.
-- users_select_own is kept: harmless with the SELECT grant gone, and it keeps
-- the row scoping correct if a narrow grant is ever re-added.
--
-- Idempotent: safe to re-run.

begin;

-- ---------------------------------------------------------------------------
-- 1. No client role may write, truncate, reference, or trigger on users.
-- ---------------------------------------------------------------------------
revoke insert, update, delete, truncate, references, trigger
    on public.users from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. anon never reads users. authenticated KEEPS SELECT: 53 RLS policies on
--    other public tables resolve the caller via `(SELECT user_id FROM users
--    WHERE auth_id = auth.uid())` as the invoker — revoking it would turn
--    those into "permission denied" instead of a row-scoped check.
--    users_select_own still limits it to the caller's own row.
-- ---------------------------------------------------------------------------
revoke select on public.users from anon;

-- ---------------------------------------------------------------------------
-- 3. Drop the write policies that no longer have a grant behind them.
-- ---------------------------------------------------------------------------
drop policy if exists users_update_own on public.users;
drop policy if exists users_insert_own on public.users;

-- ---------------------------------------------------------------------------
-- 4. RLS stays on.
-- ---------------------------------------------------------------------------
alter table public.users enable row level security;

commit;

-- ---------------------------------------------------------------------------
-- Verification (read-only, run after applying):
--
-- select r.role, p.priv,
--        has_table_privilege(r.role, 'public.users', p.priv) as granted
--   from (values ('anon'), ('authenticated'), ('service_role')) r(role),
--        (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
--                ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')) p(priv)
--  order by 1, 2;
--   -> anon: all false. authenticated: only SELECT true. service_role: all true.
--
-- select policyname, cmd, roles, qual, with_check
--   from pg_policies where schemaname = 'public' and tablename = 'users';
--   -> only users_select_own remains.
--
-- select relrowsecurity from pg_class where oid = 'public.users'::regclass;
--   -> true
-- ---------------------------------------------------------------------------
