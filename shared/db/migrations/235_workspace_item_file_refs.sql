-- ============================================================================
-- Migration 235 — workspace_item_file_refs: which deep-search report links to
--                 which part of the user's OWN file («أقسام من مستنداتك»).
--
-- Source: .claude/plans/aggregator_file_refs.md §4.5 — owner decisions D6 (a new
--         table), D7 (store WHERE the section sits in the file's text, not a
--         copy), D10 ("it's important to see who links to what … a table that
--         inspires from workspace_item_references"), D11 (the tag names the
--         file by its fixed WI number in the conversation) and D12 (written as
--         F + that number: [F3·S7] / [F3]), 2026-11-27.
--         Code: backend/app/services/file_refs_service.py (write + read),
--               agents/agent_search/publisher.py (writes beside the library refs),
--               shared/workspace/file_refs.py (the [F3·S7] grammar).
--
-- WHAT IT STORES ─────────────────────────────────────────────────────────────
--   One row per [F3·S7] / [F3] tag of a report that resolved to a part of the
--   user's file the aggregator was shown. Modelled on workspace_item_references
--   (049/050): ref_pk, wi_id (the report — who links), n (order of first mention
--   = the «من مستنداتك» list order), ref_id (the canonical tag, 'F3.S7' | 'F3';
--   3 = wi_seq), item_id (the attachment — what it links to) — plus wi_seq (the
--   attachment's WI number in the conversation) and section_seq (NULL = the whole
--   document). The index on item_id answers "which reports cite this file".
--   char_start / char_end are offsets into the attachment's text (the same offsets
--   attachment_segments holds): nothing removes that text while the report exists,
--   so the popup can always cut the section back out (D7). doc_title / what /
--   owner / brought_by / pages are written from the STORED real rows, never from
--   the aggregator's masked copies.
--
--   workspace_item_references stays library-only (its domain CHECK lists the 7
--   library corpora; its n is the [n] citation number). A file ref is never a
--   library ref.
--
-- ACCESS ──────────────────────────────────────────────────────────────────────
--   RLS ON + the four owner policies of 049 (owner through the REPORT's
--   workspace_items.user_id), as they stand live (checked 2026-11-27). Then
--   REVOKE ALL from anon / authenticated — the 195 stance: the backend reads and
--   writes as service_role, the browser never touches the table; the policies
--   are inert without a grant and restore exactly the owner scoping if one is
--   ever given.
--
-- DELETES ─────────────────────────────────────────────────────────────────────
--   Both FKs CASCADE: a report or an attachment hard-deleted (account purge)
--   takes its rows. Soft deletes (deleted_at) keep them; the route answers 410
--   for a deleted attachment.
--
-- IDEMPOTENT: IF NOT EXISTS / duplicate_object guards; REVOKE, GRANT and COMMENT
-- are re-runnable. Self-test at the end.
-- ROLLBACK: DROP TABLE public.workspace_item_file_refs; (nothing else depends on it)
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS public.workspace_item_file_refs (
    ref_pk          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    wi_id           uuid NOT NULL REFERENCES public.workspace_items(item_id) ON DELETE CASCADE,
    n               integer NOT NULL CHECK (n > 0),
    ref_id          text NOT NULL CHECK (ref_id ~ '^F[1-9][0-9]*(\.S[1-9][0-9]*)?$'),
    item_id         uuid NOT NULL REFERENCES public.workspace_items(item_id) ON DELETE CASCADE,
    wi_seq          integer NOT NULL CHECK (wi_seq > 0),
    section_seq     integer CHECK (section_seq IS NULL OR section_seq > 0),
    char_start      integer,
    char_end        integer,
    page_from       integer,
    page_to         integer,
    doc_title       text NOT NULL DEFAULT '',
    what            text NOT NULL DEFAULT '',
    owner           text NOT NULL DEFAULT '',
    brought_by      text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT workspace_item_file_refs_wi_id_n_key UNIQUE (wi_id, n),
    CONSTRAINT workspace_item_file_refs_wi_id_ref_id_key UNIQUE (wi_id, ref_id)
);

-- The list route reads by report in n order (UNIQUE (wi_id, n) covers it; UNIQUE
-- (wi_id, ref_id) covers the section route). "Which reports cite this file" and
-- the cascade from an attachment need an index on item_id.
CREATE INDEX IF NOT EXISTS idx_workspace_item_file_refs_item
    ON public.workspace_item_file_refs (item_id);

