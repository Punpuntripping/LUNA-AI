-- ============================================================================
-- Migration 220 — user_files: one row per uploaded file per user (INERT).
--
-- Source: .claude/plans/my_files_phase2.md §B (220); parent plan
--         .claude/plans/my_files.md, owner decisions D14 (every attachment
--         migrates, per-user count check) and D15 (thin pointer). Phase 2 of
--         «الملفات». Nothing reads or writes this table until 221 links it
--         and the USER_FILES_* flags are turned on.
--
-- WHAT IT STORES ─────────────────────────────────────────────────────────────
--   One row per uploaded file: extracted text (content_md), summary and its two
--   bookkeeping columns, the COMPLETE metadata blob (byte-equal to the
--   attachment row's today), storage_path (NULL once the original is purged),
--   document_id, origin ('chat' | 'library'), library_hidden_at («حذف من
--   الملفات», NULL = listed), created / updated / deleted stamps.
--   A conversation's use of a file stays a workspace_items row (kind =
--   attachment) that points here through workspace_items.file_id (221).
--   Text is stored REAL (store-real invariant, وضع السرية); consumers encode
--   what they load.
--
--   Derived columns are GENERATED ALWAYS … STORED (nobody writes them, they
--   cannot drift): content_chars, word_count, filename, mime_type,
--   file_size_bytes (NULL unless metadata holds 1–18 plain digits), doc_type,
--   ocr_status, upload_status.
--   COST NOTE (measured on PG 17.6, 2026-11-19): because this table has BEFORE
--   UPDATE triggers, Postgres recomputes EVERY stored generated column on EVERY
--   UPDATE, metadata-only ones included. word_count = compute_word_count()
--   costs ≈ 0.08 ms per KB of text (≈ 200 ms for a 2.7 MB / ~600-page file);
--   typical files (tens of KB) cost < 2 ms per write.
--
--   Functions (service_role only):
--     user_file_patch(file_id, fields, metadata_patch, user_id)  the one write
--       path for application code. metadata is MERGED (||) in the statement,
--       never read-modify-written. fields accepts only title, content_md,
--       summary, summary_source_length, summary_updated_at, storage_path,
--       deleted_at — any other key raises 22023 (a silently dropped write
--       would be drift). Returns the file_id, or NULL when no row matched
--       (wrong id, or p_user_id given and not the owner).
--     user_files_hide(user_id, file_ids[])  «حذف من الملفات» in one
--       statement; the first stamp is kept; mirrors the stamp into
--       metadata.library_hidden_at for the pre-switch reader.
--     user_files_parse_ts(text)  ISO-ish text → timestamptz or NULL (never
--       raises; 'now' / 'yesterday' / 'infinity' are NOT accepted).
--
-- LIFECYCLE ───────────────────────────────────────────────────────────────────
--   * New text ⇒ summary, summary_source_length, summary_updated_at cleared
--     (trg_user_files_invalidate_summary — same rule as workspace_items, 030).
--   * updated_at moves on every UPDATE (trg_user_files_updated_at).
--   * user_id → users ON DELETE CASCADE (the terminal auth-user delete);
--     purge_user_data deletes files explicitly from 221 on.
--
-- SECURITY / RLS ──────────────────────────────────────────────────────────────
--   RLS ENABLED (absolute rule 6), one owner-scoped SELECT policy, no client
--   write policy, REVOKE ALL from anon / authenticated (the 195 stance): the
--   backend reads and writes as service_role and the browser never touches it.
--   EXECUTE on every new function is revoked from PUBLIC / anon /
--   authenticated (Supabase's default privileges grant it to them) and granted
--   to service_role (the parse helper is also called from 221's triggers, which
--   run as service_role).
--
-- VERIFIED LIVE (catalog-only, prod dwgghvxogtwyaxmbgjod, 2026-11-19, by the
-- spec pass — re-run the preflight below before applying):
--   * Postgres 17.6; postgres owns the tables and is not superuser.
--   * no relation named user_files; no function named user_file_patch /
--     user_files_hide / user_files_parse_ts / user_files_invalidate_summary.
--   * compute_word_count(text) is IMMUTABLE (048); update_updated_at() exists.
--   * metadata of all 875 attachment rows: no library_hidden_at key yet.
-- TESTED LOCALLY 2026-11-19 on PostgreSQL 17.6 with a catalog extract of prod
-- and invented prod-shaped data (harness: scratchpad phase2_dbtest/README.md).
--
-- PREFLIGHT (read-only):
--   SELECT to_regclass('public.user_files'),
--          to_regprocedure('public.user_files_parse_ts(text)'),
--          to_regprocedure('public.user_file_patch(uuid,jsonb,jsonb,uuid)');  -- all NULL
--
-- Dependencies: users, case_documents, compute_word_count (048), update_updated_at.
-- DEPLOY ORDER: any time, before 221. No code touches the table until 221 and a flag.
-- LOCKS: a new table; its two FKs take SHARE ROW EXCLUSIVE on users and
--   case_documents until COMMIT (writes to those two wait milliseconds; reads
--   never wait). lock_timeout 5s fails fast instead of queueing.
-- IDEMPOTENT: CREATE TABLE / INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION /
--   TRIGGER, DROP POLICY IF EXISTS + CREATE POLICY; REVOKE / GRANT / COMMENT
--   re-run cleanly. Safe to run twice.
-- DRY RUN: the same file with the final COMMIT replaced by ROLLBACK.
-- ROLLBACK (221 must be rolled back first; the guard refuses otherwise):
--   BEGIN;
--   SET LOCAL lock_timeout = '5s';
--   DO $$ BEGIN
--       IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'public.workspace_items'::regclass
--                   AND attname = 'file_id' AND NOT attisdropped) THEN
--           RAISE EXCEPTION '220 rollback: roll back 221 first (workspace_items.file_id exists)';
--       END IF;
--   END $$;
--   DROP TABLE IF EXISTS public.user_files;          -- no CASCADE on purpose
--   DROP FUNCTION IF EXISTS public.user_file_patch(uuid, jsonb, jsonb, uuid),
--                           public.user_files_hide(uuid, uuid[]),
--                           public.user_files_invalidate_summary(),
--                           public.user_files_parse_ts(text);
--   COMMIT;
--   NOTIFY pgrst, 'reload schema';
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.user_files (
    file_id               uuid        NOT NULL DEFAULT gen_random_uuid(),
    user_id               uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    title                 text        NOT NULL,
    content_md            text        NOT NULL DEFAULT '',            -- extracted text, stored REAL
    summary               text,
    summary_source_length integer,
    summary_updated_at    timestamptz,
    metadata              jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- the COMPLETE blob, as on workspace_items today
    storage_path          text,                                       -- NULL once the original is purged
    document_id           uuid        REFERENCES public.case_documents(document_id),
    origin                text        NOT NULL DEFAULT 'chat',        -- 'library' = uploaded into «الملفات» (Phase 3)
    library_hidden_at     timestamptz,                                -- «حذف من الملفات»; NULL = listed
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz,
    -- Derived, never written: one source each, so they cannot drift.
    content_chars   integer GENERATED ALWAYS AS (char_length(content_md)) STORED,
    word_count      integer GENERATED ALWAYS AS (public.compute_word_count(content_md)) STORED,
    filename        text    GENERATED ALWAYS AS (metadata->>'filename') STORED,
    mime_type       text    GENERATED ALWAYS AS (metadata->>'mime_type') STORED,
    file_size_bytes bigint  GENERATED ALWAYS AS (
        CASE WHEN (metadata->>'file_size_bytes') ~ '^[0-9]{1,18}$'
             THEN (metadata->>'file_size_bytes')::bigint END) STORED,
    doc_type        text    GENERATED ALWAYS AS (metadata->>'doc_type') STORED,
    ocr_status      text    GENERATED ALWAYS AS (metadata->>'ocr_status') STORED,
    upload_status   text    GENERATED ALWAYS AS (metadata->>'upload_status') STORED,
    CONSTRAINT user_files_pkey PRIMARY KEY (file_id),
    CONSTRAINT user_files_file_owner_uq UNIQUE (file_id, user_id),       -- target of the same-owner FK (221)
    CONSTRAINT user_files_origin_chk CHECK (origin IN ('chat', 'library')),
    -- metadata is merged with ||; on a non-object that would build an array.
    CONSTRAINT user_files_metadata_object_chk CHECK (jsonb_typeof(metadata) = 'object')
);

