# «الملفات» Phase 2 — files in their own table (`user_files`): build spec

**Status:** SPEC 2026-11-19 — written by a read-only design pass over `C:/Programming/LUNA_AI_my_files`
(`feat/my-files`) and catalog-only queries on prod. Nothing here has been built or executed.
Parent plan: `.claude/plans/my_files.md` (owner decisions D10, D14, D15). Owner-chosen shape: thin
pointer. Applying any migration to production and turning any flag on each need the owner's go.

**Reviewer notes before build (2026-11-19):**
- The self-test in 221 inserts a throwaway attachment for an arbitrary user; `trg_workspace_items_cap`
  and `summarize_artifact_on_insert` fire on it. Verify on a copy that neither can fail or leave a
  queued webhook behind, or choose the row's user deliberately.
- Add the `duplicate_object` guard to the `workspace_items_file_owner_fkey` statement (noted under 221).
- Dry-run 220-222 on a local copy of the schema before any production run.

---

# Phase 2 spec — «الملفات» files in their own table (`user_files`)

Code read at `C:\Programming\LUNA_AI_my_files` (`feat/my-files`, uncommitted Phase 1 included). I also ran read-only catalog and aggregate queries on prod `dwgghvxogtwyaxmbgjod` (2026-11-19); no row content was read. Nothing was modified, and none of the SQL below has been executed.

**Live facts the design rests on**
- Postgres 17.6. `postgres` owns the tables but is not superuser, so `session_replication_role` is unavailable.
- `workspace_items` has 3,566 rows and 28 MB in total. Table size is not the concern; locks are.
- Triggers on `workspace_items`:

| Trigger | Timing |
|---|---|
| `assign_workspace_item_seq` | BEFORE INSERT |
| `set_workspace_item_word_count` | BEFORE INSERT OR UPDATE OF `content_md` |
| `trg_workspace_items_cap` | BEFORE INSERT |
| `trg_workspace_items_created_while_paid` | BEFORE INSERT |
| `trg_workspace_items_invalidate_summary` | BEFORE UPDATE |
| `update_artifacts_updated_at` | BEFORE UPDATE |
| `summarize_artifact_on_insert` | AFTER INSERT WHEN `summary IS NULL` |

- FKs into `workspace_items(item_id)`:

| Constraint | On delete |
|---|---|
| `attachment_pages_item_id_fkey` | CASCADE |
| `attachment_segment_maps_item_id_fkey` | CASCADE |
| `helper_runs_item_id_fkey` | CASCADE |
| `message_attachments_document_id_fkey` | CASCADE |
| `workspace_item_references_wi_id_fkey` | CASCADE |
| `retrieval_artifacts_artifact_id_fkey` | NO ACTION |
| `task_state_artifact_id_fkey` | NO ACTION |

- The 875 attachment rows: 0 have `document_id`, `case_id`, `message_id` or `is_visible=false`; 0 have NULL `content_md` (212 are `''`, because the column default is `''`); 435 have NULL `storage_path`.
- On every attachment row: `word_count = compute_word_count(content_md)`, `ocr_chars = char_length(content_md)`, and `summary_source_length = char_length(btrim(content_md))`.
- No `metadata.library_hidden_at` exists yet, and no relation is named `user_files`.
- `helper_runs` (80), `attachment_pages` (805) and `attachment_segment_maps` (80) all point at attachment rows. `message_attachments` has 599 attachment links plus 34 `references` links.
- The live `purge_user_data` body equals migration 090.
- Prod `schema_migrations` uses timestamp versions (max `20261118191206`), so file numbers are repo-only.

## Verdict on the four design ideas

| Idea | Verdict |
|---|---|
| Keep ids equal | **Confirm.** `file_id = item_id` for all 875 rows and for every new upload; pages, maps and helper runs need no data rewrite and keep the column name `item_id`. Equality is an accident of history, never an invariant: all new code goes through `workspace_items.file_id`, and tests must include a pointer with `item_id ≠ file_id`. Retarget the three FKs late (migration 222); nothing in Phase 2 needs it. |
| Expand → switch → contract | **Confirm, with one change.** The "double write" is a pair of DB triggers installed by the migration, not application code. Every step after the migration is then a flag, and no code/migration order can go wrong. |
| What stays on the pointer | **(a), improved.** A trigger on `user_files` keeps `title`, `summary` (and its two bookkeeping columns), the whole `metadata`, `storage_path` and `document_id` on every pointer, permanently. Only `content_md` leaves the pointer, at contract. Reject (b): 13 writer sites in two codebases plus three scheduled jobs, not atomic, and a missed site drifts silently. Reject (c): all ~33 conversation-scoped queries would change. With (a), 17 "lists pointers" sites never change. |
| `metadata jsonb` plus real columns | **Confirm, with a twist.** `user_files.metadata` stays the complete blob, byte-equal to today's. The filter/sort columns are `GENERATED ALWAYS … STORED` from it, so no writer maintains them and they cannot drift. Splitting keys out of the blob would break byte-identity of the pointer copy. |

A read view (`workspace_items` joined to `user_files`) was considered and rejected. Its column list freezes at creation, so every later `ALTER TABLE workspace_items ADD COLUMN` would silently go missing from the `select("*")` API paths. Migration 129a shows the repo has already been burned by view recreation losing `security_invoker`.

---

## A. Inventory

