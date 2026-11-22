-- ============================================================================
-- Migration 222 — link every attachment to its file, copy it, keep both in sync.
--
-- Source: .claude/plans/my_files_phase2.md §B (222), §D, §G step 2; parent plan
--         .claude/plans/my_files.md D14 (EVERY attachment migrates; per-user
--         count check, all and live) and D15 (thin pointer).
--
-- WHAT IT DOES ────────────────────────────────────────────────────────────────
--   * workspace_items.file_id uuid (NULL except on kind = attachment), CHECK
--     workspace_items_file_kind_chk, composite FK workspace_items_file_owner_fkey
--     (file_id, user_id) → user_files (file_id, user_id): a pointer can only
--     ever link a file of the SAME user. Index workspace_items_file_id_idx.
--   * Copies every attachment row (875 on 2026-11-19: 835 live + 40
--     soft-deleted) into user_files under the SAME uuid (file_id = item_id) and
--     links it. The pointer rows stay byte-identical, updated_at included.
--   * Three triggers keep pointer and file equal from then on, in the same
--     transaction, whichever code version writes:
--       trg_workspace_items_file_link   BEFORE INSERT on workspace_items (attachments):
--           no file_id ⇒ create the file with the item's uuid; a given file_id
--           must be one of the owner's files (23503 otherwise).
--       trg_workspace_items_mirror_file AFTER UPDATE on workspace_items, HOME
--           pointer only (file_id = item_id): the old code path's write reaches
--           the file (title, text, summary + bookkeeping, metadata, storage_path,
--           document_id, deleted_at; library_hidden_at first stamp).
--       trg_user_files_sync_pointers    AFTER UPDATE on user_files: every pointer
--           of the file carries its title, text, summary + bookkeeping, metadata,
--           storage_path, document_id. deleted_at stays per pointer.
--     No ping-pong: the sync marks its own writes with the transaction-local
--     setting luna.user_files_sync = 'on' and the mirror ignores them; both
--     sides write only rows that differ (IS DISTINCT FROM), so updated_at moves
--     once, exactly when a value moves. An unchanged text is never copied (no
--     re-TOAST, no word recount of a multi-MB file on a metadata-only write).
--   * purge_user_data(uuid), two changes to the live (090) body:
--       - deletes user_files right after workspace_items (pointers before files);
--       - FIX (owner-approved 2026-11-19): deletes task_state FIRST, before
--         retrieval_artifacts / workspace_items / conversations. task_state has
--         NO ACTION FKs onto workspace_items (artifact_id) and conversations
--         (conversation_id); the live body deletes it last, so an account with
--         task_state rows fails at the workspace_items delete and is never
--         purged (prod 2026-11-19: 25 task_state rows for 2 users). The
--         to_regclass guard and the dynamic EXECUTE are kept as they were.
--     retrieval_artifacts (artifact_id → workspace_items, NO ACTION) is still
--     deleted before workspace_items, as today.
--
--   KNOWN PHASE-2 LIMITS (by design, see spec §E):
--   * Soft-deleting the HOME pointer soft-deletes the file; a second pointer's
--     deleted_at never reaches the file. With a second pointer live, deleting
--     the home pointer would hide the file from the other conversation, so the
--     mirror must be dropped before Phase 3 creates second pointers.
--   * A HARD delete of a pointer (only purge_user_data in production code;
--     dev eval teardown scripts also do it) leaves its file. Re-running this
--     file deletes such orphan 'chat' files.
--
-- TRANSACTIONS AND LOCKS ──────────────────────────────────────────────────────
-- Three writing transactions and one read-only check. Durations measured
-- locally (PG 17.6, 3,566 rows / 875 attachments / 26 MB, two 2.6 MB files):
--   Tx A  ~2 ms. ACCESS EXCLUSIVE on workspace_items for a catalog-only change:
--         ADD COLUMN (no rewrite, no default) + CHECK … NOT VALID (no scan).
--         Readers AND writers wait while it runs. lock_timeout 3s; while it waits
--         for the lock, new readers queue behind it, so rerun on timeout rather
--         than raise it. A re-run takes no lock at all (catalog-checked DO block).
--   Tx B  ~2.6 s. Bulk copy into user_files (the generated word_count is computed
--         here, once per file). ACCESS SHARE on workspace_items: nobody waits.
--   Tx C  ~0.1-0.15 s locally (budget 0.5 s on prod). SHARE ROW EXCLUSIVE on
--         workspace_items and user_files:
--         readers never wait, writers wait for the whole of Tx C (catch-up of rows
--         written during Tx B, the link UPDATE, FK + CHECK validation, one index
--         build, triggers, purge function, self-checks). Every DDL in Tx C stays
--         at or below SHARE ROW EXCLUSIVE: CREATE OR REPLACE TRIGGER (DROP TRIGGER
--         on an existing trigger takes ACCESS EXCLUSIVE and would block readers
--         on any re-run), ENABLE/DISABLE TRIGGER, ADD FOREIGN KEY, VALIDATE
--         CONSTRAINT (SHARE UPDATE EXCLUSIVE), CREATE INDEX (SHARE). Nothing in
--         Tx C detoasts the texts: the catch-up finds changed rows by their small
--         columns + updated_at, the checks compare byte lengths and word counts.
--   Tx D  ~0.2 s. READ ONLY, after COMMIT: compares every file's text byte for
--         byte with its pointer's. ACCESS SHARE only: nobody waits.
--   If Tx C fails, Tx A + Tx B stay committed and are harmless (a NULL column, an
--   unused copy; flags are off). Re-run the whole file.
--
-- SELF-TEST SAFETY (reviewer note, 2026-11-19) ─────────────────────────────────
--   The behaviour test inserts two throwaway attachment pointers inside a
--   subtransaction that is always rolled back (custom SQLSTATE LS222).
--   It cannot fail or leave anything behind on production:
--   * trg_workspace_items_cap (enforce_artifact_cap) returns at once for
--     kind = 'attachment' — no count, cannot raise.
--   * trg_workspace_items_created_while_paid only SELECTs user_subscriptions /
--     plans to set a boolean on the throwaway row.
--   * assign_workspace_item_seq returns at once (conversation_id NULL): no
--     advisory lock, no wi_seq.
--   * summarize_artifact_on_insert is WHEN (summary IS NULL); the test rows are
--     inserted WITH a summary, so notify_summarize_artifact never runs: no vault
--     read, no net.http_post. (Even if it ran, pg_net's queue row is
--     transactional and dies with the subtransaction.) Before inserting, the
--     test checks that this WHEN clause is still there and that no INSERT
--     trigger it does not know exists; otherwise it skips the insert part with
--     a NOTICE and keeps the catalog checks.
--   * The user is the oldest one without deletion_requested_at (not being
--     purged), so the FK's KEY SHARE row lock cannot meet the purge sweep.
--   * The test then asserts that neither throwaway row survived.
--
-- VERIFIED LIVE (catalog-only, prod dwgghvxogtwyaxmbgjod, 2026-11-19, by the
-- spec pass — re-run the preflight below before applying):
--   * the 7 triggers on workspace_items (assign_workspace_item_seq,
--     set_workspace_item_word_count, trg_workspace_items_cap,
--     trg_workspace_items_created_while_paid, trg_workspace_items_invalidate_summary,
--     update_artifacts_updated_at, summarize_artifact_on_insert WHEN summary IS NULL);
--   * 875 attachment rows: 0 with case_id / message_id / document_id /
--     is_visible = false, 0 NULL content_md (212 are ''), 435 NULL storage_path;
--     word_count = compute_word_count(content_md) on all;
--   * the live purge_user_data body equals migration 090 (statements);
--     task_state: 25 rows / 2 users; FKs task_state_artifact_id_fkey →
--     workspace_items (NO ACTION), task_state_conversation_id_fkey →
--     conversations (NO ACTION), task_state_user_id_fkey → users (CASCADE);
--   * pages / maps / helper runs point only at attachment rows.
-- TESTED LOCALLY 2026-11-19 on PostgreSQL 17.6 (scratchpad harness
-- phase2_dbtest, README.md there): catalog extract of prod + 3,566 invented
-- prod-shaped rows; idempotent re-run, D14 counts + parity, byte-identical
-- pointers, sync both ways, deletes, dry run, rollback, second connection lock
-- probe (readers not blocked during Tx C, writers wait).
--
-- PREFLIGHT (read-only):
--   SELECT to_regclass('public.user_files') IS NOT NULL                       AS has_221,
--          EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'public.workspace_items'::regclass
--                   AND attname = 'file_id' AND NOT attisdropped)            AS has_file_id,  -- false
--          (SELECT string_agg(tgname || '=' || tgenabled, ', ') FROM pg_trigger
--            WHERE tgrelid = 'public.workspace_items'::regclass AND NOT tgisinternal) AS triggers;
--            -- the 7 listed below, all 'O'
--   SELECT rolname, rolconfig FROM pg_roles WHERE rolname IN ('service_role', 'authenticator');
--     (statement_timeout of the backend role: writers wait for Tx C)
--
-- Dependencies: 221 (user_files, user_files_parse_ts), workspace_items, users,
--               purge_user_data (090).
-- DEPLOY ORDER: after 221; before any USER_FILES_* flag is on. Independent of
--   code deploys (flags off; the code's schema probe guards the rest). Quiet window.
-- RUN: as postgres (table owner: DISABLE TRIGGER needs ownership), as ONE
--   request (Supabase SQL editor) or `psql -v ON_ERROR_STOP=1 -f` — the run must
--   stop at the first error.
-- IDEMPOTENT: every step is catalog-guarded or ON CONFLICT / IS DISTINCT FROM;
--   the FK and CHECK adds carry duplicate_object guards. Safe to run twice.
-- DRY RUN: the same file with every COMMIT replaced by ROLLBACK (221 applied).
--   Tx A and Tx B then roll back and Tx C does their work itself: it copies
--   every row BEFORE taking its lock (nobody waits, ≈ 3 s), then adds the
--   column under ACCESS EXCLUSIVE right after the lock, so in a dry run readers
--   AND writers wait for the locked part of Tx C (≈ 0.2 s measured). Tx D
--   notes the missing column and skips.
-- ROLLBACK (set both USER_FILES_* flags off and redeploy first; roll back 223
-- first if it was applied — the guard refuses otherwise). No data is lost: every
-- write through the file path was copied to the pointer in the same transaction.
-- purge_user_data is restored WITHOUT the user_files line but KEEPS the
-- task_state-first fix: a rollback must not re-introduce a known failure. So
-- after rollback its body is the live one with the task_state block moved to
-- the top — the only intended difference from the pre-222 catalog.
--   BEGIN;
--   SET LOCAL lock_timeout = '5s';
--   DO $$ BEGIN
--       IF EXISTS (SELECT 1 FROM pg_constraint WHERE contype = 'f'
--                   AND confrelid = to_regclass('public.user_files')
--                   AND conrelid <> 'public.workspace_items'::regclass) THEN
--           RAISE EXCEPTION '222 rollback: roll back 223 first (a table still references user_files)';
--       END IF;
--   END $$;
--   DROP TRIGGER IF EXISTS trg_user_files_sync_pointers    ON public.user_files;
--   DROP TRIGGER IF EXISTS trg_workspace_items_mirror_file ON public.workspace_items;
--   DROP TRIGGER IF EXISTS trg_workspace_items_file_link   ON public.workspace_items;
--   DROP FUNCTION IF EXISTS public.user_files_sync_pointers(),
--                           public.workspace_items_mirror_file(),
--                           public.workspace_items_file_link();
--   CREATE OR REPLACE FUNCTION public.purge_user_data(p_user_id uuid)
--    RETURNS jsonb
--    LANGUAGE plpgsql
--    SET search_path TO 'public'
--   AS $function$
--   BEGIN
--       IF to_regclass('public.task_state') IS NOT NULL THEN
--           EXECUTE 'DELETE FROM public.task_state WHERE user_id = $1' USING p_user_id;
--       END IF;
--
--       DELETE FROM public.retrieval_artifacts WHERE user_id = p_user_id;
--       DELETE FROM public.workspace_items     WHERE user_id = p_user_id;
--       DELETE FROM public.conversations       WHERE user_id = p_user_id;
--       DELETE FROM public.lawyer_cases        WHERE lawyer_user_id = p_user_id;
--       DELETE FROM public.message_feedback    WHERE user_id = p_user_id;
--       DELETE FROM public.pii_mappings        WHERE user_id = p_user_id;
--       DELETE FROM public.user_templates      WHERE user_id = p_user_id;
--       DELETE FROM public.user_preferences    WHERE user_id = p_user_id;
--       DELETE FROM public.blog_posts          WHERE owner_user_id = p_user_id;
--       DELETE FROM public.paused_runs         WHERE user_id = p_user_id;
--
--       UPDATE public.plan_codes
--          SET redeemed_by_users = array_remove(redeemed_by_users, p_user_id)
--        WHERE p_user_id = ANY(redeemed_by_users);
--
--       RETURN jsonb_build_object('user_id', p_user_id, 'purged', true);
--   END;
--   $function$;
--   ALTER TABLE public.workspace_items DROP CONSTRAINT IF EXISTS workspace_items_file_owner_fkey;
--   DROP INDEX IF EXISTS public.workspace_items_file_id_idx;
--   ALTER TABLE public.workspace_items DROP CONSTRAINT IF EXISTS workspace_items_file_kind_chk;
--   ALTER TABLE public.workspace_items DROP COLUMN IF EXISTS file_id;   -- catalog-only, updated_at untouched
--   TRUNCATE public.user_files;
--   COMMIT;
--   NOTIFY pgrst, 'reload schema';
--   (The rollback's DROP TRIGGER / DROP COLUMN take ACCESS EXCLUSIVE on
--   workspace_items for milliseconds.)
-- ============================================================================

-- ══ Tx A: the link column (ACCESS EXCLUSIVE, catalog-only, milliseconds) ═════
BEGIN;
SET LOCAL lock_timeout = '3s';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_attribute
                    WHERE attrelid = 'public.workspace_items'::regclass
                      AND attname = 'file_id' AND NOT attisdropped) THEN
        ALTER TABLE public.workspace_items
            ADD COLUMN file_id uuid,
            ADD CONSTRAINT workspace_items_file_kind_chk
                CHECK (file_id IS NULL OR kind = 'attachment') NOT VALID;
        COMMENT ON COLUMN public.workspace_items.file_id IS
            'For kind=attachment: the user_files row this conversation item points at (222). Equal to item_id for uploads made in a conversation; never rely on that.';
    END IF;
