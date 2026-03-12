-- Migration 019: Artifacts table
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(user_id),
    conversation_id UUID REFERENCES conversations(conversation_id),
    case_id         UUID REFERENCES lawyer_cases(case_id),
    agent_family    agent_family_enum NOT NULL,
    artifact_type   artifact_type_enum NOT NULL,
    title           TEXT NOT NULL,
    content_md      TEXT NOT NULL DEFAULT '',
    is_editable     BOOLEAN NOT NULL DEFAULT false,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

-- Indexes
CREATE INDEX idx_artifacts_user_id ON artifacts(user_id);
CREATE INDEX idx_artifacts_conversation_id ON artifacts(conversation_id) WHERE conversation_id IS NOT NULL;
CREATE INDEX idx_artifacts_case_id ON artifacts(case_id) WHERE case_id IS NOT NULL;
CREATE INDEX idx_artifacts_agent_family ON artifacts(agent_family);
CREATE INDEX idx_artifacts_not_deleted ON artifacts(artifact_id) WHERE deleted_at IS NULL;

-- RLS
ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;

CREATE POLICY artifacts_select ON artifacts FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY artifacts_insert ON artifacts FOR INSERT
    WITH CHECK (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY artifacts_update ON artifacts FOR UPDATE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY artifacts_delete ON artifacts FOR DELETE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

-- updated_at trigger
CREATE TRIGGER update_artifacts_updated_at
    BEFORE UPDATE ON artifacts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