Step codes: **S2** = read switch (`USER_FILES_READ_ENABLED`), **S3** = write switch (`USER_FILES_WRITE_ENABLED`), **C** = contract (Phase 4), **P3** = Phase 3 design, **—** = never changes. All agent paths are under `C:\Programming\LUNA_AI_my_files\agents\`, backend paths under `C:\Programming\LUNA_AI_my_files\backend\app\`.

### A1. Reads content (text, summary, pages, sections)

| File:line — function | Columns / keys | Scope | Step |
|---|---|---|---|
| `router\context.py:325-421` `_load_workspace_item_summaries` | `item_id, wi_seq, kind, title, summary, content_md` (only as the no-summary fallback, 1,500/6,000 chars), `created_at`, `metadata->>doc_type`, `metadata->reading_corrections` | conversation + user | S2 |
| `orchestrator.py:306-362` `_load_attached_items` (callers 1283, 1507, 1711, 2067, 3929) | `item_id, wi_seq, kind, title, content_md, summary, metadata` | item + user + conversation | S2 |
| `tool_repository\unfold_workspace_item.py:318-353` `_fetch_item` | `title, content_md` (+ lock columns) | item + user | S2 |
| `writer_planner\walkers.py:42-87` `_fetch_workspace_items_sync` | `title, content_md, word_count, summary, doc_type, wi_seq, kind` | item ids + conversation | S2 |
| `services\writer_planner_context.py:99-110` `load_writer_planner_context` | `title, summary, word_count, doc_type, wi_seq, created_at` | conversation | S2 |
| `memory\item_analyzer\runner.py:493-567` `_load_workspace_items` | `title, content_md, word_count, wi_seq` | item ids + user | S2 |
| `memory\agent.py:218-281` `_load_compaction_context` | `title, summary, doc_type` (`content_md` is used for `convo_context` only) | conversation + user | S2 |
| `memory\agent.py:316-420` `resummarize_dirty_items` | `content_md` (length), `summary, summary_source_length, metadata.summary_attempt` | conversation | S2 |
| `memory\summarize.py:413-420` fetch in `summarize_workspace_item` | `*` | item | S2 |
| `memory\attachment_segmenter\runner.py:406-415` `_load_item`, `:661-668` `_load_items`, `:785-809` `_awaiting_summary` | `title, summary, content_md, metadata.doc_type, created_at, deleted_at, wi_seq` | item | S2 |
| `memory\attachment_segmenter\bundles.py:159-186` `message_members`, `:189-197` `_item_row` | `content_md` (length only), `wi_seq, created_at, deleted_at, kind, user_id` | message / item | S2 |
| `memory\attachment_segmenter\store.py:112-121` `_content_lengths` | `item_id, content_md` (length only) | item ids | S2 → `user_files.content_chars` |
| `memory\attachment_segmenter\store.py:43-55, 58-80, 124-216` `load_pages`, `map_state`, `load_segment_maps` | pages, maps, segments by `item_id` | item | — in Phase 2; P3 passes file ids |
| `utils\attachment_sections.py:51-103` `wait_and_load_sections` | helper wait + maps, keyed by `item_id` | item ids | — ; P3 maps pointer → file here |
| `memory\template_ingester\runner.py:251-285` | `title, content_md` | item + user | S2 |
| `services\workspace_service.py:222-266, 269-305, 308-364` list and get | `*` (the pane shows `content_md` from the list) | conversation / case / item | S2 |
| `services\files_service.py:346-376, 409-429, 517-565, 585-631, 722-781` | see D | user | S2 |
| `services\summary_sweeper.py:67-80` | `item_id, content_md, metadata, created_at` filtered `summary IS NULL` | all users | C (second scan on `user_files`) |

Pure consumers that take what the loaders return and need no change:
- `deep_search_v4\shared\case_documents.py`
- `deep_search_v4\orchestrator.py:1354-1384`
- `deep_search_v4\planner\prompts.py` (`_render_attached_items`)
- `utils\attachment_delivery.py`
- `utils\history.py:77-166, 340+`
- `writer\exhibits.py`
- `simple_search\runner.py:1017-1045`

### A2. Lists pointers (per-conversation fields plus small fields) — no change, ever

| File:line — function | Fields |
|---|---|
| `orchestrator.py:439-467` `_load_wi_provenance` | `item_id, wi_seq, kind, title` (history tags) |
| `orchestrator.py:273-303` `_owned_item_exists` | `item_id, user_id` |
| `orchestrator.py:365-391` `_load_reply_attachment_ids`; `:569-581` | `message_attachments` |
| `router\context.py:515-557` `_attach_message_attachments` | `message_attachments` |
| `router\router.py:803-853` `list_workspace_items` tool | `title, kind, metadata.subtype, wi_seq, created_at` |
| `memory\convo_memory_writer\runner.py:146-165` `_load_item_titles` | `title` |
| `services\message_service.py:335-351` `list_messages` | embed `workspace_items(title, kind, metadata)` |
| `services\message_service.py:517-544` `_insert_attachment_links` | `item_id` by user; writes `message_attachments` |
| `memory\attachment_segmenter\runner.py:110-172` `_load_user_words`; `bundles.py:126-146` `primary_messages` | `message_attachments` by `document_id` |
| `services\workspace_service.py:439-481` `update_visibility` | `is_visible` |
| `writer\publisher.py:95-118` | `deleted_at` by item |
| `api\workspace.py:116-178` `_to_response` | maps whatever row it is given |

Same table, never an attachment (no change):
- `orchestrator.py:240-270, 696-748, 808-898`
- `memory\agent.py:707`
- `memory\artifact_analyzer\context.py:87`
- `artifact_editor\agent.py:295-306`
- `tool_repository\edit_artifact.py:140`, `edit_supabase_md.py`, `save_memo.py:126`
- `utils\reading_corrections.py:124, 144`
- `writer\lock.py:40`
- `base\context.py:84`
- `services\blog_service.py:736`, `library_item_service.py:707`, `context_service.py:142`, `memory_md_service.py:29, 83`
- `api\deepsearch_api\service.py:485`

`services\workspace_context.py:119-131` reads attachments but has no live caller.

### A3. Writes content

| File:line — function | Writes | Step |
|---|---|---|
| `memory\ocr_extractor\runner.py:69-99` `_mark_status` | `metadata.ocr_status`, `ocr_error`, as read-merge-write of a snapshot taken at the start of the run (up to 10 minutes old) | S3 |
| `memory\ocr_extractor\runner.py:569-575` | `content_md` + `metadata{ocr_status, ocr_pages, ocr_chars, ocr_partial, ocr_page_total, ocr_page_ranges, ocr_text_layer_ranges, ocr_lost_ranges}` | S3 |
| `memory\ocr_extractor\runner.py:102-141` `_store_pages` | `attachment_pages` upsert | — ; P3 file id |
| `memory\summarize.py:146-173` `_mark_attempt` | `metadata.summary_attempt` | S3 (attachment kind only) |
| `memory\summarize.py:300-350` `_persist_attachment_summary` | `title, summary, summary_updated_at, summary_source_length, metadata.artifact_summary{…, context_link}, metadata.doc_type` | S3 |
| `memory\attachment_segmenter\store.py:88-104` `write_segment_map` | maps + segments | — ; P3 file id |
| `helpers\status.py:120-222` `SupabaseHelperRunStore` | `helper_runs (item_id, helper)` | — ; P3 file id |
| `C:\Programming\LUNA_AI_my_files\scripts\my_files_type_backfill.py:207-216` | `metadata.doc_type` | S3 |

`memory\summarize.py:181-213` `_persist_summary` is the generic flow and never handles attachments.

### A4. Lifecycle

| File:line — function | What | Step |
|---|---|---|
| `services\workspace_service.py:125-219` `create_workspace_item` (insert at 192); creators `:702-711`, `:824-833`, `api\workspace.py:947-957` | creates the row; metadata `filename, mime_type, file_size_bytes, upload_status, declared_*, upload_url_expires_at, upload_init_at, page_count, legacy_single_shot, linked_from_case_documents` | — in Phase 2 (a trigger creates the file) |
| `workspace_service.py:742-749` (legacy promote), `:844-923` `finalize_attachment_upload` | `metadata.upload_status`, `upload_finalized_at` | S3 |
| `workspace_service.py:926-996` `cancel_attachment_upload` | `deleted_at`, `metadata.upload_status`, `upload_cancelled_at`, storage wipe | S3 |
| `workspace_service.py:541-612` `delete_workspace_item` | `deleted_at`; storage object deleted at 586-594 (`storage_path` is left set) | S3; rule changes in P3 |
| `services\upload_reconciler.py:134-179, 288-381, 438-468` | scans every attachment older than 24 h and filters `upload_status` in Python; promotes or soft-deletes | S3 |
| `services\attachment_cleanup.py:100-218` | `storage_path → NULL`, `metadata.original_purged_at`, `original_purge_reason`, then storage remove | S3 |
| `memory\ocr_extractor\runner.py:289-313` `has_unread_attachments`, `:402-432` candidates | `storage_path`, `metadata.ocr_status / upload_status / mime_type / page_count / file_size_bytes / filename` | S2 |
| `services\message_service.py:547-590` `_attachment_page_counts` | `kind`, `metadata.ocr_status`, `metadata.page_count` (the page gate) | S2 |
| `shared\quota\__init__.py:286-297` `ocr_budget`; `runner.py:597-605` `record_call(pages_used)` | ledger only; no attachment row is read | — |
| `api\workspace.py:1096-1194` `get_workspace_file_url` | `storage_path`, `document_id → case_documents`, `metadata.original_purged_at` | — (pointer copy) |
| `services\files_service.py:639-714` `hide_library_file` | `metadata.library_hidden_at`, read-merge-write | S2 |
| `services\account_purge_service.py:53, 76` | storage prefix `general/{user_id}`; RPC `purge_user_data` | migration 221 |
| `C:\Programming\LUNA_AI_my_files\scripts\nuke_account.py:123-145` `CASCADING_TABLES` | add `("user_files", "user_id")` right after `workspace_items` | with step 3 |
| `services\conversation_service.py:366-386` `delete_conversation` | soft-deletes the conversation only | — |
| `api\internal_webhooks.py:114-135` | calls `summarize_workspace_item` | — |

### A5. Masking

Every encode point works on loaded values, so nothing changes:
- `orchestrator.py:394-436`
- `router\context.py:612-624`
- `unfold_workspace_item.py:936-959`
- `utils\attachment_sections.py:29-48`
- `memory\summarize.py:90-131`
- `attachment_segmenter\runner.py:185-212`

Invariant to state in the table comment: `user_files.title / content_md / summary` are stored real.

### A6. Dev tools

These are not production paths; update them at contract:
- `scripts\segmentation_preview.py:79, 88`
- `scripts\e2e_segmentation_review.py:150, 370, 387`
- `scripts\my_files_preview.py:168`
- `scripts\backfill_compaction_summaries.py:252, 317, 462`

### A7. SQL objects touching attachment rows

- The seven triggers listed above.
- CHECK `workspace_content_shape` (026 / 125).
- The three cascading FKs plus `message_attachments`.
- `purge_user_data(uuid)` (090).
- The inert RLS policies `artifacts_select / insert / update / delete` (019, revoked by 195).

---

## B. Migrations

Preflight before each (catalog only): confirm no `user_files` relation, no `workspace_items.file_id` column, and that the FK names above are unchanged. Coordinate numbers: 219/219b are already on master as trial limits, so the unmerged seg-v5.2 work must renumber. Reserve 220–223 for this plan.

### 220 `220_user_files.sql` — the table (inert)

Deploy order: any time. No code touches it until a flag is on. Rollback: `DROP TABLE public.user_files CASCADE; DROP FUNCTION public.user_file_patch(uuid,jsonb,jsonb,uuid), public.user_files_hide(uuid,uuid[]), public.user_files_invalidate_summary(), public.try_timestamptz(text);`

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.user_files (
    file_id               uuid        NOT NULL DEFAULT gen_random_uuid(),
    user_id               uuid        NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    title                 text        NOT NULL,
    content_md            text        NOT NULL DEFAULT '',      -- extracted text, stored REAL
    summary               text,
    summary_source_length integer,
    summary_updated_at    timestamptz,
    metadata              jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- the COMPLETE blob, as on workspace_items today
    storage_path          text,                                  -- NULL once the original is purged
    document_id           uuid        REFERENCES public.case_documents(document_id),
    origin                text        NOT NULL DEFAULT 'chat',   -- 'library' = uploaded into «الملفات» (Phase 3)
    library_hidden_at     timestamptz,                           -- «حذف من الملفات»; NULL = listed
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
    CONSTRAINT user_files_file_owner_uq UNIQUE (file_id, user_id),   -- target of the same-owner FK
    CONSTRAINT user_files_origin_chk CHECK (origin IN ('chat', 'library'))
);

CREATE INDEX IF NOT EXISTS user_files_user_created_idx ON public.user_files (user_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS user_files_user_size_idx    ON public.user_files (user_id, file_size_bytes) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS user_files_uploading_idx    ON public.user_files (created_at) WHERE upload_status = 'uploading' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS user_files_original_idx     ON public.user_files (created_at) WHERE storage_path IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS user_files_document_id_idx  ON public.user_files (document_id) WHERE document_id IS NOT NULL;

-- Same rule as workspace_items_invalidate_summary (030): new text => summary is dirty.
CREATE OR REPLACE FUNCTION public.user_files_invalidate_summary()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF NEW.content_md IS DISTINCT FROM OLD.content_md THEN
        NEW.summary := NULL; NEW.summary_source_length := NULL; NEW.summary_updated_at := NULL;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_user_files_invalidate_summary ON public.user_files;
CREATE TRIGGER trg_user_files_invalidate_summary BEFORE UPDATE ON public.user_files
    FOR EACH ROW EXECUTE FUNCTION public.user_files_invalidate_summary();
DROP TRIGGER IF EXISTS trg_user_files_updated_at ON public.user_files;
CREATE TRIGGER trg_user_files_updated_at BEFORE UPDATE ON public.user_files
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();

CREATE OR REPLACE FUNCTION public.try_timestamptz(p text)
RETURNS timestamptz LANGUAGE plpgsql STABLE SET search_path = public AS $$
BEGIN RETURN p::timestamptz; EXCEPTION WHEN others THEN RETURN NULL; END $$;

-- The one way application code writes a file. metadata is MERGED in the statement
-- (no read-modify-write), so two writers can no longer drop each other's keys.
CREATE OR REPLACE FUNCTION public.user_file_patch(
    p_file_id uuid, p_fields jsonb DEFAULT '{}'::jsonb,
    p_metadata_patch jsonb DEFAULT '{}'::jsonb, p_user_id uuid DEFAULT NULL)
RETURNS uuid LANGUAGE sql VOLATILE SECURITY INVOKER SET search_path = public AS $$
    UPDATE public.user_files f SET
        title        = CASE WHEN p_fields ? 'title'        THEN p_fields->>'title' ELSE f.title END,
        content_md   = CASE WHEN p_fields ? 'content_md'   THEN COALESCE(p_fields->>'content_md', '') ELSE f.content_md END,
        summary      = CASE WHEN p_fields ? 'summary'      THEN p_fields->>'summary' ELSE f.summary END,
        summary_source_length = CASE WHEN p_fields ? 'summary_source_length'
                                     THEN (p_fields->>'summary_source_length')::integer ELSE f.summary_source_length END,
        summary_updated_at    = CASE WHEN p_fields ? 'summary_updated_at'
                                     THEN (p_fields->>'summary_updated_at')::timestamptz ELSE f.summary_updated_at END,
        storage_path = CASE WHEN p_fields ? 'storage_path' THEN p_fields->>'storage_path' ELSE f.storage_path END,
        deleted_at   = CASE WHEN p_fields ? 'deleted_at'   THEN (p_fields->>'deleted_at')::timestamptz ELSE f.deleted_at END,
        metadata     = f.metadata || COALESCE(p_metadata_patch, '{}'::jsonb)
    WHERE f.file_id = p_file_id AND (p_user_id IS NULL OR f.user_id = p_user_id)
    RETURNING f.file_id;
$$;

-- «حذف من الملفات»: one statement, first stamp kept. The metadata key is the copy
-- the pre-switch reader looks at; the column is the truth.
CREATE OR REPLACE FUNCTION public.user_files_hide(p_user_id uuid, p_file_ids uuid[])
RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = public AS $$
DECLARE v_n integer;
BEGIN
    UPDATE public.user_files
       SET library_hidden_at = now(),
           metadata = metadata || jsonb_build_object('library_hidden_at', now())
     WHERE user_id = p_user_id AND file_id = ANY(p_file_ids)
       AND library_hidden_at IS NULL AND deleted_at IS NULL;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
END $$;

ALTER TABLE public.user_files ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_files_select_own ON public.user_files;
CREATE POLICY user_files_select_own ON public.user_files FOR SELECT TO authenticated
    USING (user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid())));
REVOKE ALL ON TABLE public.user_files FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.user_files TO service_role;
REVOKE ALL ON FUNCTION public.user_file_patch(uuid, jsonb, jsonb, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.user_files_hide(uuid, uuid[])            FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.user_file_patch(uuid, jsonb, jsonb, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.user_files_hide(uuid, uuid[])            TO service_role;

COMMENT ON TABLE public.user_files IS
    'One row per uploaded file per user (220): extracted text, summary, type, original. Text stored REAL. A conversation''s use of a file is a workspace_items row (kind=attachment) with file_id. Service-role only: RLS on, owner SELECT policy, no client grants.';

DO $$
BEGIN
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.user_files'::regclass) THEN
        RAISE EXCEPTION '220: RLS is off on user_files'; END IF;
    IF EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = 'public.user_files'::regclass AND polcmd <> 'r') THEN
        RAISE EXCEPTION '220: user_files must have no client write policy'; END IF;
    IF EXISTS (SELECT 1 FROM information_schema.role_table_grants
                WHERE table_schema='public' AND table_name='user_files' AND grantee IN ('anon','authenticated','PUBLIC')) THEN
        RAISE EXCEPTION '220: client-role privileges on user_files'; END IF;
    IF (SELECT count(DISTINCT privilege_type) FROM information_schema.role_table_grants
         WHERE table_schema='public' AND table_name='user_files' AND grantee='service_role'
           AND privilege_type IN ('SELECT','INSERT','UPDATE','DELETE')) <> 4 THEN
        RAISE EXCEPTION '220: service_role must hold DML on user_files'; END IF;
    IF (SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='user_files'
          AND is_generated = 'ALWAYS') <> 8 THEN
        RAISE EXCEPTION '220: expected 8 generated columns on user_files'; END IF;
    IF has_function_privilege('authenticated', 'public.user_file_patch(uuid,jsonb,jsonb,uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION '220: user_file_patch is callable by clients'; END IF;
    RAISE NOTICE '220 OK: user_files created, RLS on, client roles revoked, service_role DML, RPCs service-only';
END $$;

COMMIT;
NOTIFY pgrst, 'reload schema';
```

