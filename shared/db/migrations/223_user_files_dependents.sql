-- ============================================================================
-- Migration 223 — pages, segment maps and helper runs hang on the FILE.
--
-- Source: .claude/plans/my_files_phase2.md §B (223), §G step 6. Last Phase-2
--         step and a Phase-3 prerequisite («الملفات»: a file outlives its
--         conversation items).
--
-- WHAT IT DOES ────────────────────────────────────────────────────────────────
--   Re-points three foreign keys, keeping their names and the column name
--   item_id (no data rewrite: file_id = item_id for every file made so far):
--     attachment_pages.item_id        → user_files(file_id) ON DELETE CASCADE
--     attachment_segment_maps.item_id → user_files(file_id) ON DELETE CASCADE
--     helper_runs.item_id             → user_files(file_id) ON DELETE CASCADE
--   attachment_segments keeps its FK to the map (cascades from it).
--   message_attachments stays on workspace_items: a message links to the
--   conversation's item.
--   Adds CHECK workspace_items_attachment_file_chk (kind <> 'attachment' OR
--   file_id IS NOT NULL): every attachment pointer has a file (the 222 link
--   trigger guarantees it for new rows).
--
-- LIFECYCLE AFTER 223 ──────────────────────────────────────────────────────────
--   * Hard delete of a POINTER no longer removes pages / maps / helper runs;
--     they die with the file (purge_user_data deletes files after pointers,
--     and users → user_files cascades).
--   * helper_runs becomes file-only: all rows are the segmenter today; a future
--     helper on a non-file item needs its own key.
--
-- VERIFIED: 222's self-check proved every page / map / helper row has a file
--   under the same id; the preflight below re-checks it.
-- TESTED LOCALLY 2026-11-19 on PostgreSQL 17.6 (scratchpad harness
--   phase2_dbtest): applied twice, cascades from user_files proven, rollback
--   restores the 215 / 216 definitions exactly.
--
-- PREFLIGHT (read-only; expect 0):
--   SELECT (SELECT count(*) FROM public.attachment_pages p WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = p.item_id))
--        + (SELECT count(*) FROM public.attachment_segment_maps m WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = m.item_id))
--        + (SELECT count(*) FROM public.helper_runs h WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = h.item_id));
--
-- Dependencies: 221, 222.
-- DEPLOY ORDER: after both USER_FILES_* flags are on for everyone and soaked.
--   No code depends on it (ids are equal).
-- LOCKS: Tx 1 (≈ 10-16 ms measured locally): dropping the three FKs and adding the CHECK take
--   ACCESS EXCLUSIVE on attachment_pages, attachment_segment_maps, helper_runs
--   and workspace_items (readers and writers wait); adding the new FKs takes
--   SHARE ROW EXCLUSIVE on user_files (file writers wait). The new FKs are
--   validated here (805 + 80 + 80 rows). Tx 2 validates the CHECK under SHARE
--   UPDATE EXCLUSIVE: nobody waits. lock_timeout 5s on both.
-- IDEMPOTENT: each FK is re-pointed only while it still references
--   workspace_items; the CHECK add has a duplicate_object guard; Tx 2 validates
--   only when needed. Safe to run twice.
-- DRY RUN: the same file with both COMMITs replaced by ROLLBACK. Tx 1 runs its
--   own self-check before its COMMIT; Tx 2 then finds no CHECK and says so.
-- ROLLBACK (only while every page / map / helper row has a same-id pointer —
-- i.e. before Phase 3 creates library uploads; the guard refuses otherwise):
--   BEGIN;
--   SET LOCAL lock_timeout = '5s';
--   DO $$ BEGIN
--       IF (SELECT count(*) FROM public.attachment_pages p WHERE NOT EXISTS (SELECT 1 FROM public.workspace_items w WHERE w.item_id = p.item_id))
--        + (SELECT count(*) FROM public.attachment_segment_maps m WHERE NOT EXISTS (SELECT 1 FROM public.workspace_items w WHERE w.item_id = m.item_id))
--        + (SELECT count(*) FROM public.helper_runs h WHERE NOT EXISTS (SELECT 1 FROM public.workspace_items w WHERE w.item_id = h.item_id)) <> 0 THEN
--           RAISE EXCEPTION '223 rollback: some page / map / helper rows have no same-id workspace item';
--       END IF;
--   END $$;
--   ALTER TABLE public.attachment_pages DROP CONSTRAINT IF EXISTS attachment_pages_item_id_fkey,
--       ADD CONSTRAINT attachment_pages_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.workspace_items(item_id) ON DELETE CASCADE;
--   ALTER TABLE public.attachment_segment_maps DROP CONSTRAINT IF EXISTS attachment_segment_maps_item_id_fkey,
--       ADD CONSTRAINT attachment_segment_maps_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.workspace_items(item_id) ON DELETE CASCADE;
--   ALTER TABLE public.helper_runs DROP CONSTRAINT IF EXISTS helper_runs_item_id_fkey,
--       ADD CONSTRAINT helper_runs_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.workspace_items(item_id) ON DELETE CASCADE;
--   ALTER TABLE public.workspace_items DROP CONSTRAINT IF EXISTS workspace_items_attachment_file_chk;
--   COMMENT ON COLUMN public.attachment_pages.item_id IS NULL;
--   COMMENT ON COLUMN public.attachment_segment_maps.item_id IS NULL;
--   COMMENT ON COLUMN public.helper_runs.item_id IS NULL;
--   COMMIT;
--   NOTIFY pgrst, 'reload schema';
-- ============================================================================

