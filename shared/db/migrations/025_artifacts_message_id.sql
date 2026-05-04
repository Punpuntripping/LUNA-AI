-- Migration 025: Add `message_id` column to artifacts.
-- Consistency: artifacts should know which user turn produced them.
-- Nullable so existing rows survive; all new rows from _run_pydantic_ai_task
-- will populate it.

ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS message_id UUID REFERENCES messages(message_id);
CREATE INDEX IF NOT EXISTS artifacts_message_id_idx ON artifacts(message_id);
