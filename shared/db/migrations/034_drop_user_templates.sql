-- 034_drop_user_templates.sql
-- Drop the user_templates table and its trigger. The templates feature has been
-- removed from the application; user_preferences (in 020) is retained.

DROP TRIGGER IF EXISTS update_user_templates_updated_at ON user_templates;
DROP TABLE IF EXISTS user_templates;