-- No index on filename on purpose: files_service compares the name in Python so
-- it never travels in a request URL.
CREATE INDEX IF NOT EXISTS user_files_user_created_idx ON public.user_files (user_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS user_files_user_size_idx    ON public.user_files (user_id, file_size_bytes) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS user_files_uploading_idx    ON public.user_files (created_at) WHERE upload_status = 'uploading' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS user_files_original_idx     ON public.user_files (created_at) WHERE storage_path IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS user_files_document_id_idx  ON public.user_files (document_id) WHERE document_id IS NOT NULL;

-- ── triggers ────────────────────────────────────────────────────────────────
-- Same rule as workspace_items_invalidate_summary (030): new text => summary is dirty.
CREATE OR REPLACE FUNCTION public.user_files_invalidate_summary()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF NEW.content_md IS DISTINCT FROM OLD.content_md THEN
        NEW.summary               := NULL;
        NEW.summary_source_length := NULL;
        NEW.summary_updated_at    := NULL;
    END IF;
    RETURN NEW;
END $$;

-- CREATE OR REPLACE TRIGGER (not DROP + CREATE): DROP TRIGGER takes ACCESS
-- EXCLUSIVE on the table even when the trigger does not exist.
CREATE OR REPLACE TRIGGER trg_user_files_invalidate_summary
    BEFORE UPDATE ON public.user_files
    FOR EACH ROW EXECUTE FUNCTION public.user_files_invalidate_summary();
CREATE OR REPLACE TRIGGER trg_user_files_updated_at
    BEFORE UPDATE ON public.user_files
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

-- ── helpers / RPCs ──────────────────────────────────────────────────────────
-- Text → timestamptz, NULL when it is not a date. The pattern check keeps the
-- special inputs ('now', 'yesterday', 'infinity', 'epoch') out, and keeps the
-- exception block (a subtransaction) off the common path.
CREATE OR REPLACE FUNCTION public.user_files_parse_ts(p text)
RETURNS timestamptz LANGUAGE plpgsql STABLE SET search_path = public AS $$
BEGIN
    IF p IS NULL OR p !~ '^\d{4}-\d{2}-\d{2}' THEN
        RETURN NULL;
    END IF;
    BEGIN
        RETURN p::timestamptz;
    EXCEPTION WHEN others THEN
        RETURN NULL;
    END;
END $$;

-- The one way application code writes a file. metadata is MERGED in the
-- statement (no read-modify-write), so two writers can no longer drop each
-- other's keys. A JSON null title keeps the title (NOT NULL column).
CREATE OR REPLACE FUNCTION public.user_file_patch(
    p_file_id        uuid,
    p_fields         jsonb DEFAULT '{}'::jsonb,
    p_metadata_patch jsonb DEFAULT '{}'::jsonb,
    p_user_id        uuid  DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = public AS $$
DECLARE
    v_fields jsonb := COALESCE(p_fields, '{}'::jsonb);
    v_patch  jsonb := COALESCE(p_metadata_patch, '{}'::jsonb);
    v_bad    text;
    v_id     uuid;
BEGIN
    IF jsonb_typeof(v_fields) <> 'object' OR jsonb_typeof(v_patch) <> 'object' THEN
        RAISE EXCEPTION 'user_file_patch: p_fields and p_metadata_patch must be JSON objects'
              USING ERRCODE = '22023';
    END IF;
    SELECT string_agg(k, ', ' ORDER BY k) INTO v_bad
      FROM jsonb_object_keys(v_fields) AS k
     WHERE k NOT IN ('title', 'content_md', 'summary', 'summary_source_length',
                     'summary_updated_at', 'storage_path', 'deleted_at');
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'user_file_patch: unknown field(s): %', v_bad USING ERRCODE = '22023';
    END IF;

    UPDATE public.user_files f SET
        title        = CASE WHEN v_fields ? 'title' THEN COALESCE(v_fields->>'title', f.title) ELSE f.title END,
        content_md   = CASE WHEN v_fields ? 'content_md' THEN COALESCE(v_fields->>'content_md', '') ELSE f.content_md END,
        summary      = CASE WHEN v_fields ? 'summary' THEN v_fields->>'summary' ELSE f.summary END,
        summary_source_length = CASE WHEN v_fields ? 'summary_source_length'
                                     THEN (v_fields->>'summary_source_length')::integer ELSE f.summary_source_length END,
        summary_updated_at    = CASE WHEN v_fields ? 'summary_updated_at'
                                     THEN (v_fields->>'summary_updated_at')::timestamptz ELSE f.summary_updated_at END,
        storage_path = CASE WHEN v_fields ? 'storage_path' THEN v_fields->>'storage_path' ELSE f.storage_path END,
        deleted_at   = CASE WHEN v_fields ? 'deleted_at' THEN (v_fields->>'deleted_at')::timestamptz ELSE f.deleted_at END,
        metadata     = f.metadata || v_patch
     WHERE f.file_id = p_file_id
       AND (p_user_id IS NULL OR f.user_id = p_user_id)
    RETURNING f.file_id INTO v_id;
    RETURN v_id;
END $$;

-- «حذف من الملفات»: one statement, first stamp kept. The metadata key is the
-- copy the pre-switch reader looks at; the column is the truth.
CREATE OR REPLACE FUNCTION public.user_files_hide(p_user_id uuid, p_file_ids uuid[])
RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = public AS $$
DECLARE v_n integer;
BEGIN
    UPDATE public.user_files
       SET library_hidden_at = now(),
           metadata = metadata || jsonb_build_object('library_hidden_at', now())
     WHERE user_id = p_user_id
       AND file_id = ANY (p_file_ids)
       AND library_hidden_at IS NULL
       AND deleted_at IS NULL;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
END $$;

-- ── RLS + grants ────────────────────────────────────────────────────────────
ALTER TABLE public.user_files ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_files_select_own ON public.user_files;
CREATE POLICY user_files_select_own ON public.user_files FOR SELECT TO authenticated
    USING (user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid())));

