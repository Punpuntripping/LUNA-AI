-- Migration 041: 300-char minimum content_md gate on the artifact summarizer.
--
-- Updates ``notify_summarize_artifact()`` (migration 040) to skip the webhook
-- call when ``char_length(content_md) < 300``. This is a cost-control measure:
-- short blurbs and tiny notes don't need an agent-facing summary (downstream
-- agents can just read content_md directly), and skipping the HTTP call here
-- saves a backend round-trip + LLM tokens.
--
-- The backend webhook also enforces this gate (``MIN_CONTENT_LENGTH_CHARS``
-- in ``internal_webhooks.py``) so a manual POST or backfill script can't
-- bypass it. Defense in depth.
--
-- Threshold rationale: 300 chars ≈ 50-80 Arabic words ≈ a few short sentences.
-- Below that, the artifact body IS effectively the summary. Above that, it's
-- worth the ~$0.001 round-trip.
--
-- This migration is idempotent.

CREATE OR REPLACE FUNCTION public.notify_summarize_artifact()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, net, extensions
AS $$
DECLARE
    webhook_url    TEXT := current_setting('app.webhook_url',    true);
    webhook_secret TEXT := current_setting('app.webhook_secret', true);
    -- Below this length, the LLM call is wasteful. Mirrored in the backend.
    min_content_chars CONSTANT INT := 300;
BEGIN
    -- No webhook URL configured — silently skip. Lets the migration land in
    -- environments where the trigger is intentionally disabled.
    IF webhook_url IS NULL OR webhook_url = '' THEN
        RETURN NEW;
    END IF;

    -- Nothing to summarize: short-circuit the HTTP call.
    IF COALESCE(NEW.content_md, '') = '' THEN
        RETURN NEW;
    END IF;

    -- Too short to be worth summarizing — save the LLM round-trip. The
    -- backend enforces the same gate as defense in depth.
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
    'row with content_md >= 300 chars and summary IS NULL. Best-effort: '
    'webhook URL/secret are read from session GUCs; if absent the trigger no-ops.';