Notes:
- The generated columns are not recomputed on metadata-only updates.
- `content_chars` and `word_count` reproduce `metadata.ocr_chars` and `workspace_items.word_count` exactly (verified live on all rows).
- There is no index on `filename` on purpose: `files_service` deliberately compares the name in Python so it never travels in a request URL.
- `user_file_patch` has no fail-safe default for a JSON-null `title`; callers never send one.

### 221 `221_user_files_backfill_and_sync.sql` — link, copy, sync

Deploy order: after 220 and before any `USER_FILES_*` flag is on. It is independent of code deploys, because flags are off and the schema probe in C guards the rest. Run in a quiet window. It is three transactions: only the first blocks readers (milliseconds), and only the third blocks writers (sub-second).

```sql
-- ── Tx A: the link column (ACCESS EXCLUSIVE, metadata-only, no rewrite) ──────
BEGIN;
SET LOCAL lock_timeout = '3s';
ALTER TABLE public.workspace_items ADD COLUMN IF NOT EXISTS file_id uuid;
DO $$ BEGIN
    ALTER TABLE public.workspace_items
        ADD CONSTRAINT workspace_items_file_kind_chk CHECK (file_id IS NULL OR kind = 'attachment');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
COMMENT ON COLUMN public.workspace_items.file_id IS
    'For kind=attachment: the user_files row this conversation item points at (221). Equal to item_id for uploads made in a conversation; never rely on that.';
COMMIT;

-- ── Tx B: bulk copy (reads workspace_items only; nobody waits) ──────────────
BEGIN;
SET LOCAL statement_timeout = '120s';
INSERT INTO public.user_files
    (file_id, user_id, title, content_md, summary, summary_source_length, summary_updated_at,
     metadata, storage_path, document_id, origin, library_hidden_at, created_at, updated_at, deleted_at)
SELECT wi.item_id, wi.user_id, wi.title, COALESCE(wi.content_md, ''), wi.summary,
       wi.summary_source_length, wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb),
       wi.storage_path, wi.document_id, 'chat',
       public.try_timestamptz(wi.metadata->>'library_hidden_at'),
       wi.created_at, wi.updated_at, wi.deleted_at
  FROM public.workspace_items wi
 WHERE wi.kind = 'attachment'
ON CONFLICT (file_id) DO NOTHING;
COMMIT;

-- ── Tx C: lock writers, catch up, link, install the sync, verify ────────────
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
LOCK TABLE public.workspace_items IN SHARE ROW EXCLUSIVE MODE;   -- writers wait; readers do not

CREATE TEMP TABLE _221_before ON COMMIT DROP AS
SELECT md5(string_agg(md5(ROW(item_id, user_id, conversation_id, case_id, message_id, wi_seq, kind, created_by,
       title, content_md, summary, summary_source_length, summary_updated_at, metadata, storage_path,
       document_id, is_visible, word_count, created_at, updated_at, deleted_at)::text), '' ORDER BY item_id)) AS h
  FROM public.workspace_items WHERE kind = 'attachment';

-- Catch-up for rows written during Tx B. user_files' own triggers off: a copy, not an edit.
ALTER TABLE public.user_files DISABLE TRIGGER USER;
INSERT INTO public.user_files
    (file_id, user_id, title, content_md, summary, summary_source_length, summary_updated_at,
     metadata, storage_path, document_id, origin, library_hidden_at, created_at, updated_at, deleted_at)
SELECT wi.item_id, wi.user_id, wi.title, COALESCE(wi.content_md, ''), wi.summary,
       wi.summary_source_length, wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb),
       wi.storage_path, wi.document_id, 'chat',
       public.try_timestamptz(wi.metadata->>'library_hidden_at'),
       wi.created_at, wi.updated_at, wi.deleted_at
  FROM public.workspace_items wi WHERE wi.kind = 'attachment'
ON CONFLICT (file_id) DO NOTHING;
UPDATE public.user_files f SET
       title = wi.title, content_md = COALESCE(wi.content_md, ''), summary = wi.summary,
       summary_source_length = wi.summary_source_length, summary_updated_at = wi.summary_updated_at,
       metadata = COALESCE(wi.metadata, '{}'::jsonb), storage_path = wi.storage_path,
       document_id = wi.document_id, updated_at = wi.updated_at, deleted_at = wi.deleted_at,
       library_hidden_at = COALESCE(f.library_hidden_at, public.try_timestamptz(wi.metadata->>'library_hidden_at'))
  FROM public.workspace_items wi
 WHERE wi.item_id = f.file_id AND wi.kind = 'attachment'
   AND (f.title, f.content_md, f.summary, f.summary_source_length, f.summary_updated_at, f.metadata,
        f.storage_path, f.document_id, f.updated_at, f.deleted_at)
       IS DISTINCT FROM
       (wi.title, COALESCE(wi.content_md, ''), wi.summary, wi.summary_source_length, wi.summary_updated_at,
        COALESCE(wi.metadata, '{}'::jsonb), wi.storage_path, wi.document_id, wi.updated_at, wi.deleted_at);
ALTER TABLE public.user_files ENABLE TRIGGER USER;

-- Link. updated_at must not move: the web viewer footer and the mobile change stamp read it.
ALTER TABLE public.workspace_items DISABLE TRIGGER update_artifacts_updated_at;
UPDATE public.workspace_items SET file_id = item_id WHERE kind = 'attachment' AND file_id IS NULL;
ALTER TABLE public.workspace_items ENABLE TRIGGER update_artifacts_updated_at;

-- A pointer can only ever link a file of the SAME user (composite FK).
ALTER TABLE public.workspace_items
    ADD CONSTRAINT workspace_items_file_owner_fkey
    FOREIGN KEY (file_id, user_id) REFERENCES public.user_files (file_id, user_id) NOT VALID;
ALTER TABLE public.workspace_items VALIDATE CONSTRAINT workspace_items_file_owner_fkey;
CREATE INDEX IF NOT EXISTS workspace_items_file_id_idx ON public.workspace_items (file_id) WHERE file_id IS NOT NULL;

-- (1) An attachment row is born with a file (same uuid), or must point at one of its owner's.
CREATE OR REPLACE FUNCTION public.workspace_items_file_link()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF NEW.item_id IS NULL THEN NEW.item_id := gen_random_uuid(); END IF;
    IF NEW.file_id IS NULL THEN
        NEW.file_id := NEW.item_id;
        INSERT INTO public.user_files
            (file_id, user_id, title, content_md, summary, summary_source_length, summary_updated_at,
             metadata, storage_path, document_id, origin, created_at, updated_at, deleted_at)
        VALUES (NEW.item_id, NEW.user_id, NEW.title, COALESCE(NEW.content_md, ''), NEW.summary,
                NEW.summary_source_length, NEW.summary_updated_at, COALESCE(NEW.metadata, '{}'::jsonb),
                NEW.storage_path, NEW.document_id, 'chat',
                COALESCE(NEW.created_at, now()), COALESCE(NEW.updated_at, now()), NEW.deleted_at)
        ON CONFLICT (file_id) DO NOTHING;
    ELSIF NOT EXISTS (SELECT 1 FROM public.user_files f
                       WHERE f.file_id = NEW.file_id AND f.user_id = NEW.user_id) THEN
        RAISE EXCEPTION 'workspace_items.file_id % is not a file of this user', NEW.file_id
              USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_workspace_items_file_link ON public.workspace_items;
CREATE TRIGGER trg_workspace_items_file_link
    BEFORE INSERT ON public.workspace_items FOR EACH ROW
    WHEN (NEW.kind = 'attachment')
    EXECUTE FUNCTION public.workspace_items_file_link();

-- (2) pointer -> file: a write by code that still writes workspace_items reaches the file.
CREATE OR REPLACE FUNCTION public.workspace_items_mirror_file()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN RETURN NULL; END IF;   -- our own file->pointer write
    UPDATE public.user_files f SET
           title = NEW.title, content_md = COALESCE(NEW.content_md, ''), summary = NEW.summary,
           summary_source_length = NEW.summary_source_length, summary_updated_at = NEW.summary_updated_at,
           metadata = COALESCE(NEW.metadata, '{}'::jsonb), storage_path = NEW.storage_path,
           document_id = NEW.document_id, deleted_at = NEW.deleted_at,
           library_hidden_at = COALESCE(f.library_hidden_at,
                                        public.try_timestamptz(NEW.metadata->>'library_hidden_at'))
     WHERE f.file_id = NEW.file_id
       AND (f.title, f.content_md, f.summary, f.summary_source_length, f.summary_updated_at, f.metadata,
            f.storage_path, f.document_id, f.deleted_at)
           IS DISTINCT FROM
           (NEW.title, COALESCE(NEW.content_md, ''), NEW.summary, NEW.summary_source_length,
            NEW.summary_updated_at, COALESCE(NEW.metadata, '{}'::jsonb), NEW.storage_path,
            NEW.document_id, NEW.deleted_at);
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS trg_workspace_items_mirror_file ON public.workspace_items;
CREATE TRIGGER trg_workspace_items_mirror_file
    AFTER UPDATE ON public.workspace_items FOR EACH ROW
    WHEN (NEW.kind = 'attachment' AND NEW.file_id IS NOT NULL AND NEW.file_id = NEW.item_id)
    EXECUTE FUNCTION public.workspace_items_mirror_file();

-- (3) file -> pointers: every conversation item of the file carries the file's small fields.
CREATE OR REPLACE FUNCTION public.user_files_sync_pointers()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN RETURN NULL; END IF;   -- our own pointer->file write
    UPDATE public.workspace_items wi SET
           title = NEW.title, content_md = NEW.content_md, summary = NEW.summary,
           summary_source_length = NEW.summary_source_length, summary_updated_at = NEW.summary_updated_at,
           metadata = NEW.metadata, storage_path = NEW.storage_path, document_id = NEW.document_id
     WHERE wi.file_id = NEW.file_id AND wi.kind = 'attachment'
       AND (wi.title, COALESCE(wi.content_md, ''), wi.summary, wi.summary_source_length,
            wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb), wi.storage_path, wi.document_id)
           IS DISTINCT FROM
           (NEW.title, NEW.content_md, NEW.summary, NEW.summary_source_length, NEW.summary_updated_at,
            NEW.metadata, NEW.storage_path, NEW.document_id);
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS trg_user_files_sync_pointers ON public.user_files;
CREATE TRIGGER trg_user_files_sync_pointers
    AFTER UPDATE ON public.user_files FOR EACH ROW
    EXECUTE FUNCTION public.user_files_sync_pointers();

REVOKE ALL ON FUNCTION public.workspace_items_file_link(), public.workspace_items_mirror_file(),
                       public.user_files_sync_pointers() FROM PUBLIC, anon, authenticated;

-- Account purge: files die in the RPC transaction, AFTER their pointers (FK order).
-- Body = the live one (read 2026-11-19, equals migration 090) + one line.
CREATE OR REPLACE FUNCTION public.purge_user_data(p_user_id uuid)
RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = public AS $$
BEGIN
    DELETE FROM public.retrieval_artifacts WHERE user_id = p_user_id;
    DELETE FROM public.workspace_items     WHERE user_id = p_user_id;
    DELETE FROM public.user_files          WHERE user_id = p_user_id;   -- 221
    DELETE FROM public.conversations       WHERE user_id = p_user_id;
    DELETE FROM public.lawyer_cases        WHERE lawyer_user_id = p_user_id;
    DELETE FROM public.message_feedback    WHERE user_id = p_user_id;
    DELETE FROM public.pii_mappings        WHERE user_id = p_user_id;
    DELETE FROM public.user_templates      WHERE user_id = p_user_id;
    DELETE FROM public.user_preferences    WHERE user_id = p_user_id;
    DELETE FROM public.blog_posts          WHERE owner_user_id = p_user_id;
    DELETE FROM public.paused_runs         WHERE user_id = p_user_id;
    IF to_regclass('public.task_state') IS NOT NULL THEN
        EXECUTE 'DELETE FROM public.task_state WHERE user_id = $1' USING p_user_id;
    END IF;
    UPDATE public.plan_codes
       SET redeemed_by_users = array_remove(redeemed_by_users, p_user_id)
     WHERE p_user_id = ANY(redeemed_by_users);
    RETURN jsonb_build_object('user_id', p_user_id, 'purged', true);
END $$;
REVOKE ALL ON FUNCTION public.purge_user_data(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.purge_user_data(uuid) TO service_role;

-- ── self-checks ──────────────────────────────────────────────────────────────
DO $$
DECLARE v_n bigint; v_src text; v_user uuid; v_item uuid := gen_random_uuid(); v_ok boolean := false;
BEGIN
    -- every attachment has exactly its file; nothing else has one
    SELECT count(*) INTO v_n FROM public.workspace_items
     WHERE (kind = 'attachment' AND file_id IS DISTINCT FROM item_id) OR (kind <> 'attachment' AND file_id IS NOT NULL);
    IF v_n <> 0 THEN RAISE EXCEPTION '221: % rows with a wrong file_id', v_n; END IF;

    -- count check, per user, all and live (owner decision D14)
    SELECT count(*) INTO v_n FROM
        (SELECT user_id, count(*) n, count(*) FILTER (WHERE deleted_at IS NULL) live
           FROM public.workspace_items WHERE kind = 'attachment' GROUP BY 1) a
        FULL JOIN
        (SELECT user_id, count(*) n, count(*) FILTER (WHERE deleted_at IS NULL) live
           FROM public.user_files GROUP BY 1) b USING (user_id)
     WHERE a.n IS DISTINCT FROM b.n OR a.live IS DISTINCT FROM b.live;
    IF v_n <> 0 THEN RAISE EXCEPTION '221: attachment / file counts differ for % user(s)', v_n; END IF;

    -- field parity on every row, including the derived columns agents read
    SELECT count(*) INTO v_n FROM public.workspace_items wi JOIN public.user_files f ON f.file_id = wi.file_id
     WHERE wi.kind = 'attachment'
       AND (wi.user_id, wi.title, COALESCE(wi.content_md, ''), wi.summary, wi.summary_source_length,
            wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb), wi.storage_path, wi.document_id,
            wi.created_at, wi.deleted_at, wi.word_count, wi.metadata->>'doc_type')
           IS DISTINCT FROM
           (f.user_id, f.title, f.content_md, f.summary, f.summary_source_length, f.summary_updated_at,
            f.metadata, f.storage_path, f.document_id, f.created_at, f.deleted_at, f.word_count, f.doc_type);
    IF v_n <> 0 THEN RAISE EXCEPTION '221: % file row(s) differ from their attachment row', v_n; END IF;

    -- the pointer rows themselves did not change (updated_at included)
    IF (SELECT h FROM _221_before) IS DISTINCT FROM
       (SELECT md5(string_agg(md5(ROW(item_id, user_id, conversation_id, case_id, message_id, wi_seq, kind, created_by,
               title, content_md, summary, summary_source_length, summary_updated_at, metadata, storage_path,
               document_id, is_visible, word_count, created_at, updated_at, deleted_at)::text), '' ORDER BY item_id))
          FROM public.workspace_items WHERE kind = 'attachment') THEN
        RAISE EXCEPTION '221: attachment rows changed during the migration';
    END IF;

    -- pages / maps / helper runs all have a file under the same id (222 depends on it)
    SELECT (SELECT count(*) FROM public.attachment_pages p WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = p.item_id))
         + (SELECT count(*) FROM public.attachment_segment_maps m WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = m.item_id))
         + (SELECT count(*) FROM public.helper_runs h WHERE NOT EXISTS (SELECT 1 FROM public.user_files f WHERE f.file_id = h.item_id))
      INTO v_n;
    IF v_n <> 0 THEN RAISE EXCEPTION '221: % page / map / helper rows without a file', v_n; END IF;

    -- triggers in place and enabled (the updated_at trigger was switched off above)
    SELECT count(*) INTO v_n FROM pg_trigger
     WHERE NOT tgisinternal AND tgenabled = 'O'
       AND ((tgrelid = 'public.workspace_items'::regclass
             AND tgname IN ('update_artifacts_updated_at', 'trg_workspace_items_file_link', 'trg_workspace_items_mirror_file'))
         OR (tgrelid = 'public.user_files'::regclass
             AND tgname IN ('trg_user_files_sync_pointers', 'trg_user_files_invalidate_summary', 'trg_user_files_updated_at')));
    IF v_n <> 6 THEN RAISE EXCEPTION '221: expected 6 enabled triggers, found %', v_n; END IF;

    -- purge order: pointers before files
    SELECT prosrc INTO v_src FROM pg_proc WHERE oid = 'public.purge_user_data(uuid)'::regprocedure;
    IF position('public.user_files' IN v_src) = 0
       OR position('public.workspace_items' IN v_src) > position('public.user_files' IN v_src) THEN
        RAISE EXCEPTION '221: purge_user_data must delete workspace_items before user_files';
    END IF;

    -- behaviour, on a row that never commits
    SELECT user_id INTO v_user FROM public.users LIMIT 1;
    IF v_user IS NOT NULL THEN
        BEGIN
            INSERT INTO public.workspace_items (item_id, user_id, kind, created_by, title, storage_path, metadata)
            VALUES (v_item, v_user, 'attachment', 'user', '221 self-test', '221/self-test',
                    '{"filename":"t.pdf","mime_type":"application/pdf","file_size_bytes":10,"upload_status":"uploading"}');
            IF NOT EXISTS (SELECT 1 FROM public.user_files WHERE file_id = v_item AND filename = 't.pdf'
                              AND file_size_bytes = 10 AND upload_status = 'uploading')
               OR (SELECT file_id FROM public.workspace_items WHERE item_id = v_item) IS DISTINCT FROM v_item THEN
                RAISE EXCEPTION '221: an attachment insert did not create its file'; END IF;
            UPDATE public.workspace_items SET content_md = 'abc def', metadata = metadata || '{"ocr_status":"done"}'
             WHERE item_id = v_item;
            IF NOT EXISTS (SELECT 1 FROM public.user_files WHERE file_id = v_item AND content_md = 'abc def'
                              AND content_chars = 7 AND word_count = 2 AND ocr_status = 'done') THEN
                RAISE EXCEPTION '221: pointer -> file mirror failed'; END IF;
            PERFORM public.user_file_patch(v_item, '{"summary":"s","title":"t2"}'::jsonb, '{"doc_type":"contract"}'::jsonb);
            IF NOT EXISTS (SELECT 1 FROM public.workspace_items WHERE item_id = v_item AND summary = 's'
                              AND title = 't2' AND metadata->>'doc_type' = 'contract'
                              AND metadata->>'ocr_status' = 'done') THEN
                RAISE EXCEPTION '221: file -> pointer sync failed'; END IF;
            PERFORM public.user_file_patch(v_item, '{"content_md":"x"}'::jsonb);
            IF EXISTS (SELECT 1 FROM public.workspace_items wi JOIN public.user_files f ON f.file_id = wi.file_id
                        WHERE wi.item_id = v_item AND (wi.content_md <> 'x' OR wi.summary IS NOT NULL
                              OR f.summary IS NOT NULL OR wi.word_count <> 1)) THEN
                RAISE EXCEPTION '221: new text must clear the summary on both rows'; END IF;
            RAISE EXCEPTION 'undo' USING ERRCODE = 'LS221';
        EXCEPTION WHEN SQLSTATE 'LS221' THEN v_ok := true;
        END;
        IF NOT v_ok OR EXISTS (SELECT 1 FROM public.user_files WHERE file_id = v_item) THEN
            RAISE EXCEPTION '221: self-test left a row behind'; END IF;
    END IF;
    RAISE NOTICE '221 OK: every attachment has its file, rows equal, sync both ways, purge covers files';
END $$;

COMMIT;
NOTIFY pgrst, 'reload schema';
```

