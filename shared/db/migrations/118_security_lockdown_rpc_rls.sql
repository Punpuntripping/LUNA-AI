-- ============================================================================
-- Migration 118 — security lockdown: SECURITY DEFINER grants, one RLS-off
--                 table, and a forgeable audit trail.
--
-- Source: agents_reports/security_review_2026-08-07.md — findings H-1, H-2,
--         H-3, M-3 (all four live-verified against prod dwgghvxogtwyaxmbgjod
--         on 2026-08-07, and re-verified against pg_proc / pg_policy /
--         information_schema immediately before this file was written).
--
-- THE PREMISE THAT MAKES ALL FOUR REACHABLE ─────────────────────────────────
-- The Supabase anon key ships inside the public frontend bundle. Therefore
-- `anon` and `authenticated` can speak to PostgREST directly at /rest/v1/...
-- and /rest/v1/rpc/..., which is a completely different door from FastAPI.
-- Every ownership check, every Redis brute-force wall, every IP rate limit in
-- this product lives in FastAPI and is simply NOT in that path. So a grant to
-- anon/authenticated is not "a permission the app might use" — it is a public
-- internet endpoint. The backend talks to Postgres as `service_role`
-- (shared/db/client.py:116, surfaced as `get_supabase()` in
-- backend/app/deps.py:123), which is unaffected by everything below.
--
-- Verified before writing, so the revokes cannot break the product:
--   * frontend/ contains ZERO `.from(` and ZERO `.rpc(` calls. The browser
--     Supabase client (frontend/lib/supabase.ts:11) is used for GoTrue auth
--     only — it never reads a table or calls an RPC.
--   * The backend's anon-key client (shared/db/client.py:134) is wired to
--     `app.state.supabase_auth` (backend/app/main.py:99) and serves GoTrue
--     auth only — it never reads a table either.
--   * Every blog_posts read is `supabase.table("blog_posts")` in
--     backend/app/services/blog_service.py, reached via
--     `Depends(get_supabase)` = service_role.
--   * Every audit row is `supabase.table("audit_logs").insert(...)` in
--     backend/app/services/audit_service.py:36 (plus one direct insert in
--     account_purge_service.py:76), always with the service_role client.
--
-- WHAT THIS FILE DOES ────────────────────────────────────────────────────────
--   1. H-1a  REVOKE EXECUTE on bm25_search from PUBLIC/anon/authenticated.
--   2. H-1b  Take blog_posts off the PostgREST-readable surface for client
--            roles, killing the owner_user_id + token harvest.
--   3. H-2   RLS ON + revoke DML on cases_content_backup (the only RLS-off
--            table in `public`, and the single Supabase advisor ERROR).
--   4. H-3   REVOKE EXECUTE on redeem_plan_code + a caller-binding guard
--            inside the body, so it can never grant to someone else's account.
--   5. M-3   Drop the forge-anything audit_logs INSERT policy + revoke.
--   6. Guard ALTER DEFAULT PRIVILEGES so the NEXT table created in `public`
--            does not inherit the blanket anon/authenticated grant that made
--            H-2 possible.
--
-- NOT DONE, DELIBERATELY ─────────────────────────────────────────────────────
--   * No blanket REVOKE across the ~40 existing `public` tables that carry the
--     inherited anon INSERT/UPDATE/DELETE grants. The review verified those are
--     neutralised by RLS (H-2's table was the ONLY hole), and a sweeping revoke
--     is a separate, separately-tested change. Section 6 stops the bleeding for
--     new tables; the existing sweep is its own migration.
--   * H-4, H-5, M-1, M-2 and L-1 are application-code findings, not schema.
--
-- IDEMPOTENT: REVOKE on a privilege already gone is a no-op; ENABLE ROW LEVEL
-- SECURITY on an already-enabled table is a no-op; DROP POLICY IF EXISTS;
-- CREATE OR REPLACE FUNCTION; ALTER DEFAULT PRIVILEGES is declarative. Safe to
-- run twice.
-- ============================================================================


-- ════════════════════════════════════════════════════════════════════════════
-- 1. H-1a — bm25_search: anonymous cross-tenant read of private templates
-- ════════════════════════════════════════════════════════════════════════════
-- `prosecdef = true`, owner postgres, and the live proacl was
--   {postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,
--    service_role=X/postgres}
-- Its owner scope is a CALLER-SUPPLIED PARAMETER, never bound to the session:
--     and (case when p_owner is null then si.owner_user_id is null
--               else si.owner_user_id = p_owner end)
-- search_index has correct RLS, but SECURITY DEFINER bypasses RLS by
-- definition. So anyone holding the bundled anon key could harvest an
-- owner_user_id, pass it as p_owner, and read that user's private قوالبي
-- templates and unpublished blog drafts — including `slug`, which IS the
-- secret share token.
--
-- The ONLY callers are backend/app/api/search*.py and the قوالبي/مدوناتي list
-- endpoints, all of which hold the service_role client. Nothing client-side
-- calls this RPC (verified: zero `.rpc(` in frontend/).
--
-- Done as a loop over pg_proc rather than a hard-coded signature for two
-- reasons: migrations 111 and 112 each (re)defined this function, so an
-- orphaned overload with a different argument list would silently keep the
-- grant; and this repo's migration files are known to drift from prod. The
-- live database currently holds exactly ONE overload:
--   bm25_search(text[], text, uuid, jsonb, integer, integer, integer,
--               numeric, numeric, numeric, numeric, numeric)
-- The loop revokes from every overload that exists at apply time.
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT p.oid::regprocedure::text AS sig
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname = 'bm25_search'
    LOOP
        EXECUTE format(
            'REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated', r.sig
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION %s TO service_role', r.sig
        );
        RAISE NOTICE '118: locked down %', r.sig;
    END LOOP;
END $$;

COMMENT ON FUNCTION public.bm25_search(
    text[], text, uuid, jsonb, integer, integer, integer,
    numeric, numeric, numeric, numeric, numeric
) IS
  'BM25 ranking over search_index for the 9 navigation surfaces '
  '(bm25_navigation_search.md). SECURITY DEFINER because search_index spans '
  'pipeline-owned corpora AND user-owned rows. SERVICE_ROLE ONLY (migration '
  '118 / security review 2026-08-07 H-1): p_owner is a caller-supplied tenant '
  'scope that is NEVER bound to auth.uid(), so an anon/authenticated EXECUTE '
  'grant is a cross-tenant read of private قوالبي templates, unpublished blog '
  'drafts and their share tokens. If this ever needs to be client-callable, '
  'derive the owner from auth.uid() INSIDE the body and ignore p_owner — do '
  'not simply re-grant.';


-- ════════════════════════════════════════════════════════════════════════════
-- 2. H-1b — blog_posts: stop handing client roles the victim-UUID + token list
-- ════════════════════════════════════════════════════════════════════════════
-- Section 1 breaks the exploit; this breaks the reconnaissance that feeds it.
--
-- Live state before this migration: anon AND authenticated both held
-- SELECT+INSERT+UPDATE+DELETE+TRUNCATE+REFERENCES+TRIGGER (the inherited
-- Supabase blanket grant), and policy `blog_posts_public_read`
-- (USING: is_published AND deleted_at IS NULL) is TO {authenticated, anon}.
-- That combination made
--     GET /rest/v1/blog_posts?select=owner_user_id,token&is_published=eq.true
-- an anonymous dump of every published post's OWNER UUID (the p_owner input
-- for H-1) and its `token` — which is the secret share link for posts that are
-- published but is_public = false.
--
-- The write grants were already inert (there is no anon/authenticated INSERT,
-- UPDATE or DELETE policy that a non-owner can satisfy), so revoking them
-- changes no behaviour; it just removes the standing primitive, exactly as
-- H-2 below proves is worth doing.
--
-- Column-level grants were considered and rejected: the public blog gallery
-- does not read this table as anon at all. /blog, /blog/<token> and the ISR
-- bake all go through FastAPI → blog_service.py → service_role. A narrowed
-- column grant would preserve an access path the product does not use.
--
-- authenticated is revoked alongside anon on the same evidence (zero `.from(`
-- in frontend/): a free registered account was otherwise still able to dump
-- every published post's owner UUID and share token. If a future feature needs
-- direct client reads, add a scoped policy AND an explicit column grant then —
-- do not restore the blanket grant.
REVOKE ALL ON TABLE public.blog_posts FROM anon;
REVOKE ALL ON TABLE public.blog_posts FROM authenticated;

