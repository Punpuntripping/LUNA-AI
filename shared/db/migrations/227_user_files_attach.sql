-- ============================================================================
-- Migration 227 — one file, many conversations («الملفات» Phase 3: attach a
-- library file to a conversation).
--
-- Source: .claude/plans/my_files.md §3.5 / §5 (Phase 3), and the Phase-2 note in
--         .claude/plans/my_files_phase2.md: "the item→file mirror must change
--         before Phase 3 creates second items".
--
-- WHAT IT DOES ────────────────────────────────────────────────────────────────
--   1. public.user_file_attach(p_user_id, p_conversation_id, p_file_id)
--      Puts one of the user's library files into a conversation: a new
--      attachment item (a pointer) whose title / text / summary / metadata /
--      storage path are copied from the file INSIDE the database (the text never
--      travels through the API). Nothing is extracted, summarised or segmented
--      again: the copied summary keeps summarize_artifact_on_insert quiet, the
--      copied metadata carries ocr_status, and the agents find the file's pages
--      and map by file_id.
--      * Idempotent per conversation: a live item of the same file already in
--        that conversation is returned instead of a second one.
--      * Refuses (SQLSTATE P0002, no row written) a conversation that is not the
--        user's or is deleted, and a file that is not the user's, is deleted, is
--        hidden from the library, or has no extracted text.
--      * Returns the item row (PostgREST: one JSON object).
--   2. A file's life follows ALL of its items, not only the first one.
--      Until now the home item's deleted_at was mirrored onto the file
--      (trg_workspace_items_mirror_file, migration 222). With a second item that
--      would remove the file from every conversation when its first copy is
--      deleted. Now:
--      * soft-deleting an item deletes its file only when no other live item of
--        that file is left (one item — today's case — behaves exactly as before);
--      * restoring an item restores its file.
--      The home mirror keeps every other field (title, text, summary, metadata,
--      storage path, document id) — it only stops copying deleted_at.
--
-- Dependencies: 221, 222 (user_files, file_id, the sync triggers), 223.
-- DEPLOY ORDER: before the code that calls user_file_attach (the route answers
--   503 «الميزة غير متاحة» while the function is missing). Harmless before it.
-- LOCKS: CREATE OR REPLACE FUNCTION + CREATE TRIGGER on workspace_items
--   (SHARE ROW EXCLUSIVE, milliseconds). lock_timeout 5s.
-- IDEMPOTENT: CREATE OR REPLACE everywhere. Safe to run twice.
-- DRY RUN: the same file with COMMIT replaced by ROLLBACK; the self-check runs
--   before the COMMIT.
-- ROLLBACK (puts 222's mirror back; leaves second items in place — run only
--   while none exist, i.e. before the route ships, or accept that deleting a
--   first copy then removes the file everywhere):
--   BEGIN;
--   SET LOCAL lock_timeout = '5s';
--   DROP TRIGGER IF EXISTS trg_workspace_items_file_liveness ON public.workspace_items;
--   DROP FUNCTION IF EXISTS public.workspace_items_file_liveness();
--   DROP FUNCTION IF EXISTS public.user_file_attach(uuid, uuid, uuid);
--   -- then re-run section (2) of 222 (CREATE OR REPLACE FUNCTION
--   -- public.workspace_items_mirror_file …, with deleted_at in SET and compare).
--   COMMIT;
--   NOTIFY pgrst, 'reload schema';
-- ============================================================================

BEGIN;
SET LOCAL lock_timeout = '5s';

-- ── (2a) the home mirror: every field but deleted_at ──────────────────────────
CREATE OR REPLACE FUNCTION public.workspace_items_mirror_file()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF current_setting('luna.user_files_sync', true) = 'on' THEN
        RETURN NULL;
    END IF;
    UPDATE public.user_files f SET
           title                 = NEW.title,
           content_md            = CASE WHEN f.content_md IS DISTINCT FROM COALESCE(NEW.content_md, '')
                                        THEN COALESCE(NEW.content_md, '') ELSE f.content_md END,
           summary               = NEW.summary,
           summary_source_length = NEW.summary_source_length,
           summary_updated_at    = NEW.summary_updated_at,
           metadata              = COALESCE(NEW.metadata, '{}'::jsonb),
           storage_path          = NEW.storage_path,
           document_id           = NEW.document_id,
           library_hidden_at     = COALESCE(f.library_hidden_at,
                                            public.user_files_parse_ts(NEW.metadata->>'library_hidden_at'))
     WHERE f.file_id = NEW.file_id
       AND (f.title, f.content_md, f.summary, f.summary_source_length, f.summary_updated_at, f.metadata,
            f.storage_path, f.document_id)
           IS DISTINCT FROM
           (NEW.title, COALESCE(NEW.content_md, ''), NEW.summary, NEW.summary_source_length,
            NEW.summary_updated_at, COALESCE(NEW.metadata, '{}'::jsonb), NEW.storage_path,
            NEW.document_id);
    RETURN NULL;
END $$;

-- ── (2b) a file lives while any of its items lives ───────────────────────────
-- Any item (not only the home one). Ignores the file→items sync's own writes
-- (it never writes deleted_at anyway).
CREATE OR REPLACE FUNCTION public.workspace_items_file_liveness()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF current_setting('luna.user_files_sync', true) = 'on' THEN
        RETURN NULL;
    END IF;
    IF NEW.deleted_at IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM public.workspace_items w
                        WHERE w.file_id = NEW.file_id
                          AND w.kind = 'attachment'
                          AND w.deleted_at IS NULL
                          AND w.item_id <> NEW.item_id) THEN
            UPDATE public.user_files SET deleted_at = NEW.deleted_at
             WHERE file_id = NEW.file_id AND deleted_at IS NULL;
        END IF;
    ELSE
        UPDATE public.user_files SET deleted_at = NULL
         WHERE file_id = NEW.file_id AND deleted_at IS NOT NULL;
    END IF;
    RETURN NULL;
