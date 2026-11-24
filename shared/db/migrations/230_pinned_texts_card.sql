-- ============================================================================
-- Migration 230 — one «النصوص المثبّتة» card per conversation.
--
-- Source: .claude/plans/completion_search.md §4.5 / §5, owner decision C21
--         (2026-11-21): the user gets ONE card per conversation listing the
--         official texts that were pinned — not a new card on every search turn,
--         which is what the `statute_package` note does today (203 cards in 177
--         conversations on 2026-11-21).
--
-- APPLY AFTER 229 HAS COMMITTED: the index predicate uses the enum value 229
-- adds, and a new enum value cannot be used in the transaction that adds it.
--
-- WHAT IT ADDS ───────────────────────────────────────────────────────────────
--   A unique partial index: at most one LIVE pinned_texts item per conversation.
--   Two writers can reach the card at once (a planner's pin and a background
--   attachment run in the same turn). The application merges with a
--   compare-and-set on updated_at (shared/workspace/pinned_texts.py); this index
--   is the database's own guarantee for the one case that code cannot settle —
--   two writers both finding no card and both inserting. The loser gets a unique
--   violation, re-reads, and merges into the winner's card.
--
--   A soft-deleted card (deleted_at set) is outside the index, so a conversation
--   whose card the user removed gets a fresh one on the next pin.
--
-- WHAT IT DOES NOT NEED ──────────────────────────────────────────────────────
--   * The 15-item cap: `enforce_artifact_cap` (031) counts only agent_search,
--     agent_writing and note. A pinned_texts item is outside it with no change.
--   * Edit protection: the API allows edits only for note / agent_writing
--     (workspace_service.USER_EDITABLE); every other kind answers 403.
--   * The LLM summary: the card is inserted with its summary already set, so the
--     `summarize_artifact_on_insert` trigger (WHEN NEW.summary IS NULL) never
--     fires for it, and the code re-sets the summary after every merge.
--
-- VERIFIED LIVE (prod dwgghvxogtwyaxmbgjod, 2026-11-22): workspace_items has
--   conversation_id uuid, kind workspace_item_kind, deleted_at timestamptz; no
--   index named workspace_items_one_pinned_texts_per_conversation.
--
-- LOCKS: CREATE INDEX takes SHARE on workspace_items for the build. The
--   predicate matches zero rows today, so the build is a single pass over the
--   table. lock_timeout 5s: on a busy moment it fails cleanly and is re-run.
--
-- IDEMPOTENT: CREATE UNIQUE INDEX IF NOT EXISTS.
-- ROLLBACK: DROP INDEX IF EXISTS public.workspace_items_one_pinned_texts_per_conversation;
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
         WHERE t.typname = 'workspace_item_kind' AND e.enumlabel = 'pinned_texts'
    ) THEN
        RAISE EXCEPTION '230: apply 229 first (workspace_item_kind has no pinned_texts value)';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS workspace_items_one_pinned_texts_per_conversation
    ON public.workspace_items (conversation_id)
    WHERE kind = 'pinned_texts' AND deleted_at IS NULL;

COMMENT ON INDEX public.workspace_items_one_pinned_texts_per_conversation IS
    'At most one live «النصوص المثبّتة» card (kind = pinned_texts) per conversation (230). The application merges into it; this index settles two writers that both find no card.';

-- self-check (before COMMIT, so a dry run proves it too)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public' AND tablename = 'workspace_items'
           AND indexname = 'workspace_items_one_pinned_texts_per_conversation'
           AND indexdef ILIKE '%UNIQUE%' AND indexdef ILIKE '%pinned_texts%'
    ) THEN
        RAISE EXCEPTION '230: the one-card-per-conversation index is missing or not unique';
    END IF;
    RAISE NOTICE '230 OK: one live pinned_texts card per conversation is enforced';
END $$;

COMMIT;
