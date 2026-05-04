-- Migration 026: Rename `artifacts` -> `workspace_items` and unify item kinds.
--
-- Wave 8A — Conversation Workspace.
-- Reshapes the existing artifacts table into a single per-conversation
-- workspace bag that holds attachments, notes, agent search outputs,
-- agent writing drafts, conversation context, and references.
--
-- Dependencies:
--   - 019_artifacts.sql           (creates `artifacts` table + RLS policies)
--   - 022_artifact_type_legal_synthesis.sql (extended artifact_type_enum)
--   - 023_retrieval_artifacts.sql (FK retrieval_artifacts.artifact_id -> artifacts)
--   - 025_artifacts_message_id.sql (added artifacts.message_id)
--
-- Notes on inheritance:
--   - RLS policies created in 019 (artifacts_select/insert/update/delete) follow
--     the table through the rename and DO NOT need to be recreated. Same for the
--     update_artifacts_updated_at trigger and all idx_artifacts_* indexes.
--   - retrieval_artifacts.artifact_id FK target follows the table rename
--     automatically; column rename of the PK (`artifact_id` -> `item_id`) also
--     cascades to the FK in Postgres 12+. Smoke-test post-migration; the spec
--     in wave_8_workspace.md notes a fallback ALTER if the cascade misbehaves.
--
-- This migration is idempotent: re-runs are safe.

------------------------------------------------------------------------
-- 1. New enum types
------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE workspace_item_kind AS ENUM (
        'attachment', 'note', 'agent_search', 'agent_writing', 'convo_context', 'references'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE workspace_creator AS ENUM ('user', 'agent');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

------------------------------------------------------------------------
-- 2. Rename table + PK column (idempotent)
------------------------------------------------------------------------
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'public' AND table_name = 'artifacts')
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = 'workspace_items') THEN
        ALTER TABLE public.artifacts RENAME TO workspace_items;
    END IF;
END $$;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public'
                 AND table_name = 'workspace_items'
                 AND column_name = 'artifact_id')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = 'public'
                         AND table_name = 'workspace_items'
                         AND column_name = 'item_id') THEN
        ALTER TABLE public.workspace_items RENAME COLUMN artifact_id TO item_id;
    END IF;
END $$;

------------------------------------------------------------------------
-- 3. New columns
------------------------------------------------------------------------
ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS kind                  workspace_item_kind NOT NULL DEFAULT 'agent_writing',
    ADD COLUMN IF NOT EXISTS created_by            workspace_creator   NOT NULL DEFAULT 'agent',
    ADD COLUMN IF NOT EXISTS storage_path          TEXT,
    ADD COLUMN IF NOT EXISTS document_id           UUID REFERENCES public.case_documents(document_id),
    ADD COLUMN IF NOT EXISTS is_visible            BOOLEAN             NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS locked_by_agent_until TIMESTAMPTZ;

------------------------------------------------------------------------
-- 4. Backfill (must run BEFORE dropping is_editable / artifact_type)
------------------------------------------------------------------------
-- Guarded so the UPDATE only runs while the legacy columns still exist.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public'
                 AND table_name = 'workspace_items'
                 AND column_name = 'is_editable')
       AND EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name = 'workspace_items'
                     AND column_name = 'artifact_type') THEN

        EXECUTE $sql$
            UPDATE public.workspace_items
            SET kind = CASE WHEN is_editable THEN 'agent_writing'::workspace_item_kind
                            ELSE 'agent_search'::workspace_item_kind
                       END,
                created_by = 'agent'::workspace_creator,
                metadata = COALESCE(metadata, '{}'::jsonb)
                           || jsonb_build_object('subtype', artifact_type::text),
                locked_by_agent_until = CASE
                    WHEN metadata ? 'locked_until'
                         AND (metadata->>'locked_until') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                    THEN (metadata->>'locked_until')::timestamptz
                    ELSE NULL
                END
        $sql$;
    END IF;
END $$;

------------------------------------------------------------------------
-- 5. Drop legacy columns + orphaned enum
------------------------------------------------------------------------
ALTER TABLE public.workspace_items DROP COLUMN IF EXISTS is_editable;
ALTER TABLE public.workspace_items DROP COLUMN IF EXISTS artifact_type;

DROP TYPE IF EXISTS artifact_type_enum;

------------------------------------------------------------------------
-- 6. content_md becomes nullable + content shape constraint
------------------------------------------------------------------------
ALTER TABLE public.workspace_items ALTER COLUMN content_md DROP NOT NULL;

DO $$ BEGIN
    ALTER TABLE public.workspace_items
        ADD CONSTRAINT workspace_content_shape CHECK (
            (kind = 'attachment'
             AND (storage_path IS NOT NULL OR document_id IS NOT NULL))
            OR
            (kind <> 'attachment' AND content_md IS NOT NULL)
        );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

------------------------------------------------------------------------
-- 7. New indexes (FK indexes on conversation_id already exist from 019)
------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_workspace_items_kind
    ON public.workspace_items (conversation_id, kind)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_workspace_items_visible
    ON public.workspace_items (conversation_id)
    WHERE deleted_at IS NULL AND is_visible = true;

CREATE INDEX IF NOT EXISTS idx_workspace_items_document_id
    ON public.workspace_items (document_id)
    WHERE document_id IS NOT NULL;

------------------------------------------------------------------------
-- 8. RLS — INHERITED FROM MIGRATION 019
------------------------------------------------------------------------
-- The RLS policies created in 019_artifacts.sql (artifacts_select / _insert /
-- _update / _delete) follow the table through ALTER TABLE ... RENAME TO. They
-- key on user_id which is unchanged. DO NOT recreate them here.
--
-- Verify post-apply with:
--   SELECT polname, polcmd FROM pg_policy
--   WHERE polrelid = 'public.workspace_items'::regclass;

------------------------------------------------------------------------
-- Comments
------------------------------------------------------------------------
COMMENT ON TABLE public.workspace_items IS
    'Per-conversation workspace bag: attachments, notes, agent outputs, context, references. Renamed from artifacts in migration 026.';
COMMENT ON COLUMN public.workspace_items.kind IS
    'Discriminator. Drives permission (note/agent_writing editable; everything else read-only) and which renderer the workspace pane uses.';
COMMENT ON COLUMN public.workspace_items.created_by IS
    'Whether the row was created by the user (note, attachment) or by an agent (agent_search, agent_writing, convo_context).';
COMMENT ON COLUMN public.workspace_items.storage_path IS
    'Supabase Storage path for kind=attachment uploads. Mutually compatible with document_id (one or the other for attachments).';
COMMENT ON COLUMN public.workspace_items.document_id IS
    'For kind=attachment: link to a row in case_documents instead of duplicating the file.';
COMMENT ON COLUMN public.workspace_items.is_visible IS
    'Chip-visibility toggle. Items with is_visible=false are hidden from the chip bar and excluded from agent context loaders.';
COMMENT ON COLUMN public.workspace_items.locked_by_agent_until IS
    'Promoted from Cut-1 metadata.locked_until stopgap. When > now() and kind=agent_writing, user edits return 409.';
