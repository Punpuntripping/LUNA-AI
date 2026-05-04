-- Migration 028: workspace_items.agent_family becomes nullable.
--
-- Wave 8C added user-created kinds (note, attachment, references) via the
-- new POST /workspace/notes / /workspace/attachments / /workspace/references
-- endpoints. These rows have ``created_by='user'`` and no semantic
-- agent_family -- the user authored them, no agent ran. The original
-- artifacts table (migration 019) marked agent_family NOT NULL because
-- every row was an agent output; that invariant doesn't hold post-026.
--
-- Fix: drop the NOT NULL on agent_family. Agent-created rows still pass it
-- (agent_search/agent_writing/convo_context populate it), and user-created
-- rows now legally insert with agent_family=NULL.

ALTER TABLE public.workspace_items ALTER COLUMN agent_family DROP NOT NULL;

COMMENT ON COLUMN public.workspace_items.agent_family IS
    'Which agent family produced this item (deep_search/agent_writing/etc). NULL for user-created kinds (note, attachment, references) where created_by=user.';