-- `blog_posts_public_read` is intentionally LEFT IN PLACE. With the table
-- grant gone it is unreachable for both roles (PostgREST needs the grant
-- before RLS is ever consulted), and keeping it documents what "public" would
-- mean if a direct client read is ever deliberately re-enabled.

COMMENT ON COLUMN public.blog_posts.owner_user_id IS
  'FK users.user_id. NEVER expose to anon/authenticated over PostgREST: this '
  'column is the p_owner input that made bm25_search a cross-tenant read '
  '(security review 2026-08-07 H-1). Table grants revoked in migration 118.';

COMMENT ON COLUMN public.blog_posts.token IS
  'Secret share token — the /blog/<token> URL. Unguessable by design, which '
  'only holds while the token list itself is not readable. Client-role table '
  'grants revoked in migration 118; reads go through the service_role backend.';


-- ════════════════════════════════════════════════════════════════════════════
-- 3. H-2 — cases_content_backup: RLS disabled with full DML granted to anon
-- ════════════════════════════════════════════════════════════════════════════
-- Live-verified: this is the ONLY table in `public` with relrowsecurity =
-- false, it has ZERO policies, and anon/authenticated hold SELECT, INSERT,
-- UPDATE, DELETE, TRUNCATE. It holds 30,531 rows — the retained full text of
-- the judgments corpus.
--
-- With RLS off, the inherited blanket grant is not neutralised by anything:
--     DELETE /rest/v1/cases_content_backup?id=neq.<any-uuid>
-- with the bundled anon key destroys all 30,531 rows. An INSERT is worse in a
-- quieter way — attacker-authored `content` in a table whose provenance reads
-- as trusted corpus if it is ever used to restore or re-ingest `cases`.
--
-- Migration 067 closed exactly this class on cases / case_sections / entities
-- / services. This table was created afterwards and inherited the default.
--
-- NO POLICY IS ADDED, on purpose. RLS enabled + zero policies = deny-all for
-- every non-superuser, non-owner role. service_role BYPASSES RLS, so the
-- backup/restore tooling is unaffected. Adding a policy here could only widen
-- access; the correct posture for a service_role-only backup table is silence.
ALTER TABLE public.cases_content_backup ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.cases_content_backup FROM anon;
REVOKE ALL ON TABLE public.cases_content_backup FROM authenticated;

