-- Migration 053: agent_runs.pause_reason column (writer_planner support).
--
-- Adds a column to distinguish between pause flavors when a Pydantic AI agent
-- run is in `status = 'awaiting_user'`. Required by the writer_planner
-- (`.claude/plans/writer_planner.md`), which can pause for two distinct
-- reasons within the same agent family:
--
--   'clarify'      — ask_user tool raised CallDeferred.
--                    UI renders the question_text directly in chat.
--   'approve_plan' — present_plan_for_approval tool raised CallDeferred.
--                    UI renders question_text as a plan markdown block with
--                    inline approve/reject affordances.
--
-- Earlier pause-capable agents (deep_search_v4) only ever used 'clarify',
-- so existing rows keep the default and behavior is unchanged.
--
-- Dependencies:
--   - 033_agent_runs_pause_columns.sql (agent_runs.deferred_payload, etc.)
--
-- This migration is idempotent.

ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS pause_reason TEXT NOT NULL DEFAULT 'clarify';

COMMENT ON COLUMN public.agent_runs.pause_reason IS
    'Why the run is awaiting_user. Valid values by convention (no CHECK): '
    '''clarify'' (ask_user pause — plain question), '
    '''approve_plan'' (present_plan_for_approval — plan markdown awaiting user yes/no/edit). '
    'Existing rows default to ''clarify'' since pre-053 pauses were all from ask_user.';

-- No new index needed: pause_reason is only consulted alongside an existing
-- conversation_id + status='awaiting_user' lookup, which is already covered
-- by idx_agent_runs_awaiting from migration 033.