**Locks**
- Tx A takes ACCESS EXCLUSIVE on `workspace_items` for a catalog change and a 3,566-row scan. `lock_timeout` 3 s makes it fail fast rather than queue every turn behind it; rerun on timeout.
- Tx C takes SHARE ROW EXCLUSIVE: readers continue, writers wait for the catch-up, an 875-row update, the FK validation and one index build.
- Nothing rewrites the table. The 875-row update reuses the TOAST values, because `content_md` is not in the SET list.

**Idempotent**
- Every statement is re-runnable.
- The `ADD CONSTRAINT … fkey` needs a `DO … EXCEPTION WHEN duplicate_object` guard if the file is ever run twice. Add it at build.

**Rollback** — first set both flags off and redeploy:

```sql
BEGIN; SET LOCAL lock_timeout = '5s';
DROP TRIGGER IF EXISTS trg_user_files_sync_pointers    ON public.user_files;
DROP TRIGGER IF EXISTS trg_workspace_items_mirror_file ON public.workspace_items;
DROP TRIGGER IF EXISTS trg_workspace_items_file_link   ON public.workspace_items;
DROP FUNCTION IF EXISTS public.user_files_sync_pointers(), public.workspace_items_mirror_file(), public.workspace_items_file_link();
-- purge_user_data: re-create with the 090 body (the block above minus the user_files line)
ALTER TABLE public.workspace_items DROP CONSTRAINT IF EXISTS workspace_items_file_owner_fkey;
DROP INDEX IF EXISTS public.workspace_items_file_id_idx;
ALTER TABLE public.workspace_items DROP CONSTRAINT IF EXISTS workspace_items_file_kind_chk;
ALTER TABLE public.workspace_items DROP COLUMN IF EXISTS file_id;      -- no rewrite, updated_at untouched
TRUNCATE public.user_files;
COMMIT; NOTIFY pgrst, 'reload schema';
```

