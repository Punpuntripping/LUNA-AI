-- Migration 023: retrieval_artifacts table.
-- One row per deep_search turn -- carries the full merged retrieval object
-- (URA 2.0) consumed by the aggregator. The original query is reachable via
-- messages.content WHERE message_id = retrieval_artifacts.message_id.

CREATE TABLE IF NOT EXISTS retrieval_artifacts (
    ura_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(user_id),
    conversation_id UUID REFERENCES conversations(conversation_id),
    message_id      UUID REFERENCES messages(message_id),
    artifact_id     UUID REFERENCES artifacts(artifact_id),
    ura_json        JSONB NOT NULL,            -- full UnifiedRetrievalArtifact.model_dump()
    schema_version  TEXT NOT NULL DEFAULT '2.0',
    high_count      INT NOT NULL DEFAULT 0,
    medium_count    INT NOT NULL DEFAULT 0,
    produced_by     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {reg_search, compliance_search, case_search}
    duration_ms     INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS retrieval_artifacts_user_id_idx ON retrieval_artifacts(user_id);
CREATE INDEX IF NOT EXISTS retrieval_artifacts_conversation_id_idx ON retrieval_artifacts(conversation_id);
CREATE INDEX IF NOT EXISTS retrieval_artifacts_message_id_idx ON retrieval_artifacts(message_id);
CREATE INDEX IF NOT EXISTS retrieval_artifacts_artifact_id_idx ON retrieval_artifacts(artifact_id);
CREATE INDEX IF NOT EXISTS retrieval_artifacts_created_at_idx ON retrieval_artifacts(created_at DESC);

ALTER TABLE retrieval_artifacts ENABLE ROW LEVEL SECURITY;

-- Match the `artifacts` table's RLS pattern -- see migrations/019_artifacts.sql.
DO $$ BEGIN
    CREATE POLICY retrieval_artifacts_select ON retrieval_artifacts FOR SELECT
        USING (user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid()));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY retrieval_artifacts_insert ON retrieval_artifacts FOR INSERT
        WITH CHECK (user_id = (SELECT user_id FROM users WHERE auth_id = auth.uid()));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
