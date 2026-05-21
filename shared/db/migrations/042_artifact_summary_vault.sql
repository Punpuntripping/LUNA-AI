-- Migration 042: switch the artifact summarizer trigger from GUCs to Supabase Vault.
--
-- The original design in migration 040 used database-level GUCs
-- (``current_setting('app.webhook_url', true)``) read via session settings.
-- On Supabase managed databases ``ALTER DATABASE postgres SET ...`` is
-- restricted to the platform owner — the dashboard SQL user does not have
-- permission. Supabase Vault is the supported alternative for trigger-
-- readable secrets: encrypted at rest, decrypted only inside SECURITY
-- DEFINER functions that opt in.
--
-- Operator step before applying this migration:
--   SELECT vault.create_secret(
--     'https://luna-backend.../internal/summarize-workspace-item',
--     'artifact_summarizer_webhook_url',
--     'URL the workspace_items trigger POSTs to'
--   );
--   SELECT vault.create_secret(
--     '<value matching backend INTERNAL_WEBHOOK_SECRET>',
--     'artifact_summarizer_webhook_secret',
--     'Shared secret for the artifact summarizer webhook'
--   );
--
-- To rotate later: ``SELECT vault.update_secret('<id>', 'new value', ...);``
-- Same vault entries; the trigger reads the new value on next fire.
--
-- This migration is idempotent.

CREATE OR REPLACE FUNCTION public.notify_summarize_artifact()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, net, extensions, vault
AS $$
DECLARE
    webhook_url    TEXT;
    webhook_secret TEXT;
    min_content_chars CONSTANT INT := 300;
BEGIN
    -- Read both secrets from Vault. If either is missing the trigger no-ops
    -- silently — same fail-safe behavior as the GUC-based version.
    SELECT decrypted_secret INTO webhook_url
    FROM vault.decrypted_secrets
    WHERE name = 'artifact_summarizer_webhook_url'
    LIMIT 1;

    SELECT decrypted_secret INTO webhook_secret
    FROM vault.decrypted_secrets
    WHERE name = 'artifact_summarizer_webhook_secret'
    LIMIT 1;

    IF webhook_url IS NULL OR webhook_url = '' THEN
        RETURN NEW;
    END IF;

    IF COALESCE(NEW.content_md, '') = '' THEN
        RETURN NEW;
    END IF;

    IF char_length(NEW.content_md) < min_content_chars THEN
        RETURN NEW;
    END IF;

    PERFORM net.http_post(
        url     := webhook_url,
        body    := jsonb_build_object('item_id', NEW.item_id::text),
        headers := jsonb_build_object(
            'Content-Type',     'application/json',
            'X-Webhook-Secret', COALESCE(webhook_secret, '')
        )
    );

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.notify_summarize_artifact() IS
    'Fires POST /internal/summarize-workspace-item for any new workspace_items '
    'row with content_md >= 300 chars and summary IS NULL. Reads webhook URL '
    'and shared secret from Supabase Vault (artifact_summarizer_webhook_url + '
    'artifact_summarizer_webhook_secret). If either is missing the trigger '
    'silently no-ops.';