No data is lost on rollback: every write made through the file path was copied to the pointer row in the same transaction.

### 222 `222_user_files_dependents.sql` — last Phase-2 step, Phase-3 prerequisite

Deploy order: after both flags are on for everyone and soaked. No code depends on it, because ids are equal. Run as two transactions.

```sql
BEGIN; SET LOCAL lock_timeout = '5s';      -- dropping these FKs takes ACCESS EXCLUSIVE on workspace_items, briefly
ALTER TABLE public.attachment_pages
    DROP CONSTRAINT attachment_pages_item_id_fkey,
    ADD  CONSTRAINT attachment_pages_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.user_files(file_id) ON DELETE CASCADE;
ALTER TABLE public.attachment_segment_maps
    DROP CONSTRAINT attachment_segment_maps_item_id_fkey,
    ADD  CONSTRAINT attachment_segment_maps_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.user_files(file_id) ON DELETE CASCADE;
ALTER TABLE public.helper_runs
    DROP CONSTRAINT helper_runs_item_id_fkey,
    ADD  CONSTRAINT helper_runs_item_id_fkey FOREIGN KEY (item_id) REFERENCES public.user_files(file_id) ON DELETE CASCADE;
ALTER TABLE public.workspace_items
    ADD CONSTRAINT workspace_items_attachment_file_chk CHECK (kind <> 'attachment' OR file_id IS NOT NULL) NOT VALID;
COMMENT ON COLUMN public.attachment_pages.item_id        IS 'user_files.file_id (222). Name kept from 215.';
COMMENT ON COLUMN public.attachment_segment_maps.item_id IS 'user_files.file_id (222). Name kept from 215.';
COMMENT ON COLUMN public.helper_runs.item_id             IS 'user_files.file_id (222). Name kept from 216.';
COMMIT;
BEGIN; ALTER TABLE public.workspace_items VALIDATE CONSTRAINT workspace_items_attachment_file_chk; COMMIT;
```