END $$;
CREATE OR REPLACE TRIGGER trg_workspace_items_file_liveness
    AFTER UPDATE OF deleted_at ON public.workspace_items
    FOR EACH ROW WHEN (NEW.kind = 'attachment' AND NEW.file_id IS NOT NULL
                       AND OLD.deleted_at IS DISTINCT FROM NEW.deleted_at)
    EXECUTE FUNCTION public.workspace_items_file_liveness();

-- ── (1) attach a library file to a conversation ─────────────────────────────
CREATE OR REPLACE FUNCTION public.user_file_attach(p_user_id uuid, p_conversation_id uuid, p_file_id uuid)
RETURNS public.workspace_items LANGUAGE plpgsql SET search_path = public AS $$
DECLARE
    v_case uuid;
    v_row  public.workspace_items;
BEGIN
    IF p_user_id IS NULL OR p_conversation_id IS NULL OR p_file_id IS NULL THEN
        RAISE EXCEPTION 'user_file_attach: missing argument' USING ERRCODE = '22004';
    END IF;

    SELECT c.case_id INTO v_case
      FROM public.conversations c
     WHERE c.conversation_id = p_conversation_id
       AND c.user_id = p_user_id
       AND c.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user_file_attach: conversation not found' USING ERRCODE = 'P0002';
    END IF;

    SELECT * INTO v_row
      FROM public.workspace_items w
     WHERE w.file_id = p_file_id
       AND w.conversation_id = p_conversation_id
       AND w.user_id = p_user_id
       AND w.kind = 'attachment'
       AND w.deleted_at IS NULL
     ORDER BY w.created_at, w.item_id
     LIMIT 1;
    IF FOUND THEN
        RETURN v_row;
    END IF;

    INSERT INTO public.workspace_items
        (user_id, conversation_id, case_id, kind, created_by, title, content_md, metadata,
         storage_path, document_id, summary, summary_source_length, summary_updated_at, file_id)
    SELECT p_user_id, p_conversation_id, v_case, 'attachment', 'user', f.title, f.content_md, f.metadata,
           f.storage_path, f.document_id, f.summary, f.summary_source_length, f.summary_updated_at, f.file_id
      FROM public.user_files f
     WHERE f.file_id = p_file_id
       AND f.user_id = p_user_id
       AND f.deleted_at IS NULL
       AND f.library_hidden_at IS NULL
       AND f.content_chars > 0
    RETURNING * INTO v_row;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'user_file_attach: file not attachable' USING ERRCODE = 'P0002';
    END IF;
    RETURN v_row;
END $$;

REVOKE ALL ON FUNCTION public.user_file_attach(uuid, uuid, uuid)   FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.workspace_items_file_liveness()      FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.workspace_items_mirror_file()        FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.user_file_attach(uuid, uuid, uuid) TO service_role;

COMMENT ON FUNCTION public.user_file_attach(uuid, uuid, uuid) IS
    '227: attach a library file to a conversation (new pointer, fields copied in SQL; idempotent per conversation). P0002 when not allowed.';

-- self-check (before COMMIT, so a dry run proves it too)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_workspace_items_file_liveness'
                    AND tgrelid = 'public.workspace_items'::regclass AND NOT tgisinternal) THEN
        RAISE EXCEPTION '227: liveness trigger missing';
    END IF;
    IF position('deleted_at' IN (SELECT prosrc FROM pg_proc WHERE oid = 'public.workspace_items_mirror_file()'::regprocedure)) > 0 THEN
        RAISE EXCEPTION '227: the home mirror still writes deleted_at';
    END IF;
    IF to_regprocedure('public.user_file_attach(uuid, uuid, uuid)') IS NULL THEN
        RAISE EXCEPTION '227: user_file_attach missing';
    END IF;
    IF has_function_privilege('authenticated', 'public.user_file_attach(uuid, uuid, uuid)', 'EXECUTE')
       OR has_function_privilege('anon', 'public.user_file_attach(uuid, uuid, uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION '227: user_file_attach is executable by a client role';
    END IF;
    RAISE NOTICE '227 OK: user_file_attach in place; a file now lives while any of its items lives';
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