COMMENT ON TABLE public.cases_content_backup IS
  'Retained full text of the judgments corpus (30,531 rows) — a service_role '
  'ONLY backup table. RLS enabled with ZERO policies = deny-all for anon and '
  'authenticated; service_role bypasses RLS, so restore tooling is unaffected. '
  'Do NOT add a policy here (migration 118 / security review 2026-08-07 H-2: '
  'this table was the single RLS-off hole in `public` and the only advisor '
  'ERROR — an unauthenticated DELETE would have destroyed every row).';


-- ════════════════════════════════════════════════════════════════════════════
-- 4. H-3 — redeem_plan_code: anon-executable, target account caller-supplied
-- ════════════════════════════════════════════════════════════════════════════
-- Live proacl before this migration:
--   {=X/postgres,postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,
--    service_role=X/postgres}
-- The leading `=X/postgres` is the DEFAULT PUBLIC EXECUTE grant that Postgres
-- puts on every new function and that nobody revoked. Every sibling money/quota
-- function was revoked and shows {postgres=X,service_role=X} only — verified
-- live for grant_plan(uuid,text,text,uuid), get_user_quota_state(uuid),
-- record_library_item_use(uuid,text,text) and revoke_plan_grant(uuid). This one
-- was missed.
--
-- Proven reachable: POST /rest/v1/rpc/redeem_plan_code with the public anon key
-- and a junk code returns HTTP 400 P0001 `code_invalid_or_used` — a RAISE from
-- inside the body, i.e. it executed. The control call to the correctly-revoked
-- grant_plan returns 401 42501 permission denied, which is what this should do.
--
-- Consequences of that reachability, all of which live outside FastAPI:
--   (a) the 5-fails/24h Redis wall and the 5/min IP limit in
--       backend/app/api/plans.py are absent from this path, so codes can be
--       enumerated at platform speed;
--   (b) p_user_id is caller-supplied, so a circulated multi-use code can be
--       burned onto a victim's account and rewrite their subscription;
--   (c) the downgrade guard runs BEFORE code validation, so even a garbage code
--       returns `plan_already_active` vs `code_invalid_or_used` — a paid-status
--       oracle for any user_id supplied.
--
-- Two independent fixes, because either alone is one mistake from re-opening:
--
--   4a. The grant. This is the fix.
--   4b. A caller-binding guard in the body. This is the fix that survives
--       someone re-granting EXECUTE in two years without reading this comment.
--
-- The body below is `pg_get_functiondef` from PROD, verbatim, with ONE addition:
-- step 0. Nothing else was retyped, reordered or reformatted — this function
-- moves subscriptions, and a blind rewrite from the 081 file text would revert
-- any prod drift (project rule: migration files are NOT the prod schema).
--
-- WHY THE GUARD IS SAFE FOR THE BACKEND: it fires only when auth.uid() IS NOT
-- NULL. auth.uid() reads the `sub` claim of the request JWT (verified live:
-- `select auth.uid() is null` → true under the service key). The service_role
-- key carries no `sub`, so the backend path is untouched — verified live, and
-- verified again in the post-apply block at the bottom of this file.
--
-- WHY `caller_not_owner` AND NOT ONE OF THE EXISTING STRINGS: backend/app/api/
-- plans.py:75-80 substring-matches `plan_already_active`,
-- `code_already_redeemed` and `code_invalid_or_used` to map RPC failures onto
-- 409/400 responses. `caller_not_owner` collides with none of them, so it falls
-- through to `logger.exception` + the generic 500 — which is correct: via the
-- backend this condition is unreachable, and if it ever fires it is an incident,
-- not a user-facing error. The three existing strings are unchanged.
--
-- WHY STEP 0 IS FIRST: it must precede the downgrade guard, because that guard
-- is itself the paid-status oracle described in (c) above.