COMMENT ON TABLE public.workspace_item_file_refs IS
    'Which deep-search report (wi_id) links to which part of the user''s own file '
    '(item_id, section_seq) — the [F3·S7] / [F3] tags of the report '
    '(aggregator_file_refs.md, migration 235). Modelled on workspace_item_references; '
    'positions into the file''s text, not a copy. Library refs stay in '
    'workspace_item_references.';
COMMENT ON COLUMN public.workspace_item_file_refs.wi_id IS
    'The report (workspace_items.item_id of the agent_search item) — who links.';
COMMENT ON COLUMN public.workspace_item_file_refs.n IS
    'Order of first mention in the report (1-based) — the «من مستنداتك» list order.';
COMMENT ON COLUMN public.workspace_item_file_refs.ref_id IS
    'Canonical tag as the answer cites it: F3.S7 (section 7 of the conversation''s WI-3) or F3 (the whole document); the number is wi_seq.';
COMMENT ON COLUMN public.workspace_item_file_refs.item_id IS
    'The attachment (workspace_items.item_id of the conversation''s copy) — what is linked to.';
COMMENT ON COLUMN public.workspace_item_file_refs.wi_seq IS
    'The attachment''s WI number in the conversation (workspace_items.wi_seq) as the tag names it.';
COMMENT ON COLUMN public.workspace_item_file_refs.section_seq IS
    'attachment_segments.seq when cited (the «الخيوط» section number); NULL = the whole document.';
COMMENT ON COLUMN public.workspace_item_file_refs.char_start IS
    'Where the section starts in the attachment''s text (NULL = whole document or no position).';

-- ── RLS — the four owner policies of 049, owner through the REPORT ──────────
ALTER TABLE public.workspace_item_file_refs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    CREATE POLICY workspace_item_file_refs_select ON public.workspace_item_file_refs
        FOR SELECT
        USING (EXISTS (
            SELECT 1 FROM public.workspace_items wi
            WHERE wi.item_id = workspace_item_file_refs.wi_id
              AND wi.user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY workspace_item_file_refs_insert ON public.workspace_item_file_refs
        FOR INSERT
        WITH CHECK (EXISTS (
            SELECT 1 FROM public.workspace_items wi
            WHERE wi.item_id = workspace_item_file_refs.wi_id
              AND wi.user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY workspace_item_file_refs_update ON public.workspace_item_file_refs
        FOR UPDATE
        USING (EXISTS (
            SELECT 1 FROM public.workspace_items wi
            WHERE wi.item_id = workspace_item_file_refs.wi_id
              AND wi.user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY workspace_item_file_refs_delete ON public.workspace_item_file_refs
        FOR DELETE
        USING (EXISTS (
            SELECT 1 FROM public.workspace_items wi
            WHERE wi.item_id = workspace_item_file_refs.wi_id
              AND wi.user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── Grants — the 195 stance ─────────────────────────────────────────────────
REVOKE ALL ON TABLE public.workspace_item_file_refs FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.workspace_item_file_refs TO service_role;

-- ── self-test ───────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_rls      boolean;
    v_policies int;
    v_client   int;
    v_svc      int;
BEGIN
    SELECT c.relrowsecurity INTO v_rls
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relname = 'workspace_item_file_refs';
    IF NOT coalesce(v_rls, false) THEN
        RAISE EXCEPTION '235: RLS must be enabled on workspace_item_file_refs';
    END IF;

    SELECT count(*) INTO v_policies
      FROM pg_policies
     WHERE schemaname = 'public' AND tablename = 'workspace_item_file_refs';
    IF v_policies <> 4 THEN
        RAISE EXCEPTION '235: expected 4 owner policies, found %', v_policies;
    END IF;

    SELECT count(*) INTO v_client
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'workspace_item_file_refs'
       AND grantee IN ('anon', 'authenticated', 'PUBLIC');
    IF v_client <> 0 THEN
        RAISE EXCEPTION '235: % client-role privileges remain', v_client;
    END IF;

    SELECT count(DISTINCT privilege_type) INTO v_svc
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'workspace_item_file_refs'
       AND grantee = 'service_role'
       AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE');
    IF v_svc <> 4 THEN
        RAISE EXCEPTION '235: service_role must hold SELECT/INSERT/UPDATE/DELETE (found %)', v_svc;
    END IF;

    RAISE NOTICE '235 OK: table, RLS + 4 owner policies, client roles revoked, service_role DML';
END $$;

COMMIT;
