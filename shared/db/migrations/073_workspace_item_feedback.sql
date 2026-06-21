-- 073_workspace_item_feedback.sql
-- Feature: إعجاب / عدم إعجاب (like / dislike) on workspace items.
--
-- Purpose:
--   The workspace-item action bar (تحليل قانوني / بحث قانوني viewers) gains a
--   👍/👎 control. Per the product decision the rating is PERSISTED, and the
--   user asked for a single column on workspace_items rather than a separate
--   table — the rating is per-item (each workspace_item belongs to exactly one
--   user) so a column suffices.
--
-- Data model:
--   * feedback TEXT, nullable. CHECK (feedback IN ('up','down')).
--   * NULL          = no rating (default / un-rated).
--   * 'up' / 'down' = like / dislike.
--   A NULL passes the CHECK (CHECK only rejects a value that evaluates FALSE),
--   so clearing a rating sets it back to NULL.
--
-- Scope note: the UI only exposes the control on agent outputs
--   (agent_writing, agent_search) but the column is kind-agnostic — the
--   service layer decides where it is writable, not the schema.
--
-- Security / RLS:
--   * No RLS change. feedback is just another column on the already-RLS'd
--     workspace_items table; all reads/writes stay user-scoped through the
--     service layer (get_user_id + eq("user_id", …)), exactly like is_visible.
--
-- Verified live-state (Supabase MCP, 2026-06-21):
--   * workspace_items has no feedback column yet.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + guarded ADD CONSTRAINT.

BEGIN;

ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS feedback TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.workspace_items'::regclass
          AND conname = 'workspace_items_feedback_chk'
    ) THEN
        ALTER TABLE public.workspace_items
            ADD CONSTRAINT workspace_items_feedback_chk
            CHECK (feedback IN ('up', 'down'));
    END IF;
END $$;

COMMENT ON COLUMN public.workspace_items.feedback IS
    'User rating of this item: ''up'' (like), ''down'' (dislike), or NULL (no rating). UI exposes it on agent_writing / agent_search only.';

COMMIT;
