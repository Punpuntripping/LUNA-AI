-- 060_paused_runs.sql
--
-- Dedicated store for pause/resume working state, split out of agent_runs.
--
-- agent_runs conflated an immutable audit LOG with a mutable suspension STATE.
-- The suspension state is the only part anything reads back (_find_awaiting_user
-- reads ONLY status='awaiting_user' rows; nothing reads completed rows). It is
-- mutable working state — created when a run pauses (ask_user / approve_plan),
-- read back to rehydrate the pydantic-ai run, then resolved. That does not
-- belong in an append-only trace; it gets its own tiny table.
--
-- Delete-on-resolve: a row exists ONLY while a run is actually paused. Resume
-- success / abandon / timeout / expire all DELETE it. So the table is
-- self-cleaning and stays tiny forever — no status column, no historical bloat.
-- (Pause analytics — how often runs pause/expire — come from Logfire spans.)
--
-- RLS enabled, no policies: service-role only, exactly like agent_runs was.

CREATE TABLE IF NOT EXISTS public.paused_runs (
    run_id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  uuid        NOT NULL,
    user_id          uuid        NOT NULL,
    case_id          uuid,
    -- Only 'deep_search' supports resume today; stored so the resume path can
    -- branch and abandon anything else.
    agent_family     text,
    -- Router-emitted short Arabic label, inherited on resume (router does not
    -- re-run) so the resumed MajorAgentInput keeps the original task identity.
    task_label       text,
    -- pydantic-ai serialized message history (result.all_messages_json()),
    -- replayed via ModelMessagesTypeAdapter to continue the run.
    message_history  bytea,
    -- {tool_call_id, tool_name, args, partial_output} for DeferredToolResults.
    deferred_payload jsonb,
    -- The question / plan_md surfaced to the user.
    question_text    text,
    -- 'clarify' (ask_user) | 'approve_plan' (present_plan_for_approval).
    pause_reason     text        NOT NULL DEFAULT 'clarify',
    asked_at         timestamptz,
    expires_at       timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.paused_runs IS
    'Mutable pause/resume working state (delete-on-resolve). Split out of agent_runs. Written via agents/paused_runs.py.';

-- The only query: "the open pause for this conversation", newest first.
CREATE INDEX IF NOT EXISTS idx_paused_runs_lookup
    ON public.paused_runs (conversation_id, user_id, asked_at DESC);

ALTER TABLE public.paused_runs ENABLE ROW LEVEL SECURITY;
-- No policies on purpose: service-role-only, matching agent_runs.
