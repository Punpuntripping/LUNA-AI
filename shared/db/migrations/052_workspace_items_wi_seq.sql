-- Migration 052: per-conversation wi_seq on workspace_items.
--
-- Adds a short, stable, conversation-scoped integer handle for every
-- workspace item. This is the LLM-emitted identifier ("WI-3") that
-- replaces raw UUID exposure in the router and planner prompts.
--
-- Why this exists:
--   Today the router and planner inject raw UUIDs into prompts and demand
--   them back in structured output (DispatchAgent.attached_item_ids,
--   PlannerResponse.referenced_item_id, the read_workspace_item tool).
--   Models hallucinate UUIDs, transpose digits, and occasionally invent
--   them — the prompts try to mitigate this in prose ("don't try item_id
--   you don't know"). wi_seq replaces that brittle defense with a
--   structural one: each WI gets a short integer label local to its
--   conversation, stable across turns, and the orchestrator maps
--   ``"WI-{seq}"`` back to ``item_id`` (UUID) at output-validation time.
--
-- Design choices:
--   * Conversation-scoped, NOT global. The LLM never reasons across
--     conversations, so a fresh 1..N count per conversation is the right
--     granularity. UNIQUE(conversation_id, wi_seq) enforces it.
--   * BEFORE INSERT trigger picks the next seq under a per-conversation
--     pg_advisory_xact_lock so concurrent inserts don't race on
--     MAX(wi_seq) + 1.
--   * NULL when conversation_id is NULL — workspace items without a
--     conversation home aren't reachable by the router/planner anyway.
--   * Soft-deleted items still hold their seq slot (no reuse), keeping
--     the alias stable across delete + undelete cycles.
--   * Backfill walks each conversation in created_at order; ties broken
--     by item_id for determinism.
--
-- Dependencies:
--   * 026_workspace_items.sql
--
-- This migration is idempotent.

-- ---------------------------------------------------------------------------
-- 1. Column
-- ---------------------------------------------------------------------------
ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS wi_seq INTEGER;

COMMENT ON COLUMN public.workspace_items.wi_seq IS
    'Conversation-scoped 1-based integer alias for the WI. The "{seq}" in '
    '"WI-{seq}" — the short, stable handle the router and planner LLMs '
    'emit instead of the raw UUID. NULL when conversation_id IS NULL. '
    'Auto-assigned by the assign_workspace_item_seq trigger; UNIQUE per '
    'conversation_id. Soft-deleted items keep their seq.';

-- ---------------------------------------------------------------------------
-- 2. Trigger function — assigns wi_seq before insert.
--
-- pg_advisory_xact_lock keyed on the conversation_id ensures two concurrent
-- inserts into the same conversation pick different seqs. The lock is
-- released automatically at transaction commit / rollback.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.assign_workspace_item_seq()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- No conversation -> no alias. Router/planner only operate on items
    -- with a conversation_id; case-only items stay at NULL.
    IF NEW.conversation_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- Allow callers to pre-set a specific wi_seq (rare; mostly tests). The
    -- UNIQUE constraint will reject duplicates regardless.
    IF NEW.wi_seq IS NOT NULL THEN
        RETURN NEW;
    END IF;

    -- Serialise per-conversation MAX(wi_seq) reads + writes.
    PERFORM pg_advisory_xact_lock(
        hashtext('workspace_items_seq:' || NEW.conversation_id::TEXT)
    );

    SELECT COALESCE(MAX(wi_seq), 0) + 1
      INTO NEW.wi_seq
      FROM public.workspace_items
      WHERE conversation_id = NEW.conversation_id;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.assign_workspace_item_seq() IS
    'BEFORE INSERT trigger: assigns the next wi_seq within the row''s '
    'conversation_id. Uses pg_advisory_xact_lock to serialise concurrent '
    'inserts. Skips when conversation_id IS NULL or the caller has already '
    'set wi_seq explicitly.';

-- ---------------------------------------------------------------------------
-- 3. Trigger binding
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS assign_workspace_item_seq ON public.workspace_items;

CREATE TRIGGER assign_workspace_item_seq
    BEFORE INSERT ON public.workspace_items
    FOR EACH ROW
    EXECUTE FUNCTION public.assign_workspace_item_seq();

COMMENT ON TRIGGER assign_workspace_item_seq ON public.workspace_items IS
    'Picks the next wi_seq within a conversation at insert time. Fires '
    'BEFORE so the assigned value lands in the same row before the '
    'UNIQUE constraint is checked.';

-- ---------------------------------------------------------------------------
-- 4. Backfill — assign wi_seq to existing rows, per-conversation, ordered
--    by created_at then item_id (deterministic tie-break).
--    Includes soft-deleted rows so their seq slots stay reserved.
-- ---------------------------------------------------------------------------
WITH numbered AS (
    SELECT item_id,
           ROW_NUMBER() OVER (
               PARTITION BY conversation_id
               ORDER BY created_at, item_id
           ) AS seq
    FROM public.workspace_items
    WHERE conversation_id IS NOT NULL
)
UPDATE public.workspace_items wi
SET wi_seq = numbered.seq
FROM numbered
WHERE wi.item_id = numbered.item_id
  AND wi.wi_seq IS NULL;

-- ---------------------------------------------------------------------------
-- 5. Uniqueness — one seq per slot, per conversation. Items without a
--    conversation (wi_seq IS NULL) are excluded.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS workspace_items_conv_seq_unique
    ON public.workspace_items (conversation_id, wi_seq)
    WHERE wi_seq IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 6. Lookup index — the router/planner alias→UUID resolver scans by
--    (conversation_id, wi_seq) on every prompt build. The unique index
--    above already covers this query, so no additional index needed.
-- ---------------------------------------------------------------------------
