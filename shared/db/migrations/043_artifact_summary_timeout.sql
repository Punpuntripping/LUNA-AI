-- Migration 043: bump pg_net timeout for the artifact summarizer webhook.
--
-- The default ``net.http_post`` timeout is 5 seconds. The summarizer LLM
-- call takes ~10-15s end-to-end (tier_2 reasoning, ~3-5k tokens). The
-- backend successfully completes the work and writes ``summary`` even when
-- pg_net's side times out — FastAPI keeps running once the request is in
-- flight — but ``net._http_response`` accumulates spurious timeout error
-- rows, and any pg_net retry policy could fire the LLM twice.
--
-- Setting ``timeout_milliseconds := 60000`` (60s) gives the LLM plenty of
-- headroom while still bounding the worker connection. Backend p99 is
-- well under 30s; 60s is a wide guard rail.
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
        url                  := webhook_url,
        body                 := jsonb_build_object('item_id', NEW.item_id::text),
        headers              := jsonb_build_object(
            'Content-Type',     'application/json',
            'X-Webhook-Secret', COALESCE(webhook_secret, '')
        ),
        timeout_milliseconds := 60000  -- 60s — LLM call typically ~13s, ample headroom.
    );

    RETURN NEW;
END;
$$;
