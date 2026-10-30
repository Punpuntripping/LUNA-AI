-- ============================================================================
-- Migration 195 — take the workspace tables off the PostgREST surface for
--                 client roles (anon, authenticated).
--
-- Source: .claude/plans/wi_preview_lock.md §1.1 — verified live against prod
--         dwgghvxogtwyaxmbgjod on 2026-10-30, immediately before this file.
--
-- THE HOLE ───────────────────────────────────────────────────────────────────
-- `anon` and `authenticated` hold ALL privileges (SELECT/INSERT/UPDATE/DELETE/
-- TRUNCATE/REFERENCES/TRIGGER) on workspace_items, workspace_item_references
-- and retrieval_artifacts — the inherited Supabase default 118 §6 stopped for
-- NEW tables but did not sweep. RLS limits a user to their OWN rows, but with
-- the anon key from the web bundle plus their own JWT, a signed-in user can,
-- straight through /rest/v1/:
--   * read the full `content_md` of every workspace item — which makes any
--     server-side preview lock (wi_preview_lock.md) decorative;
--   * UPDATE their own rows — rewrite `created_at` / `kind` / `created_by` /
--     `metadata` to dodge that lock, or edit content no FastAPI route allows;
--   * INSERT fabricated agent items / references into their own workspace.
-- None of it passes through FastAPI's ownership checks, quotas or audit log.
--
-- Verified before writing, so the revokes cannot break the product:
--   * frontend/ and mobile/ contain ZERO `.from(` / `.rpc(` calls and no
--     `postgres_changes` / `.channel(` subscription — the browser Supabase
--     client is GoTrue auth only (same finding as 118).
--   * None of the three tables is in a realtime publication.
--   * The backend reads/writes them as service_role (`get_supabase()`,
--     backend/app/deps.py:123). Its anon-key clients (`app.state.supabase_auth`,
--     `create_isolated_anon_client` in backend/app/api/auth.py) serve GoTrue
--     only; `get_user_client` (shared/db/client.py:238) has no caller.
--   * SECURITY INVOKER functions that touch these tables
--     (enforce_artifact_cap, assign_workspace_item_seq,
--     recompute_used_ref_words_total, purge_user_data) run as service_role:
--     the triggers fire on service_role writes, and purge_user_data is called
--     by account_purge_service.py:76 on the service_role client.
--   * reranker_runs_select / _insert policies subquery retrieval_artifacts;
--     reranker_runs is only ever touched by the service_role pipeline.
--
-- WHAT THIS FILE DOES ─────────────────────────────────────────────────────────
--   REVOKE ALL on the three tables FROM anon, authenticated. RLS stays ENABLED
--   (absolute rule 6) and the existing policies are left in place — they are
--   inert without a grant, and leaving them means a deliberate re-GRANT later
--   restores exactly the old row scoping instead of an open table.
--
-- IDEMPOTENT: REVOKE of a privilege already gone is a no-op. Safe to run twice.
-- ROLLBACK (if ever needed):
--   GRANT SELECT, INSERT, UPDATE, DELETE ON public.workspace_items,
--     public.workspace_item_references, public.retrieval_artifacts
--     TO authenticated;
-- ============================================================================

BEGIN;

REVOKE ALL ON TABLE public.workspace_items           FROM anon, authenticated;
REVOKE ALL ON TABLE public.workspace_item_references FROM anon, authenticated;
REVOKE ALL ON TABLE public.retrieval_artifacts       FROM anon, authenticated;

-- ── self-tests ───────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_left   int;
    v_rls    int;
    v_svc    int;
BEGIN
    SELECT count(*) INTO v_left
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name IN ('workspace_items', 'workspace_item_references', 'retrieval_artifacts')
       AND grantee IN ('anon', 'authenticated', 'PUBLIC');
    IF v_left <> 0 THEN
        RAISE EXCEPTION '195: % client-role privileges remain on the workspace tables', v_left;
    END IF;

    SELECT count(*) INTO v_rls
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relname IN ('workspace_items', 'workspace_item_references', 'retrieval_artifacts')
       AND c.relrowsecurity;
    IF v_rls <> 3 THEN
        RAISE EXCEPTION '195: RLS must stay enabled on all three tables (found % of 3)', v_rls;
    END IF;

    SELECT count(DISTINCT table_name) INTO v_svc
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name IN ('workspace_items', 'workspace_item_references', 'retrieval_artifacts')
       AND grantee = 'service_role'
       AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE');
    IF v_svc <> 3 THEN
        RAISE EXCEPTION '195: service_role must keep DML on all three tables (found % of 3)', v_svc;
    END IF;

    RAISE NOTICE '195 OK: client roles revoked, RLS on, service_role intact';
END $$;

COMMIT;
