-- Migration 030: Memory infrastructure (Wave 9 Task 2).
--
-- Adds:
--   1. Per-item summary columns on workspace_items. `summary IS NULL` is the
--      "dirty" signal consumed by the lazy summarization pre-router hook.
--   2. A BEFORE UPDATE trigger that invalidates the summary whenever
--      content_md changes. Metadata-only updates (title, is_visible, etc.)
--      do NOT clear the summary.
--   3. conversations.compacted_through_message_id — cutoff pointer for
--      conversation compaction. Router loads messages with created_at >
--      the cutoff message's created_at. NULL = no compaction yet.
--
-- Dependencies:
--   - 026_workspace_items.sql (workspace_items table + content_md column)
--   - 008_messages.sql        (messages.message_id)
--   - 007_conversations.sql   (conversations table)
--
-- This migration is idempotent.

------------------------------------------------------------------------
-- 1. workspace_items summary columns
------------------------------------------------------------------------
ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS summary               TEXT,
    ADD COLUMN IF NOT EXISTS summary_source_length INTEGER,
    ADD COLUMN IF NOT EXISTS summary_updated_at    TIMESTAMPTZ;

------------------------------------------------------------------------
-- 2. Invalidation trigger
------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.workspace_items_invalidate_summary()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.content_md IS DISTINCT FROM OLD.content_md THEN
        NEW.summary               := NULL;
        NEW.summary_source_length := NULL;
        NEW.summary_updated_at    := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_workspace_items_invalidate_summary
    ON public.workspace_items;

CREATE TRIGGER trg_workspace_items_invalidate_summary
    BEFORE UPDATE ON public.workspace_items
    FOR EACH ROW
    EXECUTE FUNCTION public.workspace_items_invalidate_summary();

------------------------------------------------------------------------
-- 3. conversations.compacted_through_message_id
------------------------------------------------------------------------
ALTER TABLE public.conversations
    ADD COLUMN IF NOT EXISTS compacted_through_message_id UUID
        REFERENCES public.messages(message_id) ON DELETE SET NULL;

------------------------------------------------------------------------
-- Comments
------------------------------------------------------------------------
COMMENT ON COLUMN public.workspace_items.summary IS
    'Lazily-computed Arabic summary of content_md. NULL = dirty (needs regeneration).';
COMMENT ON COLUMN public.workspace_items.summary_source_length IS
    'Character length of content_md at the time summary was last generated. Used by the pre-router hook to detect drift.';
COMMENT ON COLUMN public.workspace_items.summary_updated_at IS
    'When the current summary was generated. NULL when summary is dirty.';
COMMENT ON COLUMN public.conversations.compacted_through_message_id IS
    'Cutoff for conversation compaction. Messages with created_at <= this row are folded into the running summary; loader fetches messages strictly after it.';