CREATE OR REPLACE FUNCTION public.redeem_plan_code(p_code text, p_user_id uuid)
 RETURNS TABLE(plan_id text, name_ar text, expires_at timestamp with time zone)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
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
    v_caller    UUID;   -- migration 118
    v_self      UUID;   -- migration 118
BEGIN
    -- 0. Caller binding (migration 118 — security review 2026-08-07 H-3).
    --    Defence in depth behind the REVOKE below: if this function is ever
    --    re-exposed to a client role, it still cannot grant a plan to an
    --    account other than the caller's own.
    --    auth.uid() IS NULL for service_role (no `sub` claim) and for direct
    --    psql/ops sessions, which is exactly the set of callers allowed to name
    --    an arbitrary p_user_id. Anything with a session identity must match.
    v_caller := auth.uid();
    IF v_caller IS NOT NULL THEN
        SELECT u.user_id INTO v_self
          FROM public.users u
         WHERE u.auth_id = v_caller;

        IF v_self IS NULL OR v_self IS DISTINCT FROM p_user_id THEN
            RAISE EXCEPTION 'caller_not_owner';
        END IF;
    END IF;

    -- 1. Downgrade guard.
    SELECT s.plan_id, s.expires_at
      INTO v_cur, v_curexp
      FROM public.user_subscriptions s
     WHERE s.user_id = p_user_id
       FOR UPDATE;

    IF v_cur IN ('basic', 'pro', 'max', 'dev')
       AND (v_curexp IS NULL OR v_curexp > now()) THEN
        RAISE EXCEPTION 'plan_already_active';
    END IF;

    -- 2. Lock the code row and validate existence -> shelf-life -> dedup -> capacity.
    SELECT c.plan_id, c.max_uses, c.uses_count, c.redeemed_by_users, c.expires_at
      INTO v_plan, v_max, v_used, v_redeemers, v_codeexp
      FROM public.plan_codes c
     WHERE c.code = v_norm
       FOR UPDATE;

    IF v_plan IS NULL THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;
    IF v_codeexp IS NOT NULL AND v_codeexp <= now() THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;
    IF p_user_id = ANY(v_redeemers) THEN
        RAISE EXCEPTION 'code_already_redeemed';
    END IF;
    IF v_used >= v_max THEN
        RAISE EXCEPTION 'code_invalid_or_used';
    END IF;

    -- 3. Consume one slot (atomic under the row lock held above).
    UPDATE public.plan_codes c
       SET uses_count        = c.uses_count + 1,
           redeemed_by_users = array_append(c.redeemed_by_users, p_user_id),
           redeemed_by       = p_user_id,
           redeemed_at       = now()
     WHERE c.code = v_norm;

    -- 4. Grant.
    SELECT p.duration_days INTO v_dur FROM public.plans p WHERE p.plan_id = v_plan;
    v_newexp := CASE WHEN v_dur IS NULL THEN NULL
                     ELSE now() + make_interval(days => v_dur) END;

    INSERT INTO public.user_subscriptions
        (user_id, plan_id, source, started_at, expires_at, redeemed_code)
    VALUES
        (p_user_id, v_plan, 'code', now(), v_newexp, v_norm)
    ON CONFLICT (user_id) DO UPDATE SET
        plan_id       = EXCLUDED.plan_id,
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
$function$;