END $$;

COMMIT;

-- ══ Tx B: bulk copy (reads workspace_items only; nobody waits) ══════════════
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

-- NOT EXISTS, not just ON CONFLICT: Postgres computes the generated columns
-- (word_count = a regexp over the whole text) BEFORE it detects a conflict.
INSERT INTO public.user_files
    (file_id, user_id, title, content_md, summary, summary_source_length, summary_updated_at,
     metadata, storage_path, document_id, origin, library_hidden_at, created_at, updated_at, deleted_at)
SELECT wi.item_id, wi.user_id, wi.title, COALESCE(wi.content_md, ''), wi.summary,
       wi.summary_source_length, wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb),
       wi.storage_path, wi.document_id, 'chat',
       public.user_files_parse_ts(wi.metadata->>'library_hidden_at'),
       wi.created_at, wi.updated_at, wi.deleted_at
  FROM public.workspace_items wi
 WHERE wi.kind = 'attachment'
   AND NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = wi.item_id)
ON CONFLICT (file_id) DO NOTHING;

COMMIT;

-- ══ Tx C: block writers, catch up, link, install the sync, verify ═══════════
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

DO $$
BEGIN
    IF (SELECT count(*) FROM pg_trigger WHERE tgrelid = 'public.workspace_items'::regclass AND tgenabled = 'O'
          AND tgname IN ('update_artifacts_updated_at', 'trg_workspace_items_invalidate_summary')) <> 2 THEN
        RAISE EXCEPTION '222: update_artifacts_updated_at / trg_workspace_items_invalidate_summary missing or not plainly enabled; refusing to toggle them';
    END IF;