REVOKE ALL ON TABLE public.user_files FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.user_files TO service_role;

REVOKE ALL ON FUNCTION public.user_file_patch(uuid, jsonb, jsonb, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.user_files_hide(uuid, uuid[])            FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.user_files_parse_ts(text)                FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.user_files_invalidate_summary()          FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.user_file_patch(uuid, jsonb, jsonb, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.user_files_hide(uuid, uuid[])            TO service_role;
GRANT EXECUTE ON FUNCTION public.user_files_parse_ts(text)                TO service_role;

COMMENT ON TABLE public.user_files IS
    'One row per uploaded file per user (220): extracted text, summary, type, original. Text stored REAL. A conversation''s use of a file is a workspace_items row (kind=attachment) with file_id (221). Service-role only: RLS on, owner SELECT policy, no client grants.';
COMMENT ON COLUMN public.user_files.library_hidden_at IS
    '«حذف من الملفات»: set once by user_files_hide (first stamp kept). NULL = listed. metadata.library_hidden_at is a copy for the pre-switch reader.';
COMMENT ON FUNCTION public.user_file_patch(uuid, jsonb, jsonb, uuid) IS
    'Write one file (220). p_fields: title, content_md, summary, summary_source_length, summary_updated_at, storage_path, deleted_at (others raise 22023). p_metadata_patch is merged with ||. Returns file_id, NULL when no row matched.';

-- ── self-tests ───────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_n int;
BEGIN
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.user_files'::regclass) THEN
        RAISE EXCEPTION '220: RLS is off on user_files';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = 'public.user_files'::regclass
                    AND polname = 'user_files_select_own' AND polcmd = 'r') THEN
        RAISE EXCEPTION '220: owner SELECT policy missing on user_files';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = 'public.user_files'::regclass AND polcmd <> 'r') THEN
        RAISE EXCEPTION '220: user_files must have no client write policy';
    END IF;

    SELECT count(*) INTO v_n FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'user_files'
       AND grantee IN ('anon', 'authenticated', 'PUBLIC');
    IF v_n <> 0 THEN
        RAISE EXCEPTION '220: % client-role privileges on user_files', v_n;
    END IF;
    SELECT count(DISTINCT privilege_type) INTO v_n FROM information_schema.role_table_grants
     WHERE table_schema = 'public' AND table_name = 'user_files' AND grantee = 'service_role'
       AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE');
    IF v_n <> 4 THEN
        RAISE EXCEPTION '220: service_role must hold DML on user_files (found % of 4)', v_n;
    END IF;

    SELECT count(*) INTO v_n FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'user_files' AND is_generated = 'ALWAYS';
    IF v_n <> 8 THEN
        RAISE EXCEPTION '220: expected 8 generated columns on user_files, found %', v_n;
    END IF;

    IF (SELECT count(*) FROM pg_trigger WHERE tgrelid = 'public.user_files'::regclass AND NOT tgisinternal
          AND tgenabled = 'O' AND tgname IN ('trg_user_files_invalidate_summary', 'trg_user_files_updated_at')) <> 2 THEN
        RAISE EXCEPTION '220: the two BEFORE UPDATE triggers are missing or disabled';
    END IF;

    IF has_function_privilege('anon',          'public.user_file_patch(uuid,jsonb,jsonb,uuid)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.user_file_patch(uuid,jsonb,jsonb,uuid)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.user_files_hide(uuid,uuid[])', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.user_files_parse_ts(text)', 'EXECUTE') THEN
        RAISE EXCEPTION '220: a user_files function is callable by clients';
    END IF;
    IF NOT (has_function_privilege('service_role', 'public.user_file_patch(uuid,jsonb,jsonb,uuid)', 'EXECUTE')
            AND has_function_privilege('service_role', 'public.user_files_hide(uuid,uuid[])', 'EXECUTE')
            AND has_function_privilege('service_role', 'public.user_files_parse_ts(text)', 'EXECUTE')) THEN
        RAISE EXCEPTION '220: service_role cannot execute the user_files functions';
    END IF;

    -- the parser: dates in, everything else NULL, never an error
    IF public.user_files_parse_ts('2026-11-01T10:00:00Z') IS DISTINCT FROM timestamptz '2026-11-01 10:00:00+00'
       OR public.user_files_parse_ts('not-a-date') IS NOT NULL
       OR public.user_files_parse_ts('yesterday') IS NOT NULL
       OR public.user_files_parse_ts('2026-13-45') IS NOT NULL
       OR public.user_files_parse_ts('') IS NOT NULL THEN
        RAISE EXCEPTION '220: user_files_parse_ts misbehaves';
    END IF;

    RAISE NOTICE '220 OK: user_files created, RLS on, client roles revoked, service_role DML, 8 generated columns, RPCs service-only';
END $$;

COMMIT;

NOTIFY pgrst, 'reload schema';
