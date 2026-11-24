-- ============================================================================
-- Migration 229 — workspace_item_kind gains 'pinned_texts'.
--
-- Source: .claude/plans/completion_search.md §4.5 / §5, owner decisions C8, C21,
--         C25 (2026-11-15 → 11-22): the official texts an agent or an attachment
--         brought into a conversation live on ONE read-only card per conversation,
--         shown to the user as «النصوص المثبّتة». It replaces the `statute_package`
--         note card (a new note on every search turn).
--
-- THIS FILE DOES ONE THING, AND ALONE, ON PURPOSE ────────────────────────────
--   `ALTER TYPE … ADD VALUE` cannot be used in the transaction that adds it. So
--   the value is added here, with NO transaction block, and everything that uses
--   it (the one-card-per-conversation index, the merge function) is in 230.
--   Apply 229, let it commit, then apply 230.
--
-- SAFE TO APPLY EARLY ────────────────────────────────────────────────────────
--   Additive. Nothing writes the new kind until 230 is applied AND one of the
--   completion-search flags is on. Released web and mobile clients that do not
--   know the kind leave such an item out of their lists; none can exist before
--   the flags are on.
--
-- CANNOT BE UNDONE ───────────────────────────────────────────────────────────
--   Postgres has no DROP VALUE for an enum. Unused, the value is harmless.
--
-- VERIFIED LIVE (prod dwgghvxogtwyaxmbgjod, 2026-11-22): the enum holds
--   attachment, note, agent_search, agent_writing, convo_context, references.
--
-- IDEMPOTENT: ADD VALUE IF NOT EXISTS.
-- ============================================================================

ALTER TYPE public.workspace_item_kind ADD VALUE IF NOT EXISTS 'pinned_texts';

-- self-check (reads the catalog only; safe in the same session)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
         WHERE t.typname = 'workspace_item_kind' AND e.enumlabel = 'pinned_texts'
    ) THEN
        RAISE EXCEPTION '229: workspace_item_kind has no pinned_texts value';
    END IF;
    RAISE NOTICE '229 OK: workspace_item_kind has pinned_texts';
END $$;