END $$;

-- Before the lock (nobody waits): copy what Tx B did not see. Normal run: the
-- few rows created during Tx B. Dry run (Tx B rolled back): every row, so the
-- writers' wait below stays short in a dry run too.
INSERT INTO public.user_files
    (file_id, user_id, title, content_md, summary, summary_source_length, summary_updated_at,
     metadata, storage_path, document_id, origin, library_hidden_at, created_at, updated_at, deleted_at)
SELECT wi.item_id, wi.user_id, wi.title, COALESCE(wi.content_md, ''), wi.summary,
       wi.summary_source_length, wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb),
       wi.storage_path, wi.document_id, 'chat',
       public.user_files_parse_ts(wi.metadata->>'library_hidden_at'),
       wi.created_at, wi.updated_at, wi.deleted_at
  FROM public.workspace_items wi
 WHERE wi.kind = 'attachment'
   AND NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = wi.item_id)
ON CONFLICT (file_id) DO NOTHING;

LOCK TABLE public.workspace_items IN SHARE ROW EXCLUSIVE MODE;   -- writers wait; readers do not
LOCK TABLE public.user_files      IN SHARE ROW EXCLUSIVE MODE;

-- Normal run: Tx A committed, so this is a catalog read and takes no lock.
-- Dry run (Tx A rolled back): adds the column here, under ACCESS EXCLUSIVE,
-- which the dry run then holds for the rest of Tx C (≈ 0.2 s measured).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_attribute
                    WHERE attrelid = 'public.workspace_items'::regclass
                      AND attname = 'file_id' AND NOT attisdropped) THEN
        RAISE NOTICE '222: file_id missing at Tx C (dry run?) — adding it here under ACCESS EXCLUSIVE';
        ALTER TABLE public.workspace_items
            ADD COLUMN file_id uuid,
            ADD CONSTRAINT workspace_items_file_kind_chk
                CHECK (file_id IS NULL OR kind = 'attachment') NOT VALID;
    END IF;