-- ══ Tx 1: re-point the FKs (validated), add the CHECK (NOT VALID) ═══════════
BEGIN;
SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    v_n bigint;
    r   record;
BEGIN
    SELECT (SELECT count(*) FROM public.attachment_pages p
             WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = p.item_id))
         + (SELECT count(*) FROM public.attachment_segment_maps m
             WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = m.item_id))
         + (SELECT count(*) FROM public.helper_runs h
             WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = h.item_id))
      INTO v_n;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '223: % page / map / helper rows have no file — run 222 first', v_n;
    END IF;

    FOR r IN SELECT * FROM (VALUES ('attachment_pages',        'attachment_pages_item_id_fkey'),
                                   ('attachment_segment_maps', 'attachment_segment_maps_item_id_fkey'),
                                   ('helper_runs',             'helper_runs_item_id_fkey')) AS t (tbl, con)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                        WHERE c.conrelid = ('public.' || r.tbl)::regclass AND c.conname = r.con
                          AND c.confrelid = 'public.user_files'::regclass) THEN
            EXECUTE format(
                'ALTER TABLE public.%I DROP CONSTRAINT IF EXISTS %I, '
                'ADD CONSTRAINT %I FOREIGN KEY (item_id) REFERENCES public.user_files(file_id) ON DELETE CASCADE',
                r.tbl, r.con, r.con);
        END IF;
    END LOOP;
END $$;

DO $$
BEGIN
    ALTER TABLE public.workspace_items
        ADD CONSTRAINT workspace_items_attachment_file_chk
        CHECK (kind <> 'attachment' OR file_id IS NOT NULL) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN public.attachment_pages.item_id        IS 'user_files.file_id (223). Name kept from 215.';
COMMENT ON COLUMN public.attachment_segment_maps.item_id IS 'user_files.file_id (223). Name kept from 215.';
COMMENT ON COLUMN public.helper_runs.item_id             IS 'user_files.file_id (223). Name kept from 216.';

-- self-check (before COMMIT, so a dry run proves it too)
DO $$
DECLARE v_n int;
BEGIN
    SELECT count(*) INTO v_n FROM pg_constraint
     WHERE contype = 'f' AND confdeltype = 'c' AND convalidated
       AND confrelid = 'public.user_files'::regclass
       AND (conrelid, conname) IN (('public.attachment_pages'::regclass,        'attachment_pages_item_id_fkey'),
                                   ('public.attachment_segment_maps'::regclass, 'attachment_segment_maps_item_id_fkey'),
                                   ('public.helper_runs'::regclass,             'helper_runs_item_id_fkey'));
    IF v_n <> 3 THEN
        RAISE EXCEPTION '223: expected 3 validated cascading FKs onto user_files, found %', v_n;
    END IF;
    SELECT count(*) INTO v_n FROM pg_constraint
     WHERE contype = 'f' AND confrelid = 'public.workspace_items'::regclass
       AND conrelid IN ('public.attachment_pages'::regclass, 'public.attachment_segment_maps'::regclass,
                        'public.helper_runs'::regclass);
    IF v_n <> 0 THEN
        RAISE EXCEPTION '223: % FK(s) from pages / maps / helper runs still reference workspace_items', v_n;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE contype = 'f' AND confdeltype = 'c'
                    AND conrelid = 'public.attachment_segments'::regclass
                    AND confrelid = 'public.attachment_segment_maps'::regclass) THEN
        RAISE EXCEPTION '223: attachment_segments lost its cascade from the map';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.workspace_items'::regclass
                    AND conname = 'workspace_items_attachment_file_chk') THEN
        RAISE EXCEPTION '223: workspace_items_attachment_file_chk missing';
    END IF;
    RAISE NOTICE '223 OK (Tx 1): pages, maps, helper runs cascade from user_files; attachment CHECK added';
END $$;

COMMIT;

-- ══ Tx 2: validate the CHECK (SHARE UPDATE EXCLUSIVE: nobody waits) ════════
BEGIN;
SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.workspace_items'::regclass
                    AND conname = 'workspace_items_attachment_file_chk') THEN
        RAISE NOTICE '223 (Tx 2): workspace_items_attachment_file_chk not present (dry run?) — nothing to validate';
    ELSIF NOT (SELECT convalidated FROM pg_constraint WHERE conrelid = 'public.workspace_items'::regclass
                AND conname = 'workspace_items_attachment_file_chk') THEN
        ALTER TABLE public.workspace_items VALIDATE CONSTRAINT workspace_items_attachment_file_chk;
        RAISE NOTICE '223 OK (Tx 2): every attachment pointer has a file (CHECK validated)';
    ELSE
        RAISE NOTICE '223 OK (Tx 2): CHECK already validated';
    END IF;
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
