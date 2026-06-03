-- 061_llm_calls_run_id.sql
--
-- Add run_id to the per-call ledger so one logical agent run's calls roll up
-- together — including ACROSS a pause boundary, where a single run spans two
-- user messages (turn-1 pause leg + turn-2 resume leg).
--
-- run_id is allocated app-side at the start of each dispatch (and reused from
-- the paused_runs row on resume), bound via a ContextVar that record_call reads.
-- Nullable: single-call background ops (OCR, summarize) leave it NULL —
-- message_id is enough grouping for those.
--
-- No FK: paused_runs rows are deleted on resolve, so a completed run's run_id
-- would dangle. run_id is just a correlation key here.

ALTER TABLE public.llm_calls ADD COLUMN IF NOT EXISTS run_id uuid;

CREATE INDEX IF NOT EXISTS idx_llm_calls_run_id ON public.llm_calls (run_id);