END $$;

-- Fingerprint of the attachment rows: every column but file_id and content_md
-- (the live column list, 2026-11-19), plus the text's byte length, read from
-- the TOAST header without detoasting. NOT its stored size (pg_column_size):
-- the link UPDATE writes a new row version 16 bytes wider (file_id), and a row
-- that sat just under the TOAST threshold gets its in-row compressed text moved
-- out to TOAST — same bytes, different stored size. Prod trial run 2026-11-21:
-- 1 row of 911 (a 2 KB text compressed in the row, summary already in TOAST)
-- tripped the check on that alone. The link UPDATE sets only file_id; Tx D
-- compares the full text bytes after COMMIT. Session-temporary function,
-- dropped below.
CREATE OR REPLACE FUNCTION pg_temp.m222_fingerprint() RETURNS text LANGUAGE sql STABLE AS $f$
    SELECT md5(string_agg(md5(ROW(
               wi.item_id, wi.user_id, wi.conversation_id, wi.case_id, wi.agent_family, wi.title, wi.metadata,
               wi.created_at, wi.updated_at, wi.deleted_at, wi.message_id, wi.kind, wi.created_by, wi.storage_path,
               wi.document_id, wi.is_visible, wi.locked_by_agent_until, wi.summary, wi.summary_source_length,
               wi.summary_updated_at, wi.describe_query, wi.word_count, wi.used_ref_words_total, wi.wi_seq,
               wi.prev_content_md, wi.feedback, wi.created_while_paid,
               octet_length(wi.content_md))::text), '' ORDER BY wi.item_id))
           || ':' || count(*)
      FROM public.workspace_items wi
     WHERE wi.kind = 'attachment'
$f$;
CREATE TEMP TABLE _222_before ON COMMIT DROP AS SELECT pg_temp.m222_fingerprint() AS h;

-- Catch-up under the lock for rows written since (new, changed, hard-deleted).
-- The file's own triggers are off: this is a copy, not an edit (the summary
-- must not be cleared, updated_at must be copied). Generated columns still compute.
ALTER TABLE public.user_files DISABLE TRIGGER USER;

INSERT INTO public.user_files
    (file_id, user_id, title, content_md, summary, summary_source_length, summary_updated_at,
     metadata, storage_path, document_id, origin, library_hidden_at, created_at, updated_at, deleted_at)
SELECT wi.item_id, wi.user_id, wi.title, COALESCE(wi.content_md, ''), wi.summary,
       wi.summary_source_length, wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb),
       wi.storage_path, wi.document_id, 'chat',
       public.user_files_parse_ts(wi.metadata->>'library_hidden_at'),
       wi.created_at, wi.updated_at, wi.deleted_at
  FROM public.workspace_items wi
 WHERE wi.kind = 'attachment'
   AND NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = wi.item_id)
ON CONFLICT (file_id) DO NOTHING;

-- A row written after Tx B's snapshot has a new updated_at (its trigger is on
-- for every writer), so comparing the small columns + updated_at finds every
-- such row without reading any text; the text is copied only for those rows.
UPDATE public.user_files f SET
       title                 = wi.title,
       content_md            = CASE WHEN f.content_md IS DISTINCT FROM COALESCE(wi.content_md, '')
                                    THEN COALESCE(wi.content_md, '') ELSE f.content_md END,
       summary               = wi.summary,
       summary_source_length = wi.summary_source_length,
       summary_updated_at    = wi.summary_updated_at,
       metadata              = COALESCE(wi.metadata, '{}'::jsonb),
       storage_path          = wi.storage_path,
       document_id           = wi.document_id,
       updated_at            = wi.updated_at,
       deleted_at            = wi.deleted_at,
       library_hidden_at     = COALESCE(f.library_hidden_at, public.user_files_parse_ts(wi.metadata->>'library_hidden_at'))
  FROM public.workspace_items wi
 WHERE wi.item_id = f.file_id
   AND wi.kind = 'attachment'
   AND (f.title, f.summary, f.summary_source_length, f.summary_updated_at, f.metadata,
        f.storage_path, f.document_id, f.updated_at, f.deleted_at, octet_length(f.content_md))
       IS DISTINCT FROM
       (wi.title, wi.summary, wi.summary_source_length, wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb),
        wi.storage_path, wi.document_id, wi.updated_at, wi.deleted_at, octet_length(COALESCE(wi.content_md, '')));

-- Rows hard-deleted during Tx B left a copy with no pointer: drop it, or the
-- D14 count check below fails forever.
DELETE FROM public.user_files f
 WHERE f.origin = 'chat'
   AND NOT EXISTS (SELECT 1 FROM public.workspace_items wi WHERE wi.item_id = f.file_id AND wi.kind = 'attachment')
   AND NOT EXISTS (SELECT 1 FROM public.workspace_items wi WHERE wi.file_id = f.file_id);

ALTER TABLE public.user_files ENABLE TRIGGER USER;

