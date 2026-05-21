-- Migration 044: persist artifact + referenced-item linkage on messages.
--
-- Closes Window B Tasks 5–7. Adds two arrays on ``messages``:
--   * ``artifact_ids``           — workspace_items this assistant message
--                                  produced (one row per ``workspace_item_created``
--                                  event during the agent turn). Drives the
--                                  inline source chip + citation linking in
--                                  ``frontend/components/chat/MessageBubble.tsx``.
--   * ``referenced_item_ids``   — workspace_items the planner responder pointed
--                                  to instead of publishing a new card (Phase E
--                                  ``referenced_existing_item`` SSE). Drives the
--                                  "راجع البطاقة السابقة" chip.
--
-- Both are NULLable (legacy / user / Q&A / paused-question rows carry NULL or
-- an empty array). The frontend Message TS type already exposes the singular
-- ``artifact_ids?: string[] | null`` shape; this migration is the backing
-- column. Frontend gating logic (``hasArtifacts`` in MessageBubble.tsx:105) is
-- a no-op until the column populates, so applying this migration without the
-- ``message_service.pipeline_producer`` patch is safe.
--
-- Element-type choice: plain ``uuid[]`` rather than a join table. The
-- cardinality is bounded (today: 0–1 per deep_search turn, 0–N for future
-- multi-output agents but capped by the per-turn ``MAX_ATTACHED_ITEMS=7``
-- contract). A join table would buy referential integrity at the cost of an
-- extra read on every message list. We prefer the array for read performance
-- and accept that a hard-deleted workspace_item leaves a dangling uuid in the
-- array — the frontend treats unknown ids as "card no longer available" and
-- skips them. Soft-deletes (the actual deletion path) are handled by the
-- artifactLookup in ``ChatPage.tsx`` which filters on ``deleted_at IS NULL``.
--
-- This migration is idempotent.

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS artifact_ids uuid[];

COMMENT ON COLUMN messages.artifact_ids IS
    'Workspace items produced by the agent run that authored this assistant '
    'message. Populated by message_service.pipeline_producer as '
    'workspace_item_created events arrive on the SSE stream. NULL for user '
    'messages, agent_question messages, and pre-Window-B rows.';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS referenced_item_ids uuid[];

COMMENT ON COLUMN messages.referenced_item_ids IS
    'Workspace items the planner responder referenced instead of publishing a '
    'new card (Phase E build_artifact=False branch). Drives the prior-card '
    'chip in the chat bubble. NULL for messages that did publish a new card '
    'or did not invoke deep_search.';

-- GIN index for ``artifact_ids @> ARRAY[<id>]::uuid[]`` lookups (e.g. "which
-- message produced this artifact?"). The list_messages query path doesn't
-- need an index — it filters by conversation_id which is already indexed,
-- and arrays travel back with the row. The reverse lookup (artifact → message)
-- is rare today but cheap to support up-front.
CREATE INDEX IF NOT EXISTS idx_messages_artifact_ids
    ON messages USING GIN (artifact_ids);