- Self-check: three cascading FKs onto `user_files`, zero onto `workspace_items` from these three tables.
- Rollback: the same statements pointing back at `workspace_items(item_id)`. That is possible only while every file still has a same-id pointer, so it stops being possible once Phase 3 creates library uploads.
- `attachment_segments` keeps its FK to the map. `message_attachments` stays on `workspace_items`, because a message links to the conversation's item.
- `helper_runs` becomes file-only by this change. All 80 rows are the segmenter today; a future helper on a non-file item would need its own key.

### 223 contract (Phase 4 — design only)

Deploy order is reversed here: the code without the legacy read paths must be live before 223.
- Drop `trg_workspace_items_mirror_file`.
- Make `set_workspace_item_word_count` and `workspace_items_invalidate_summary` return early for `kind='attachment' AND file_id IS NOT NULL`.
- Change `user_files_sync_pointers` to copy `word_count` and stop copying `content_md`.
- Change `workspace_items_file_link` to fill the small fields from the file when `file_id` is given.
- Add `OR file_id IS NOT NULL` to `workspace_content_shape`.
- Then `UPDATE workspace_items SET content_md = NULL WHERE kind='attachment'`, with the `updated_at` trigger disabled.
- Reversible by refilling from `user_files`.

---

## C. Code design

### C1. Module `C:\Programming\LUNA_AI_my_files\shared\files\` (new; importable by `backend/` and `agents/`)

`flags.py`

```python
def schema_ready(supabase) -> bool
    # True once PostgREST answers user_files.select("file_id").limit(1) AND
    # workspace_items.select("file_id").limit(1). Cached for the process when True;
    # re-probed at most once a minute while False (logs one error).
def files_read_enabled(user_id=None, *, supabase=None) -> bool    # USER_FILES_READ_ENABLED / _USERS, and schema_ready
def files_write_enabled(user_id=None, *, supabase=None) -> bool   # USER_FILES_WRITE_ENABLED / _USERS, and schema_ready
def override(*, read: bool | None = None, write: bool | None = None)   # context manager (tests, parity script)
```

Same rule as `agents\utils\segmenter_flags.py::_on_for`: off on error. A `None` user with an allow-list set reads as off.

`store.py`

```python
FILES_TABLE = "user_files"
FILE_COLUMNS = ("title", "content_md", "summary", "summary_source_length", "summary_updated_at",
                "metadata", "storage_path", "document_id", "word_count")
FILE_ALIASES = {"doc_type": "doc_type"}          # select alias -> user_files column

def with_file_id(columns: str, *, user_id=None, supabase=None) -> str
    # columns + ", file_id" when the read switch is on, else columns unchanged.
def overlay_files(supabase, rows: list[dict], *, user_id=None, strict: bool = False) -> list[dict]
    # For every row with a truthy "file_id": replace each key the row already carries that is in
    # FILE_COLUMNS / FILE_ALIASES with the file's value. One user_files select per 100 ids,
    # filtered by user_id when given. Rows without file_id, and every row when the switch is off,
    # come back untouched. strict=False (Phase 2): a failed read keeps the pointer's values and
    # logs ids only. strict=True (from contract): raises.
def load_files(supabase, file_ids, *, columns: str, user_id=None) -> dict[str, dict]
def file_ids_for_items(supabase, item_ids, *, user_id=None) -> dict[str, str]      # item_id -> file_id
def content_chars_for_items(supabase, item_ids, *, user_id=None) -> dict[str, int] # item_id -> content_chars, live files only
def patch_file(supabase, file_id, *, fields=None, metadata_patch=None, user_id=None) -> bool
    # rpc("user_file_patch"); True when a row was written.
def write_attachment(supabase, item_id, *, fields=None, metadata=None, metadata_patch=None,
                     owner_id=None, filters=None) -> Any
    # Switch off: supabase.table("workspace_items").update({**fields, "metadata": metadata})
    #             .eq("item_id", item_id)[.eq(k, v) for filters] — today's statement, unchanged.
    # Switch on : patch_file(file_ids_for_items([item_id])[item_id], fields=fields, metadata_patch=metadata_patch).
def hide_files(supabase, user_id, file_ids) -> int     # rpc("user_files_hide")
```

Never log text, summaries or filenames (the `files_service` rule).

### C2. Flags — `C:\Programming\LUNA_AI_my_files\shared\config.py`, after `MY_FILES_USERS`

```python
# ── Files in their own table — .claude/plans/my_files.md §4 (Phase 2) ────
# Migrations 220 + 221 first. Until PostgREST serves user_files and
# workspace_items.file_id both switches read as OFF (shared/files/flags.py).
# READ : attachment text, summary, title and type are loaded from user_files.
# WRITE: OCR, the summarizer, upload finalize / cancel, the retention sweep and
#        the upload reconciler write user_files; the conversation items follow
#        by trigger. *_USERS: comma-separated users.user_id; empty = everyone.
USER_FILES_READ_ENABLED: bool = False
USER_FILES_READ_USERS: Optional[str] = None
USER_FILES_WRITE_ENABLED: bool = False
USER_FILES_WRITE_USERS: Optional[str] = None
```

### C3. Sync mechanism — why triggers

- **Deploy-order proof.** After 221, both tables are equal whatever code version writes, in either direction and in the same transaction. Code-before-migration is harmless (flags off, plus the probe). Migration-before-code is the normal case.
- **Reversible by flag.** Neither switch moves data.
- **No ping-pong.** The `pg_trigger_depth() > 1` guards stop the loop, and `IS DISTINCT FROM` avoids no-op writes, so `updated_at` moves exactly when it moves today.
- **Enforced in the database.** Pointer and file always have the same owner (composite FK), and a file's `metadata` is merged rather than overwritten.

Invariant to document: an attachment pointer's `metadata` is a read-only copy of the file's. Per-conversation data gets its own `workspace_items` column.

### C4. Step S2 — readers (flag `USER_FILES_READ_ENABLED`)

The pattern at every A1 site is three lines:

```python
cols = files_store.with_file_id(columns, user_id=user_id, supabase=supabase)
rows = <the existing query, with cols>
rows = files_store.overlay_files(supabase, rows, user_id=user_id)
```

