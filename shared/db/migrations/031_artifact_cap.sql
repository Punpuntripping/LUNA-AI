-- Migration 031: 15-artifact-per-conversation cap (Wave 9 Task 3).
--
-- BEFORE INSERT trigger on workspace_items that enforces a hard cap of 15
-- counted items per conversation. Counted kinds are user/agent OUTPUTS:
--   - agent_search
--   - agent_writing
--   - note
--
-- Exempt (uncounted):
--   - attachment    (user uploads / system-managed)
--   - convo_context (router-managed running summary)
--   - references    (citations bag)
--
-- Soft-deleted rows (deleted_at IS NOT NULL) are excluded from the count
-- so deleting an item frees a slot without a hard delete.
--
-- The trigger is the LAST line of defense. The application layer
-- (Wave 9 Task 7's pre-flight in `_dispatch`) catches the cap first to
-- avoid wasting a full specialist run; this trigger guards against
-- direct DB writes / race conditions.
--
-- Dependencies:
--   - 026_workspace_items.sql (workspace_items table + workspace_item_kind enum)
--
-- This migration is idempotent.

CREATE OR REPLACE FUNCTION public.enforce_artifact_cap()
RETURNS TRIGGER AS $$
DECLARE
    cap     INTEGER := 15;
    counted INTEGER;
BEGIN
    IF NEW.kind NOT IN ('agent_search', 'agent_writing', 'note') THEN
        RETURN NEW;
    END IF;

    SELECT COUNT(*) INTO counted
        FROM public.workspace_items
        WHERE conversation_id = NEW.conversation_id
          AND deleted_at IS NULL
          AND kind IN ('agent_search', 'agent_writing', 'note');

    IF counted >= cap THEN
        RAISE EXCEPTION 'workspace_items_cap_exceeded'
            USING ERRCODE = '23514',
                  HINT = 'Conversation has reached the 15-item limit. Delete an item before creating new ones.';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_workspace_items_cap
    ON public.workspace_items;

CREATE TRIGGER trg_workspace_items_cap
    BEFORE INSERT ON public.workspace_items
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_artifact_cap();

COMMENT ON FUNCTION public.enforce_artifact_cap() IS
    'Caps counted workspace_items (agent_search, agent_writing, note) at 15 per conversation. Soft-deleted rows excluded; attachment/convo_context/references exempt. Raises workspace_items_cap_exceeded (errcode 23514) on violation.';
