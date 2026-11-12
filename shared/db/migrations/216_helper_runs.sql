-- ============================================================================
-- Migration 216 — helper_runs: status + lease of the helper frame's item helpers.
--
-- Source: .claude/plans/helper_frame.md §5.3 (owner decision H6) and §7 Q2
--         (settled: a generic table for NEW item-scoped helpers).
--         Code: agents/helpers/status.py (SupabaseHelperRunStore).
--
-- WHAT IT IS ─────────────────────────────────────────────────────────────────
--   One row per (workspace item, helper), e.g. ('<item>', 'segmenter'). A run
--   claims the row with a lease ("running until lease_until"), renews it every
--   third of the lease while alive, and closes it as done / failed. Another turn
--   or process that finds a live lease polls instead of starting a second run;
--   an expired lease (the run was killed by a redeploy) or a retryable failure
--   is taken over by the next consumer that needs the result.
--
--   Every write is conditional on the row as read (attempt + status, and for a
--   'running' row an expired lease), so two consumers can never both start it.
--   The first claim is an INSERT … ON CONFLICT DO NOTHING (PostgREST upsert with
--   ignore-duplicates); later claims are UPDATE … WHERE attempt = ? AND status = ?.
--
--   OCR and the attachment summary are NOT stored here: they keep their
--   workspace_items.metadata markers (ocr_status, summary_attempt), which the
--   attachment viewer, the retention sweep and the OCR quota settle read. The
--   memory agent is message-scoped and has no item row.
--
-- COLUMNS ────────────────────────────────────────────────────────────────────
--   item_id      the workspace item; the row dies with it (ON DELETE CASCADE),
--                so account purge (purge_user_data deletes workspace_items) and
--                any hard delete clear it. A soft-deleted item keeps its row
--                until then; readers filter through the workspace_items join.
--   helper       registry name (agents/helpers/registry.py), e.g. 'segmenter'.
--   user_id      the item's owner (RLS + purge).
--   status       running | done | failed.
--   attempt      runs of this helper on this item under the current version;
--                the compare-and-set token of every write.
--   lease_until  NOT NULL while running; cleared when the run ends.
--   started_at / finished_at, error (exception type, ≤ 200 chars in the app),
--   version      the helper's version when the run was claimed.
--
-- SECURITY ───────────────────────────────────────────────────────────────────
--   RLS ENABLED (absolute rule 6). One owner-scoped SELECT policy in the repo's
--   pattern; no client write policy — leases are the backend's alone. Then
--   REVOKE ALL from anon / authenticated, the migration 195 stance: the backend
--   reads and writes as service_role, the browser never touches this table, and
--   the policy is inert without a grant (a deliberate future GRANT SELECT would
--   restore owner-only reads, never an open table).
--
-- VERIFIED LIVE (read-only, 2026-11-11, prod dwgghvxogtwyaxmbgjod):
--   * public.helper_runs does not exist; no similar table.
--   * workspace_items PK = item_id uuid; users.user_id uuid.
--   * workspace_items policies use
--       user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid()))
--   * default privileges on new postgres-owned tables grant service_role only.
--   * 215 is reserved by attachment segmentation (attachment_pages / segments);
--     this file does not depend on it.
--
-- IDEMPOTENT: CREATE … IF NOT EXISTS, DROP POLICY IF EXISTS + CREATE, REVOKE /
-- GRANT / COMMENT are re-runnable. Safe to run twice.
-- LOCKS: the FKs take a SHARE ROW EXCLUSIVE lock on workspace_items and users
-- for the length of the transaction; lock_timeout fails fast instead of queueing
-- every turn behind it.
-- ROLLBACK: DROP TABLE public.helper_runs;  (nothing else references it)
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.helper_runs (
    item_id     uuid        NOT NULL REFERENCES public.workspace_items(item_id) ON DELETE CASCADE,
    helper      text        NOT NULL,
    user_id     uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    status      text        NOT NULL,
    attempt     integer     NOT NULL DEFAULT 1,
    lease_until timestamptz,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    error       text,
    version     text,
    CONSTRAINT helper_runs_pkey PRIMARY KEY (item_id, helper),
    CONSTRAINT helper_runs_status_chk  CHECK (status IN ('running', 'done', 'failed')),
    CONSTRAINT helper_runs_attempt_chk CHECK (attempt >= 1),
    CONSTRAINT helper_runs_lease_chk   CHECK (status <> 'running' OR lease_until IS NOT NULL),
    CONSTRAINT helper_runs_helper_chk  CHECK (char_length(helper) BETWEEN 1 AND 64),
    CONSTRAINT helper_runs_error_chk   CHECK (error IS NULL OR char_length(error) <= 500)
);

-- The user FK cascade on account deletion, and the policy's filter.
CREATE INDEX IF NOT EXISTS helper_runs_user_id_idx ON public.helper_runs (user_id);

ALTER TABLE public.helper_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS helper_runs_select_own ON public.helper_runs;
CREATE POLICY helper_runs_select_own ON public.helper_runs
    FOR SELECT
    TO authenticated
    USING (user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid())));

REVOKE ALL ON TABLE public.helper_runs FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.helper_runs TO service_role;

COMMENT ON TABLE public.helper_runs IS
    'Helper frame (216): status + lease per (workspace item, helper) for new item helpers such as the segmenter. Service-role only: RLS on, owner SELECT policy, no client grants. Rows cascade with the item.';
COMMENT ON COLUMN public.helper_runs.attempt IS
    'Runs under the current version; the compare-and-set token of every claim / renew / finish.';
COMMENT ON COLUMN public.helper_runs.lease_until IS
    'While running: the run is presumed dead after this. Renewed every third of the lease; NULL once the run ends.';

-- ── self-tests ───────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_left int;
    v_svc  int;
BEGIN
    IF NOT (SELECT c.relrowsecurity FROM pg_class c
             WHERE c.oid = 'public.helper_runs'::regclass) THEN
        RAISE EXCEPTION '216: RLS is off on helper_runs';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policy p
                    WHERE p.polrelid = 'public.helper_runs'::regclass
                      AND p.polname = 'helper_runs_select_own'
                      AND p.polcmd = 'r') THEN
        RAISE EXCEPTION '216: owner SELECT policy missing on helper_runs';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_policy p
                WHERE p.polrelid = 'public.helper_runs'::regclass
                  AND p.polcmd <> 'r') THEN
        RAISE EXCEPTION '216: helper_runs must have no client write policy';
    END IF;

    SELECT count(*) INTO v_left
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name = 'helper_runs'
       AND grantee IN ('anon', 'authenticated', 'PUBLIC');
    IF v_left <> 0 THEN
        RAISE EXCEPTION '216: % client-role privileges on helper_runs', v_left;
    END IF;

    SELECT count(DISTINCT privilege_type) INTO v_svc
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND table_name = 'helper_runs'
       AND grantee = 'service_role'
       AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE');
    IF v_svc <> 4 THEN
        RAISE EXCEPTION '216: service_role must hold DML on helper_runs (found % of 4)', v_svc;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                    WHERE c.conrelid = 'public.helper_runs'::regclass
                      AND c.contype = 'f'
                      AND c.confrelid = 'public.workspace_items'::regclass
                      AND c.confdeltype = 'c') THEN
        RAISE EXCEPTION '216: helper_runs.item_id must cascade with workspace_items';
    END IF;

    RAISE NOTICE '216 OK: helper_runs created, RLS on, owner SELECT policy, client roles revoked, service_role intact';
END $$;

COMMIT;