Per site:
- `router\context.py:349-366` — pattern as is. `_apply_summary_fallback` and everything after it is unchanged.
- `orchestrator.py:327-359` — collect the row dicts first, call `overlay_files` once, then build the `WorkspaceItemSnapshot`s. Order is preserved.
- `unfold_workspace_item.py:331-347` — overlay the single row (wrap as a list).
- `walkers.py:56-67`, `writer_planner_context.py:99-110`, `item_analyzer\runner.py:519-527`, `memory\agent.py:240-252` and `:340-349`, `template_ingester\runner.py:261-269` — pattern as is.
- `summarize.py:413-420` — `select("*")` already carries `file_id`; overlay with `user_id=row["user_id"]`.
- `attachment_segmenter\runner.py:406-415` and `:661-668`; `bundles.py:173` and `:189-197` — add `file_id` to `_ITEM_COLUMNS` and `_MEMBER_COLUMNS` through `with_file_id`, then overlay.
- `attachment_segmenter\store.py:112-121` — when the switch is on, return `content_chars_for_items(...)`. The live-file filter replaces the item `deleted_at` filter; the two are equal while the mirror runs.
- `workspace_service.py:246-257`, `:286-296`, `:339-348` — overlay after the query. The demo conversation and item (no `user_id` filter) contain no attachment.
- `ocr_extractor\runner.py:294-301` and `:402-410`; `message_service.py:562-568` — overlay `metadata`, `storage_path`, `title`, so the "attempt once" marker is read from the file.
- `files_service.py` — see D.

Nothing in A2 changes.

### C5. Step S3 — writers (flag `USER_FILES_WRITE_ENABLED`)

Each site keeps computing what it computes today and passes both the full dict and its own delta:

| Site | Call |
|---|---|
| `ocr_extractor\runner.py:86-92` | `write_attachment(sb, item_id, metadata=merged, metadata_patch={"ocr_status": status, **(extra or {})}, owner_id=user_id)` — `_mark_status` gains a `user_id` argument |
| `ocr_extractor\runner.py:569-575` | `write_attachment(sb, item_id, fields={"content_md": text}, metadata=merged, metadata_patch={k: merged[k] for k in merged if k.startswith("ocr_")}, owner_id=user_id)` |
| `summarize.py:161-167` | attachment kind only: `metadata_patch={"summary_attempt": md["summary_attempt"]}` |
| `summarize.py:338-344` | `fields={"summary", "summary_updated_at", "summary_source_length"[, "title"]}`, `metadata_patch={"artifact_summary": …[, "doc_type": …]}` |
| `workspace_service.py:742-749`, `:907-914` | `metadata_patch={"upload_status": "ready", "upload_finalized_at": now}`; `updated_at` is dropped on the file path (the trigger sets it) |
| `workspace_service.py:978-985` | file: `fields={"deleted_at": now}`, `metadata_patch={"upload_status": "cancelled", "upload_cancelled_at": now}`; then the pointer `deleted_at` exactly as today |
| `attachment_cleanup.py:181-186` | `fields={"storage_path": None}`, `metadata_patch={"original_purged_at": now, "original_purge_reason": "retention_sweep"}`; same order (row first, then bytes) |
| `upload_reconciler.py:154-166`, `:352-358` | as cancel and promote, with `cancelled_by` / `finalized_by` in the patch |
| `scripts\my_files_type_backfill.py:207-216` | `metadata_patch={"doc_type": …}` |

The system jobs pass the row's `user_id` as `owner_id`, so a per-user allow-list behaves the same for them. Creation (`create_workspace_item`) does not change in Phase 2.

---

## D. Lifecycle after the move

- **Upload init.** Unchanged. The insert creates the file with the same uuid, in the same transaction.
- **Finalize, cancel, reconciler.** State lives in the file's `metadata.upload_status`. After contract, the reconciler scans `user_files WHERE upload_status = 'uploading' AND created_at < cutoff AND deleted_at IS NULL` (indexed) instead of loading every attachment. Until then the scan stays as it is.
- **OCR "attempt once".** The marker is the file's `metadata.ocr_status`. A file left without a marker (no budget, quota unreadable, process killed) is retried on the next turn, as today. The ledger call at `runner.py:597-605` is the only place pages are charged.
- **Summary.** One per file: `summary`, `summary_source_length`, `summary_updated_at`, `metadata.summary_attempt`, `metadata.doc_type`, `title`. New text clears the summary on the file and on its pointers. `resummarize_dirty_items` and the daily sweeper keep their rules; the sweeper needs a `user_files` scan only at contract.
- **Segmenter staleness.** `attachment_segment_maps.content_chars` against `user_files.content_chars` — the same number as `len(content_md)` today. One map per file. `map_state`, `load_pages` and `write_segment_map` are unchanged in Phase 2.
- **Retention purge.** Per file. The pointer's `storage_path` and `metadata` follow in the same statement, so `get_workspace_file_url` and the viewer's retention line are unchanged.
- **Item delete.** Phase 2 keeps today's meaning: the pointer's `deleted_at` is mirrored to the file, the storage object is removed, and the file leaves «الملفات». This is exact while a file has one pointer.
- **Conversation delete.** Unchanged (conversation only). The library rule "home conversation not deleted" is kept as is.
- **Account purge.** Storage prefix `general/{user_id}` as today. `purge_user_data` deletes pointers, then files; pages, maps and helper runs cascade. If the RPC line were ever missing, the `users` cascade still removes the files at the final step.
- **Visibility.** Pointer only. The mirror fires and writes nothing.
- **«حذف من الملفات».** Becomes one atomic statement, replacing the read-modify-write at `files_service.py:686-701`.
  - `hide_files(user_id, ids)` calls `user_files_hide`, which sets the `library_hidden_at` column and keeps the first stamp.
  - The column cannot be dropped by another writer's stale `metadata`, which closes plan §12 item 7.
  - The copy in `metadata` exists only so the pre-switch reader agrees if the flag is turned back.
- **`files_service.py`.** Keep the current functions as the off path, and add the file path behind `files_read_enabled`:
  - `_load_attachments` → `_load_files`: `user_files` by `user_id`, `deleted_at IS NULL`, `.gt("content_chars", 0)` instead of `.neq("content_md", "")`, `.eq("file_size_bytes", size)`; order `created_at desc, file_id desc`.
  - Pointer facts come from an embed `workspace_items!workspace_items_file_owner_fkey(item_id, conversation_id, wi_seq, deleted_at, created_at, conversations!artifacts_conversation_id_fkey(deleted_at))`. The home pointer is the oldest live one.
  - `_hidden` reads the `library_hidden_at` column; `_listable` is unchanged in meaning.
  - `chars` = `content_chars`; `_fresh_map_ids` compares against it.
  - `get_library_file(user_id, id)` resolves `id` as a pointer `item_id` first, then as a `file_id`. The pane and the page keep calling it the same way.
  - `match_library_file` returns the pointer's `item_id` and `wi_seq` for a same-conversation match.
  - Contract §11 stays as is. Add `file_id: str` to `LibraryFile` (additive).

---

## E. Phase-3 readiness (design only)

- **Second pointer.** A plain insert into `workspace_items` (`kind='attachment'`, `conversation_id`, `user_id`, `file_id`, `created_by='user'`).
  - `assign_workspace_item_seq` gives the next WI number in that conversation.
  - The link trigger checks ownership. From contract it also fills `title`, `summary`, `metadata`, `storage_path` and `word_count` from the file.
  - Add `CREATE UNIQUE INDEX … ON workspace_items (conversation_id, file_id) WHERE kind='attachment' AND deleted_at IS NULL AND file_id IS NOT NULL`. Picking the same file twice in one conversation then returns the existing item and its WI number.
- **Prerequisite.** The pointer → file mirror must be dropped before any second pointer exists, or a pointer delete would mark the file deleted. So the write switch must be on for everyone, and the legacy write branch removed, before Phase 3.
- **`message_attachments`.** Unchanged; it links the message to that conversation's pointer. History tags and chips read the pointer's synced `title`.
- **«صلة المستند بالمحادثة».** Add a nullable pointer column `summary_context text`.
  - What agents read is `user_files.summary || COALESCE(pointer.summary_context, '')`, composed in two places only: the sync trigger (pointer copy) and `overlay_files`.
  - For existing files, split the stored summary at the prompt's `**صلة المستند بالمحادثة:**` marker: head to the file, tail (with its leading whitespace) to the first pointer. The first conversation then reads the same bytes as today.
  - A second conversation starts with an empty tail and no model call.
  - The summarizer already returns `context_link` separately (`artifact_summarizer\models.py:141`). Making `summary_md` document-only is a prompt change and needs the owner (O3).
