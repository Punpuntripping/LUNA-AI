-- Migration 020: User preferences and templates tables

-- user_preferences: per-user settings (theme, language, defaults)
CREATE TABLE IF NOT EXISTS user_preferences (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL UNIQUE REFERENCES users(user_id),
    preferences     JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_user_preferences_user_id ON user_preferences(user_id);

ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_preferences_select ON user_preferences FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY user_preferences_insert ON user_preferences FOR INSERT
    WITH CHECK (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY user_preferences_update ON user_preferences FOR UPDATE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY user_preferences_delete ON user_preferences FOR DELETE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE TRIGGER update_user_preferences_updated_at
    BEFORE UPDATE ON user_preferences
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- user_templates: reusable prompt templates per user
CREATE TABLE IF NOT EXISTS user_templates (
    template_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(user_id),
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    prompt_template TEXT NOT NULL,
    agent_family    agent_family_enum NOT NULL DEFAULT 'end_services',
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE INDEX idx_user_templates_user_id ON user_templates(user_id);
CREATE INDEX idx_user_templates_not_deleted ON user_templates(template_id) WHERE deleted_at IS NULL;

ALTER TABLE user_templates ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_templates_select ON user_templates FOR SELECT
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY user_templates_insert ON user_templates FOR INSERT
    WITH CHECK (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY user_templates_update ON user_templates FOR UPDATE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE POLICY user_templates_delete ON user_templates FOR DELETE
    USING (user_id = (SELECT u.user_id FROM users u WHERE u.auth_id = (SELECT auth.uid())));

CREATE TRIGGER update_user_templates_updated_at
    BEFORE UPDATE ON user_templates
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