-- 4a. The grant. CREATE OR REPLACE preserves the existing ACL, so the REVOKE
--     must come after the definition above — otherwise the replace would not
--     undo it, but ordering it this way makes the final state unambiguous.
--     PUBLIC is listed explicitly: the hole here was the DEFAULT PUBLIC grant
--     (`=X/postgres`), not an anon-specific one, and revoking anon alone would
--     leave it wide open. Same pattern as grant_plan / get_user_quota_state /
--     record_library_item_use / revoke_plan_grant.
REVOKE ALL ON FUNCTION public.redeem_plan_code(text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.redeem_plan_code(text, uuid) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.redeem_plan_code(text, uuid) TO service_role;

COMMENT ON FUNCTION public.redeem_plan_code(text, uuid) IS
  'Atomically redeem an activation code -> assign its plan to p_user_id. '
  'SERVICE_ROLE ONLY (migration 118 / security review 2026-08-07 H-3): the '
  'default PUBLIC EXECUTE grant made this callable with the bundled anon key, '
  'which bypasses the 5-fails/24h Redis wall and the IP rate limit in '
  'backend/app/api/plans.py entirely. Step 0 binds p_user_id to auth.uid() '
  'whenever the session has one, so even a re-grant cannot redeem onto another '
  'account. Error strings plan_already_active / code_already_redeemed / '
  'code_invalid_or_used are a CONTRACT with plans.py:75-80 — do not rename.';


-- ════════════════════════════════════════════════════════════════════════════
-- 5. M-3 — audit_logs: any logged-in user could forge rows for another lawyer
-- ════════════════════════════════════════════════════════════════════════════
-- Live-verified: the table's ONLY policy was
--     audit_logs_insert_authenticated  INSERT  TO authenticated  WITH CHECK (true)
-- with qual = null. Columns include user_id, action, resource_type,
-- resource_id, metadata, ip_address, user_agent. Nothing bound user_id to the
-- caller, so any registered user could
--     POST /rest/v1/audit_logs {"user_id": "<someone else>", "action": ...}
-- and attribute fabricated actions — with a fabricated ip_address — to another
-- lawyer's account. For a legal product the evidentiary value of the audit
-- trail is the entire point of having one.
--
-- DROP rather than constrain, because the app never needed this policy:
-- backend/app/services/audit_service.py:36 inserts with the client passed in by
-- `Depends(get_supabase)`, which is service_role (backend/app/deps.py:123), and
-- service_role bypasses RLS. Same for the direct insert in
-- account_purge_service.py:76. A `WITH CHECK (user_id = ...)` policy would keep
-- a write path that has no legitimate user.
--
-- The SELECT grant goes too: anon and authenticated held SELECT with no SELECT
-- policy, so reads already returned zero rows. Removing the grant makes the
-- posture explicit instead of load-bearing on a missing policy.
DROP POLICY IF EXISTS audit_logs_insert_authenticated ON public.audit_logs;

REVOKE ALL ON TABLE public.audit_logs FROM anon;
REVOKE ALL ON TABLE public.audit_logs FROM authenticated;

COMMENT ON TABLE public.audit_logs IS
  'Append-only audit trail. WRITTEN ONLY BY THE SERVICE-ROLE BACKEND '
  '(backend/app/services/audit_service.py). RLS enabled with ZERO policies = '
  'deny-all for anon and authenticated; service_role bypasses RLS. Migration '
  '118 / security review 2026-08-07 M-3 dropped audit_logs_insert_authenticated '
  '(INSERT TO authenticated WITH CHECK (true)), which let any logged-in user '
  'forge rows carrying another user_id and a fabricated ip_address. If users '
  'ever need to READ their own audit trail, add a SELECT policy bound to '
  'auth.uid() plus a SELECT-only column grant — never an INSERT path.';


-- ════════════════════════════════════════════════════════════════════════════
-- 6. Recurrence guard — new tables must not be born open (H-2 prevention)
-- ════════════════════════════════════════════════════════════════════════════
-- H-2 did not happen because someone granted anon full DML on
-- cases_content_backup. It happened because a default privilege did it
-- automatically the moment the table was created, and the author never knew.
-- Live pg_default_acl for schema public, objtype 'r' showed anon and
-- authenticated holding `arwdDxtm` by default.
--
-- This affects ONLY tables created AFTER it is applied. It does not touch a
-- single existing table, existing grant, or existing policy — the sweep over
-- the ~40 already-granted tables is deliberately out of scope (see header).
--
-- NOTE ON GRANTOR: default ACLs are per-grantor. Two entries exist for schema
-- public — one owned by `postgres` and one by `supabase_admin`. Migrations here
-- are applied as `postgres` (verified: current_user = postgres), so tables
-- created by this repo inherit the postgres entry, which the unqualified
-- statement below clears. The supabase_admin entry is attempted best-effort
-- afterwards: `postgres` is usually not a member of `supabase_admin`, so it
-- would raise insufficient_privilege and abort the migration — hence the DO
-- block. If it is skipped, tables created by Supabase-internal tooling can
-- still be born open; that residual is covered by the advisor check in the
-- verification block below, which should stay at zero rows.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM anon, authenticated;

DO $$
BEGIN
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin IN SCHEMA public '
            'REVOKE ALL ON TABLES FROM anon, authenticated';
    RAISE NOTICE '118: cleared supabase_admin default table privileges in public';
EXCEPTION
    WHEN insufficient_privilege OR undefined_object THEN
        RAISE NOTICE
          '118: could not clear supabase_admin default privileges (%). '
          'Tables created by Supabase-internal tooling may still inherit the '
          'blanket anon/authenticated grant — keep the RLS-off check in the '
          'verification block on the ops rota.', SQLERRM;
END $$;


-- ════════════════════════════════════════════════════════════════════════════
-- POST-APPLY VERIFICATION — run this block manually; every check must PASS
-- ════════════════════════════════════════════════════════════════════════════
--
-- -- 1. H-1a — bm25_search: no overload may grant anon/authenticated/PUBLIC.
-- --    EXPECT: one row per overload, has_anon/has_auth/has_public all false,
-- --            has_service true.
-- --    has_public uses aclexplode (grantee = 0 IS the PUBLIC pseudo-role) — a
-- --    LIKE '%=X/%' on proacl::text would false-positive on 'anon=X/postgres'.
-- --    The acldefault() fallback matters because a NULL proacl means "never
-- --    touched", which is exactly the state where PUBLIC still holds EXECUTE.
-- SELECT p.oid::regprocedure                                       AS sig,
--        has_function_privilege('anon',          p.oid, 'EXECUTE') AS has_anon,
--        has_function_privilege('authenticated', p.oid, 'EXECUTE') AS has_auth,
--        has_function_privilege('service_role',  p.oid, 'EXECUTE') AS has_service,
--        EXISTS (SELECT 1
--                  FROM aclexplode(coalesce(p.proacl,
--                                           acldefault('f', p.proowner))) a
--                 WHERE a.grantee = 0)                             AS has_public,
--        p.proacl::text                                            AS acl
--   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--  WHERE n.nspname = 'public' AND p.proname = 'bm25_search';
--
-- -- 2. H-1b + H-2 + M-3 — no client-role grant survives on the three tables.
-- --    EXPECT: ZERO ROWS.
-- SELECT table_name, grantee, privilege_type
--   FROM information_schema.role_table_grants
--  WHERE table_schema = 'public'
--    AND table_name IN ('blog_posts', 'cases_content_backup', 'audit_logs')
--    AND grantee IN ('anon', 'authenticated');
--
-- -- 3. H-2 — RLS on, and still zero policies (a policy here would widen access).
-- --    EXPECT: relrowsecurity = true, n_policies = 0.
-- SELECT c.relname, c.relrowsecurity,
--        (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS n_policies
--   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'public' AND c.relname = 'cases_content_backup';
--
-- -- 4. H-2 recurrence — cases_content_backup must not reappear here, and no
-- --    other table may join it. EXPECT: ZERO ROWS.
-- SELECT c.relname
--   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity = false;
--
-- -- 5. H-3 — grants gone. EXPECT: has_anon / has_auth / has_public all false,
-- --    has_service true. has_public is THE check that matters here: the hole
-- --    was the default PUBLIC grant, which shows in proacl as a leading bare
-- --    `=X/postgres` and which revoking anon alone would have left in place.
-- SELECT p.oid::regprocedure                                       AS sig,
--        has_function_privilege('anon',          p.oid, 'EXECUTE') AS has_anon,
--        has_function_privilege('authenticated', p.oid, 'EXECUTE') AS has_auth,
--        has_function_privilege('service_role',  p.oid, 'EXECUTE') AS has_service,
--        EXISTS (SELECT 1
--                  FROM aclexplode(coalesce(p.proacl,
--                                           acldefault('f', p.proowner))) a
--                 WHERE a.grantee = 0)                             AS has_public,
--        p.proacl::text                                            AS acl
--   FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--  WHERE n.nspname = 'public' AND p.proname = 'redeem_plan_code';
--
-- -- 6. H-3 — the guard is present in the live body, and the three contract
-- --    error strings survived. EXPECT: all five columns true.
-- SELECT pg_get_functiondef('public.redeem_plan_code(text,uuid)'::regprocedure)
--          LIKE '%caller_not_owner%'      AS has_guard,
--        pg_get_functiondef('public.redeem_plan_code(text,uuid)'::regprocedure)
--          LIKE '%auth.uid()%'            AS binds_caller,
--        pg_get_functiondef('public.redeem_plan_code(text,uuid)'::regprocedure)
--          LIKE '%code_invalid_or_used%'  AS keeps_invalid,
--        pg_get_functiondef('public.redeem_plan_code(text,uuid)'::regprocedure)
--          LIKE '%plan_already_active%'   AS keeps_active,
--        pg_get_functiondef('public.redeem_plan_code(text,uuid)'::regprocedure)
--          LIKE '%code_already_redeemed%' AS keeps_redeemed;
--
-- -- 7. H-3 — the backend path is unaffected: under service_role there is no
-- --    `sub` claim, so the guard never fires. EXPECT: true.
-- SELECT auth.uid() IS NULL AS guard_inert_for_service_role;
--
-- -- 8. M-3 — the forge-anything policy is gone. EXPECT: ZERO ROWS.
-- SELECT p.polname, p.polcmd, pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr
--   FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
--   JOIN pg_namespace n ON n.oid = c.relnamespace
--  WHERE n.nspname = 'public' AND c.relname = 'audit_logs';
--
-- -- 9. Section 6 — default table privileges in `public` no longer include
-- --    anon/authenticated. EXPECT: for grantor 'postgres' / objtype 'r', the
-- --    acl contains neither 'anon=' nor 'authenticated='.
-- SELECT pg_get_userbyid(d.defaclrole) AS grantor, d.defaclobjtype AS objtype,
--        d.defaclacl::text AS acl
--   FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace
--  WHERE n.nspname = 'public' AND d.defaclobjtype = 'r';
--
-- -- 10. End-to-end, from OUTSIDE the database (the only check that proves the
-- --     PostgREST door is shut). Run with the PUBLIC anon key:
-- --     curl -s -o /dev/null -w '%{http_code}\n' \
-- --       -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
-- --       -X POST "$SUPABASE_URL/rest/v1/rpc/redeem_plan_code" \
-- --       -H 'Content-Type: application/json' \
-- --       -d '{"p_code":"ZZZZZ","p_user_id":"00000000-0000-0000-0000-000000000000"}'
-- --     EXPECT 401 with 42501 "permission denied" — NOT 400 P0001
-- --     "code_invalid_or_used" (that would mean the function still executed).
-- --
-- --     curl -s -o /dev/null -w '%{http_code}\n' \
-- --       -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
-- --       "$SUPABASE_URL/rest/v1/blog_posts?select=owner_user_id&is_published=eq.true"
-- --     EXPECT 401/permission denied — NOT a JSON array of UUIDs.
-- --
-- --     And confirm the product still works: GET https://rayhanai.com/blog must
-- --     still render the gallery (it reads via FastAPI as service_role), and a
-- --     real code redemption through POST /api/v1/plans/redeem must still 200.