- **Segmenter and the user's words.** The runner is called with the pointer (for `_load_user_words`) and writes under the file id. A file with a fresh map is `skipped_fresh` in every conversation and is excluded from any later message's bundle. A map is replaced only by a version bump or new text. Otherwise conversation 2 could change what conversation 1 receives.
- **Helper runs.** `(item_id = file id, helper)`. Two conversations needing the same file share one row, so one runs and the other polls the lease. Map pointer → file once, inside `wait_and_load_sections` and `TurnHelpers`, and return results keyed by the pointer id so `case_documents.py` and `walkers.py` stay untouched.
- **OCR pages.** A reused file has `ocr_status`, so it is not a candidate, has no ledger row, and the page gate projects 0. For a file attached twice before its first OCR, make OCR a file-scoped helper with a `helper_runs` lease (`helper_frame.md` §5.7) so two turns cannot both pay. For library uploads, `finalize` runs the page gate and OCR in its own ledger scope with no conversation. Whether `llm_calls` and the quota RPC accept a NULL conversation was not checked.
- **Deletion.**
  - Removing a use deletes only the pointer.
  - «حذف من الملفات» sets `library_hidden_at`; pointers keep working.
  - `delete_workspace_item` must stop deleting the storage object when the file is still listed or has another live pointer.
  - Hard delete happens only in account purge.
- **Library upload.** Insert into `user_files` with `origin='library'` and no pointer; storage under `general/{user_id}/…` (already covered by the purge prefix). The reconciler and retention sweep cover it once they scan `user_files`.

---

## F. Verification

- **Migration dry-run.** Run 220, then 221 with each `COMMIT` replaced by `ROLLBACK`, on a Supabase branch or a restored copy first. Then on prod in the quiet window; the self-checks run inside the transaction either way.
- **Count and parity queries.** The per-user count and the field-parity queries from 221's self-check, kept as `scripts/user_files_parity.py --sql`; both must return 0.
  - Run after 221, after each flag flip, and daily from an APScheduler job at 04:00 UTC.
  - The job logs counts only.
- **What each agent receives.** `scripts/user_files_parity.py --agents` runs read-only with the service role over every conversation that has an attachment. It calls the real functions twice, under `flags.override(read=False)` and `read=True`, and compares SHA-256 digests of the canonical JSON of each result. It prints conversation id, function name, equal or different, and lengths. No text leaves the process, and any output file goes outside the repo.
  - Functions covered:
    - `_load_workspace_item_summaries` → `render_workspace_items_fence`
    - `_load_recent_messages(full=True)` (history tags)
    - `_load_attached_items` → `build_case_document_blocks` and `build_case_document_blocks_segmented` with `load_segment_maps`
    - `build_analyzed_items_direct` and `load_writer_planner_context`
    - `unfold_item` per attachment
    - `_load_compaction_context`
    - `item_analyzer._load_workspace_items`
    - `list_workspace_items_by_conversation` → `_to_response`
    - `files_service` list / detail / match, per user
  - Run before 221 and after it (flag off both times; digests must match), then with the override on.
- **Unit tests** (need whitelist lines in `.gitignore` and the CI list, per plan §12 item 8).
  - `shared/files/tests`:
    - overlay off is a no-op
    - overlay replaces only rows with `file_id`
    - `doc_type` alias
    - a pointer with `item_id ≠ file_id`
    - missing file row
    - user scoping
    - chunking above 100 ids
    - `strict`
    - flags and probe
    - `write_attachment` off path issues today's exact call; on path issues the RPC with the patch
  - Per reader: a fake client whose file row differs from the pointer row proves which source is used under each flag.
  - `test_files_service.py` parametrised over both paths.
  - A pinned allow-list of files that call `.table("workspace_items")`, so a new call site forces the question "does it read an attachment?".
- **First hour after 221.**
  - Upload init and finalize error rate (`upload.init`, `upload.finalize` spans).
  - Warnings: `OCR: failed to persist content_md`, `failed to write metadata.ocr_status`, `attachment UPDATE failed`, `purge marker failed`.
  - Postgres log: `deadlock detected`, lock timeouts, the 23503 from the link trigger.
  - One manual upload → message → OCR → summary, checked on both rows.
  - Parity queries at 0.
- **After the read flag.** Parity script green, router context latency, any `files:` warning.
- **After the write flag.** The same warnings, parity at 0 after real uploads, and one retention-sweep and one reconciler night.

---

## G. Rollout and rollback

| # | What | Who is affected | Back out |
|---|---|---|---|
| 1 | Apply 220 | nobody | drop the table |
| 2 | Capture agent digests; apply 221 in a quiet window; capture again | every attachment write now runs two triggers | flags are off; run the 221 rollback |
| 3 | Deploy code, all flags off (`shared/files`, the S2/S3 call sites, `files_service` file path, parity job, `nuke_account.py`) | nobody | redeploy the previous build |
| 4 | `USER_FILES_READ_ENABLED=true` with `_USERS` = the test account; parity script; then everyone | readers | unset the flag |
| 5 | `USER_FILES_WRITE_ENABLED=true`, test account for a day, then everyone | writers and system jobs | unset the flag; nothing to repair |
| 6 | Apply 222 | nobody visibly | re-point the three FKs (until Phase 3 data exists) |
| 7 | Phase 4: deploy code without the legacy branches, then 223 | all | refill pointer text from `user_files` |

Every flag change is a Railway redeploy, which kills in-flight searches, so do them in quiet windows. Step 5 can move to the start of Phase 3 with no loss. It must be complete before Phase 3.

---

## H. Risks, plan corrections, owner questions

**Risks**
- **A trigger bug fails attachment writes outright.** Uploads and OCR persistence fail loudly; nothing drifts silently. The self-test in 221 and the first-hour watch are the guard.
- **Deadlock window.** Old-path and new-path writers lock the two rows in opposite order. This needs two code versions writing the same file in the same instant (a deploy overlap), or a pointer delete racing a file write. The effect is one failed statement. Cheap hardening if wanted: a BEFORE UPDATE trigger on `user_files` that locks the pointers first. It ends at contract.
- **`pg_trigger_depth()` guard.** It assumes no other trigger ever updates attachment rows. That is true today, and it becomes a rule.
- **`user_file_patch` merges metadata.** This is a deliberate improvement, and it is identical to today whenever no two writers overlap. It fixes OCR writing back a snapshot up to 10 minutes old, which can today drop `original_purged_at` or `summary_attempt`.
- **Embed ambiguity.** PostgREST must not see a new ambiguous relationship. `workspace_items` gets one FK to `user_files` and is not a junction (its key is `item_id`). I reasoned this from PostgREST's rules and did not test it.
- **Not checked.** Whether Supabase sets a `statement_timeout` for `service_role` (it matters only for writers waiting about a second during Tx C). Whether the "آخر تحديث" footer is rendered for attachments (`updated_at` is preserved regardless).

**Plan corrections (`my_files.md`)**
1. §4.1 and §4.3: do not add `file_id` to pages, maps or helper runs, and do not move their primary keys. Keep `item_id` and retarget the FK.
2. §4.3 step 1 "new uploads write both" becomes the triggers.
3. §4.1 column list:
   - `filename`, `mime_type`, `file_size_bytes`, `doc_type`, `ocr_status`, `content_chars` are generated columns.
   - `in_library` becomes `library_hidden_at`.
   - `page_count` and `original_purged_at` stay in `metadata`.
4. §4.3 step 3: empty `content_md` only. `summary` stays on the pointer as a synced copy.
5. D14 says 599 message links "must keep pointing at the right file". They point at the conversation item, and stay there.
6. §2 calls `from-document` a precedent for a pointer row. Prod has 0 such rows, OCR marks them `skipped_unsupported`, and the only reader of their text (`workspace_context.py`) has no caller.
7. §5 proposes an index on (user, name, size). `files_service` deliberately keeps the name out of query URLs, so the index is (user, size).
8. O4 says files survive a conversation delete "from Phase 2". That changes what users see, so it belongs to Phase 3.
9. §10 "account purge removes the file rows" needs the `purge_user_data` change and the `nuke_account.py` line; neither is in the plan.
10. §4.3 says 219 is held by seg-v5.2. In this worktree 219 and 219b are the merged trial-limits migrations.

**Owner questions**
1. **Removing a file from one conversation's pane (Phase 3): does it stay in «الملفات»?** I recommend yes, with a line in the dialog. Phase 2 keeps today's behaviour, where it disappears everywhere.
2. **Deleting a conversation (O4): when do its files start staying in «الملفات»?** I recommend Phase 3, together with the dialog copy, not Phase 2.
3. **The summary's «صلة المستند بالمحادثة» (O3).** I recommend the split in E: the document summary on the file, the link per conversation, empty for a second conversation, and a summarizer prompt change approved before Phase 3 ships.

### Critical Files for Implementation
- `C:\Programming\LUNA_AI_my_files\shared\db\migrations\215_attachment_segments.sql` (style, FK and self-check pattern for 220–222)
- `C:\Programming\LUNA_AI_my_files\agents\memory\ocr_extractor\runner.py`
- `C:\Programming\LUNA_AI_my_files\agents\memory\summarize.py`
- `C:\Programming\LUNA_AI_my_files\agents\orchestrator.py`
- `C:\Programming\LUNA_AI_my_files\backend\app\services\files_service.py`