-- Link. updated_at must not move: the web viewer footer and the mobile change
-- stamp read it. Only file_id is in the SET list, so the word-count trigger
-- does not fire and the text keeps its TOAST pointer. The invalidate-summary
-- trigger is off too: it could only compare two equal texts (it detoasts both,
-- ≈ 250 ms of the writers' wait on prod-sized data, for nothing).
ALTER TABLE public.workspace_items
    DISABLE TRIGGER update_artifacts_updated_at,
    DISABLE TRIGGER trg_workspace_items_invalidate_summary;
UPDATE public.workspace_items SET file_id = item_id WHERE kind = 'attachment' AND file_id IS NULL;
ALTER TABLE public.workspace_items
    ENABLE TRIGGER update_artifacts_updated_at,
    ENABLE TRIGGER trg_workspace_items_invalidate_summary;

-- A pointer can only ever link a file of the SAME user (composite FK).
DO $$
BEGIN
    ALTER TABLE public.workspace_items
        ADD CONSTRAINT workspace_items_file_owner_fkey
        FOREIGN KEY (file_id, user_id) REFERENCES public.user_files (file_id, user_id) NOT VALID;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
ALTER TABLE public.workspace_items VALIDATE CONSTRAINT workspace_items_file_owner_fkey;
ALTER TABLE public.workspace_items VALIDATE CONSTRAINT workspace_items_file_kind_chk;
CREATE INDEX IF NOT EXISTS workspace_items_file_id_idx
    ON public.workspace_items (file_id) WHERE file_id IS NOT NULL;

-- (1) An attachment row is born with a file (same uuid), or must point at one of its owner's.
--     BEFORE INSERT triggers fire in name order; this one sorts last, so it
--     copies the row as the other four left it.
CREATE OR REPLACE FUNCTION public.workspace_items_file_link()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF NEW.item_id IS NULL THEN
        NEW.item_id := gen_random_uuid();
    END IF;
    IF NEW.file_id IS NULL THEN
        NEW.file_id := NEW.item_id;
        INSERT INTO public.user_files
            (file_id, user_id, title, content_md, summary, summary_source_length, summary_updated_at,
             metadata, storage_path, document_id, origin, library_hidden_at, created_at, updated_at, deleted_at)
        VALUES (NEW.item_id, NEW.user_id, NEW.title, COALESCE(NEW.content_md, ''), NEW.summary,
                NEW.summary_source_length, NEW.summary_updated_at, COALESCE(NEW.metadata, '{}'::jsonb),
                NEW.storage_path, NEW.document_id, 'chat',
                public.user_files_parse_ts(NEW.metadata->>'library_hidden_at'),
                COALESCE(NEW.created_at, now()), COALESCE(NEW.updated_at, now()), NEW.deleted_at)
        ON CONFLICT (file_id) DO NOTHING;   -- same user: links it; other user: the FK rejects the row
    ELSIF NOT EXISTS (SELECT 1 FROM public.user_files f
                       WHERE f.file_id = NEW.file_id AND f.user_id = NEW.user_id) THEN
        RAISE EXCEPTION 'workspace_items.file_id % is not a file of this user', NEW.file_id
              USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_workspace_items_file_link
    BEFORE INSERT ON public.workspace_items
    FOR EACH ROW WHEN (NEW.kind = 'attachment')
    EXECUTE FUNCTION public.workspace_items_file_link();

-- (2) pointer -> file: a write by code that still writes workspace_items reaches
--     the file. HOME pointer only (file_id = item_id). Ignores the sync's own
--     writes (luna.user_files_sync = 'on') — not pg_trigger_depth(), which would
--     also silence a legitimate attachment write made from inside another trigger.
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
           deleted_at            = NEW.deleted_at,
           library_hidden_at     = COALESCE(f.library_hidden_at,
                                            public.user_files_parse_ts(NEW.metadata->>'library_hidden_at'))
     WHERE f.file_id = NEW.file_id
       AND (f.title, f.content_md, f.summary, f.summary_source_length, f.summary_updated_at, f.metadata,
            f.storage_path, f.document_id, f.deleted_at)
           IS DISTINCT FROM
           (NEW.title, COALESCE(NEW.content_md, ''), NEW.summary, NEW.summary_source_length,
            NEW.summary_updated_at, COALESCE(NEW.metadata, '{}'::jsonb), NEW.storage_path,
            NEW.document_id, NEW.deleted_at);
    RETURN NULL;
END $$;
CREATE OR REPLACE TRIGGER trg_workspace_items_mirror_file
    AFTER UPDATE ON public.workspace_items
    FOR EACH ROW WHEN (NEW.kind = 'attachment' AND NEW.file_id IS NOT NULL AND NEW.file_id = NEW.item_id)
    EXECUTE FUNCTION public.workspace_items_mirror_file();

-- (3) file -> pointers: every conversation item of the file carries the file's
--     small fields and text. Two statements so an unchanged text is never copied
--     (copying it would re-TOAST it into workspace_items and fire the pointer's
--     word-count trigger: ≈ 200 ms and the full text in WAL for a 2.7 MB file).
CREATE OR REPLACE FUNCTION public.user_files_sync_pointers()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
DECLARE
    v_prev text := current_setting('luna.user_files_sync', true);
BEGIN
    PERFORM set_config('luna.user_files_sync', 'on', true);

    -- pointers whose text differs: everything
    UPDATE public.workspace_items wi SET
           title                 = NEW.title,
           content_md            = NEW.content_md,
           summary               = NEW.summary,
           summary_source_length = NEW.summary_source_length,
           summary_updated_at    = NEW.summary_updated_at,
           metadata              = NEW.metadata,
           storage_path          = NEW.storage_path,
           document_id           = NEW.document_id
     WHERE wi.file_id = NEW.file_id
       AND wi.kind = 'attachment'
       AND COALESCE(wi.content_md, '') IS DISTINCT FROM NEW.content_md;

    -- the others: small fields only, and only where one differs
    UPDATE public.workspace_items wi SET
           title                 = NEW.title,
           summary               = NEW.summary,
           summary_source_length = NEW.summary_source_length,
           summary_updated_at    = NEW.summary_updated_at,
           metadata              = NEW.metadata,
           storage_path          = NEW.storage_path,
           document_id           = NEW.document_id
     WHERE wi.file_id = NEW.file_id
       AND wi.kind = 'attachment'
       AND (wi.title, wi.summary, wi.summary_source_length, wi.summary_updated_at,
            COALESCE(wi.metadata, '{}'::jsonb), wi.storage_path, wi.document_id)
           IS DISTINCT FROM
           (NEW.title, NEW.summary, NEW.summary_source_length, NEW.summary_updated_at,
            NEW.metadata, NEW.storage_path, NEW.document_id);

    PERFORM set_config('luna.user_files_sync', COALESCE(v_prev, ''), true);
    RETURN NULL;
END $$;
CREATE OR REPLACE TRIGGER trg_user_files_sync_pointers
    AFTER UPDATE ON public.user_files
    FOR EACH ROW EXECUTE FUNCTION public.user_files_sync_pointers();

REVOKE ALL ON FUNCTION public.workspace_items_file_link(),
                       public.workspace_items_mirror_file(),
                       public.user_files_sync_pointers() FROM PUBLIC, anon, authenticated;

-- Account purge. Two changes to the live (090) body, nothing else:
--   * task_state first (owner-approved fix, 2026-11-19): its artifact_id ->
--     workspace_items and conversation_id -> conversations FKs are NO ACTION,
--     so the live order fails at the workspace_items delete for any user with
--     task_state rows (prod: 25 rows, 2 users) and the account is never purged.
--   * user_files right after workspace_items: files die AFTER their pointers (FK).
-- retrieval_artifacts still goes before workspace_items (its artifact_id FK is
-- NO ACTION too). ACL and comment are kept by CREATE OR REPLACE; restated for a
-- fresh database.
CREATE OR REPLACE FUNCTION public.purge_user_data(p_user_id uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO 'public'
AS $function$
BEGIN
    IF to_regclass('public.task_state') IS NOT NULL THEN
        EXECUTE 'DELETE FROM public.task_state WHERE user_id = $1' USING p_user_id;
    END IF;

    DELETE FROM public.retrieval_artifacts WHERE user_id = p_user_id;
    DELETE FROM public.workspace_items     WHERE user_id = p_user_id;
    DELETE FROM public.user_files          WHERE user_id = p_user_id;  -- 222: after its pointers (FK)
    DELETE FROM public.conversations       WHERE user_id = p_user_id;
    DELETE FROM public.lawyer_cases        WHERE lawyer_user_id = p_user_id;
    DELETE FROM public.message_feedback    WHERE user_id = p_user_id;
    DELETE FROM public.pii_mappings        WHERE user_id = p_user_id;
    DELETE FROM public.user_templates      WHERE user_id = p_user_id;
    DELETE FROM public.user_preferences    WHERE user_id = p_user_id;
    DELETE FROM public.blog_posts          WHERE owner_user_id = p_user_id;
    DELETE FROM public.paused_runs         WHERE user_id = p_user_id;

    UPDATE public.plan_codes
       SET redeemed_by_users = array_remove(redeemed_by_users, p_user_id)
     WHERE p_user_id = ANY(redeemed_by_users);

    RETURN jsonb_build_object('user_id', p_user_id, 'purged', true);
END;
$function$;
REVOKE ALL ON FUNCTION public.purge_user_data(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.purge_user_data(uuid) TO service_role;

-- ── self-checks ──────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_n      bigint;
    v_src    text;
    v_user   uuid;
    v_other  uuid;
    v_item   uuid := gen_random_uuid();
    v_item2  uuid := gen_random_uuid();
    v_safe   boolean;
    v_ok     boolean := false;
BEGIN
    -- every attachment has exactly its own file; nothing else has one
    SELECT count(*) INTO v_n FROM public.workspace_items
     WHERE (kind = 'attachment' AND file_id IS DISTINCT FROM item_id)
        OR (kind <> 'attachment' AND file_id IS NOT NULL);
    IF v_n <> 0 THEN RAISE EXCEPTION '222: % rows with a wrong file_id', v_n; END IF;

    -- count check, per user, all and live (owner decision D14)
    SELECT count(*) INTO v_n FROM
        (SELECT user_id, count(*) n, count(*) FILTER (WHERE deleted_at IS NULL) live
           FROM public.workspace_items WHERE kind = 'attachment' GROUP BY 1) a
        FULL JOIN
        (SELECT user_id, count(*) n, count(*) FILTER (WHERE deleted_at IS NULL) live
           FROM public.user_files GROUP BY 1) b USING (user_id)
     WHERE a.n IS DISTINCT FROM b.n OR a.live IS DISTINCT FROM b.live;
    IF v_n <> 0 THEN RAISE EXCEPTION '222: attachment / file counts differ for % user(s)', v_n; END IF;

    -- field parity on every row, including the derived columns agents read.
    -- The text is compared by byte length + word count here (no detoast while
    -- writers wait); Tx D compares the full text bytes after COMMIT.
    SELECT count(*) INTO v_n
      FROM public.workspace_items wi
      JOIN public.user_files f ON f.file_id = wi.file_id
     WHERE wi.kind = 'attachment'
       AND (wi.user_id, wi.title, octet_length(COALESCE(wi.content_md, '')), wi.word_count, wi.summary,
            wi.summary_source_length, wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb), wi.storage_path,
            wi.document_id, wi.created_at, wi.updated_at, wi.deleted_at,
            wi.metadata->>'doc_type', wi.metadata->>'filename', wi.metadata->>'upload_status', wi.metadata->>'ocr_status',
            public.user_files_parse_ts(wi.metadata->>'library_hidden_at'), 'chat'::text)
           IS DISTINCT FROM
           (f.user_id, f.title, octet_length(f.content_md), f.word_count, f.summary,
            f.summary_source_length, f.summary_updated_at, f.metadata, f.storage_path,
            f.document_id, f.created_at, f.updated_at, f.deleted_at,
            f.doc_type, f.filename, f.upload_status, f.ocr_status,
            f.library_hidden_at, f.origin);
    IF v_n <> 0 THEN RAISE EXCEPTION '222: % file row(s) differ from their attachment row', v_n; END IF;

    -- the pointer rows themselves did not change (updated_at included)
    IF (SELECT h FROM _222_before) IS DISTINCT FROM pg_temp.m222_fingerprint() THEN
        RAISE EXCEPTION '222: attachment rows changed during the migration';
    END IF;

    -- pages / maps / helper runs all have a file under the same id (223 depends on it)
    SELECT (SELECT count(*) FROM public.attachment_pages p
             WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = p.item_id))
         + (SELECT count(*) FROM public.attachment_segment_maps m
             WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = m.item_id))
         + (SELECT count(*) FROM public.helper_runs h
             WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = h.item_id))
      INTO v_n;
    IF v_n <> 0 THEN RAISE EXCEPTION '222: % page / map / helper rows without a file', v_n; END IF;

    -- constraints validated
    IF (SELECT count(*) FROM pg_constraint WHERE conrelid = 'public.workspace_items'::regclass AND convalidated
          AND conname IN ('workspace_items_file_owner_fkey', 'workspace_items_file_kind_chk')) <> 2 THEN
        RAISE EXCEPTION '222: file FK / CHECK missing or not validated';
    END IF;

    -- triggers in place and enabled (update_artifacts_updated_at was toggled above)
    SELECT count(*) INTO v_n FROM pg_trigger
     WHERE NOT tgisinternal AND tgenabled = 'O'
       AND ((tgrelid = 'public.workspace_items'::regclass
             AND tgname IN ('update_artifacts_updated_at', 'trg_workspace_items_file_link',
                            'trg_workspace_items_mirror_file'))
         OR (tgrelid = 'public.user_files'::regclass
             AND tgname IN ('trg_user_files_sync_pointers', 'trg_user_files_invalidate_summary',
                            'trg_user_files_updated_at')));
    IF v_n <> 6 THEN RAISE EXCEPTION '222: expected 6 enabled triggers, found %', v_n; END IF;
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE NOT tgisinternal AND tgenabled <> 'O'
                AND tgrelid IN ('public.workspace_items'::regclass, 'public.user_files'::regclass)) THEN
        RAISE EXCEPTION '222: a trigger on workspace_items / user_files is left disabled';
    END IF;

    -- trigger functions are not client-callable
    IF has_function_privilege('authenticated', 'public.user_files_sync_pointers()', 'EXECUTE')
       OR has_function_privilege('anon', 'public.workspace_items_file_link()', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.purge_user_data(uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION '222: a 222 function is callable by clients';
    END IF;

    -- purge order: task_state and retrieval_artifacts (NO ACTION FKs onto
    -- workspace_items / conversations) before both; pointers before files
    SELECT prosrc INTO v_src FROM pg_proc WHERE oid = 'public.purge_user_data(uuid)'::regprocedure;
    IF strpos(v_src, 'DELETE FROM public.task_state') = 0
       OR strpos(v_src, 'DELETE FROM public.retrieval_artifacts') = 0
       OR strpos(v_src, 'DELETE FROM public.workspace_items') = 0
       OR strpos(v_src, 'DELETE FROM public.user_files') = 0
       OR strpos(v_src, 'DELETE FROM public.conversations') = 0 THEN
        RAISE EXCEPTION '222: purge_user_data lacks one of the task_state / retrieval_artifacts / workspace_items / user_files / conversations deletes';
    END IF;
    IF strpos(v_src, 'DELETE FROM public.task_state') > strpos(v_src, 'DELETE FROM public.workspace_items')
       OR strpos(v_src, 'DELETE FROM public.task_state') > strpos(v_src, 'DELETE FROM public.conversations') THEN
        RAISE EXCEPTION '222: purge_user_data must delete task_state before workspace_items and conversations';
    END IF;
    IF strpos(v_src, 'DELETE FROM public.retrieval_artifacts') > strpos(v_src, 'DELETE FROM public.workspace_items') THEN
        RAISE EXCEPTION '222: purge_user_data must delete retrieval_artifacts before workspace_items';
    END IF;
    IF strpos(v_src, 'DELETE FROM public.workspace_items') > strpos(v_src, 'DELETE FROM public.user_files') THEN
        RAISE EXCEPTION '222: purge_user_data must delete workspace_items before user_files';
    END IF;

    -- behaviour, on rows that never commit (see SELF-TEST SAFETY in the header).
    -- Only when every INSERT trigger on workspace_items is one we know, and the
    -- webhook trigger still skips rows that carry a summary.
    v_safe := NOT EXISTS (
                  SELECT 1 FROM pg_trigger t
                   WHERE t.tgrelid = 'public.workspace_items'::regclass AND NOT t.tgisinternal
                     AND (t.tgtype & 4) <> 0                                   -- fires on INSERT
                     AND t.tgname NOT IN ('assign_workspace_item_seq', 'set_workspace_item_word_count',
                                          'trg_workspace_items_cap', 'trg_workspace_items_created_while_paid',
                                          'trg_workspace_items_file_link', 'summarize_artifact_on_insert'))
              AND NOT EXISTS (
                  SELECT 1 FROM pg_trigger t
                   WHERE t.tgrelid = 'public.workspace_items'::regclass
                     AND t.tgname = 'summarize_artifact_on_insert'
                     AND pg_get_triggerdef(t.oid) NOT ILIKE '%new.summary IS NULL%');
    SELECT user_id INTO v_user FROM public.users
     WHERE deletion_requested_at IS NULL ORDER BY created_at, user_id LIMIT 1;
    SELECT user_id INTO v_other FROM public.users
     WHERE deletion_requested_at IS NULL AND user_id <> v_user ORDER BY created_at, user_id LIMIT 1;

    IF NOT v_safe THEN
        RAISE NOTICE '222: unknown INSERT trigger on workspace_items — behaviour test skipped (catalog checks passed)';
    ELSIF v_user IS NULL THEN
        RAISE NOTICE '222: no user — behaviour test skipped';
    ELSE
        BEGIN
            -- (a) an attachment insert creates its file, same uuid, same transaction
            INSERT INTO public.workspace_items (item_id, user_id, kind, created_by, title, storage_path, summary, metadata)
            VALUES (v_item, v_user, 'attachment', 'user', '222 self-test', '222/self-test', '222 self-test',
                    '{"filename":"t.pdf","mime_type":"application/pdf","file_size_bytes":10,"upload_status":"uploading"}');
            IF NOT EXISTS (SELECT 1 FROM public.user_files WHERE file_id = v_item AND user_id = v_user
                              AND filename = 't.pdf' AND file_size_bytes = 10 AND upload_status = 'uploading'
                              AND summary = '222 self-test')
               OR (SELECT file_id FROM public.workspace_items WHERE item_id = v_item) IS DISTINCT FROM v_item THEN
                RAISE EXCEPTION '222: an attachment insert did not create its file';
            END IF;

            -- (b) old path: a pointer write reaches the file; new text clears both summaries
            UPDATE public.workspace_items
               SET content_md = 'abc def', metadata = metadata || '{"ocr_status":"done"}'
             WHERE item_id = v_item;
            IF NOT EXISTS (SELECT 1 FROM public.user_files WHERE file_id = v_item AND content_md = 'abc def'
                              AND content_chars = 7 AND word_count = 2 AND ocr_status = 'done' AND summary IS NULL)
               OR (SELECT summary FROM public.workspace_items WHERE item_id = v_item) IS NOT NULL THEN
                RAISE EXCEPTION '222: pointer -> file mirror failed';
            END IF;

            -- (c) a second pointer (item_id <> file_id) of the same owner
            INSERT INTO public.workspace_items (item_id, user_id, kind, created_by, title, file_id, summary)
            VALUES (v_item2, v_user, 'attachment', 'user', '222 self-test 2', v_item, '222 self-test');

            -- (d) new path: the file write reaches every pointer; metadata is merged
            PERFORM public.user_file_patch(v_item, '{"summary":"s","title":"t2"}'::jsonb,
                                           '{"doc_type":"contract"}'::jsonb, v_user);
            IF (SELECT count(*) FROM public.workspace_items
                 WHERE file_id = v_item AND summary = 's' AND title = 't2' AND content_md = 'abc def'
                   AND metadata->>'doc_type' = 'contract' AND metadata->>'ocr_status' = 'done'
                   AND metadata->>'filename' = 't.pdf') <> 2 THEN
                RAISE EXCEPTION '222: file -> pointer sync failed';
            END IF;

            -- (e) new text through the file clears the summary everywhere
            PERFORM public.user_file_patch(v_item, '{"content_md":"x"}'::jsonb);
            IF EXISTS (SELECT 1 FROM public.user_files WHERE file_id = v_item AND summary IS NOT NULL)
               OR (SELECT count(*) FROM public.workspace_items
                    WHERE file_id = v_item AND content_md = 'x' AND summary IS NULL AND word_count = 1) <> 2 THEN
                RAISE EXCEPTION '222: new text must clear the summary on the file and every pointer';
            END IF;

            -- (f) a pointer cannot link another user's file
            IF v_other IS NOT NULL THEN
                BEGIN
                    INSERT INTO public.workspace_items (user_id, kind, created_by, title, file_id, summary)
                    VALUES (v_other, 'attachment', 'user', '222 self-test 3', v_item, '222 self-test');
                    RAISE EXCEPTION '222: a pointer linked another user''s file';
                EXCEPTION WHEN foreign_key_violation THEN NULL;
                END;
            END IF;

            RAISE EXCEPTION 'undo' USING ERRCODE = 'LS222';
        EXCEPTION WHEN SQLSTATE 'LS222' THEN
            v_ok := true;
        END;
        IF NOT v_ok
           OR EXISTS (SELECT 1 FROM public.user_files WHERE file_id IN (v_item, v_item2))
           OR EXISTS (SELECT 1 FROM public.workspace_items WHERE item_id IN (v_item, v_item2)) THEN
            RAISE EXCEPTION '222: self-test left a row behind';
        END IF;
    END IF;

    RAISE NOTICE '222 OK: every attachment has its file (D14 counts + parity), pointers unchanged, sync both ways, purge covers files';
END $$;

DROP FUNCTION pg_temp.m222_fingerprint();

COMMIT;

NOTIFY pgrst, 'reload schema';

-- ══ Tx D: full-text check, READ ONLY, after COMMIT (nobody waits) ════════════
-- Compares every file's text byte for byte with its home pointer's. Tx C kept
-- this out of the writer lock (it reads every text twice). If it raises, the
-- data is already live: run the ROLLBACK above.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '300s';

DO $$
DECLARE v_n bigint;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'public.workspace_items'::regclass
                    AND attname = 'file_id' AND NOT attisdropped) THEN
        RAISE NOTICE '222 (Tx D): no workspace_items.file_id (dry run?) — full-text check skipped';
        RETURN;
    END IF;
    EXECUTE $q$
        SELECT count(*) FROM public.workspace_items wi
          JOIN public.user_files f ON f.file_id = wi.file_id
         WHERE wi.kind = 'attachment' AND wi.item_id = wi.file_id
           AND COALESCE(wi.content_md, '') IS DISTINCT FROM f.content_md $q$ INTO v_n;
    IF v_n <> 0 THEN
        RAISE EXCEPTION '222 (Tx D): % file text(s) differ from their attachment — run the 222 rollback', v_n;
    END IF;
    RAISE NOTICE '222 OK (Tx D): every file text is byte-identical to its attachment';
END $$;

COMMIT;
