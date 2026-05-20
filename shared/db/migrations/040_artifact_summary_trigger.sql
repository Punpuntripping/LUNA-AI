-- Migration 040: AFTER-INSERT trigger that fires the artifact_summarizer.
--
-- Wave 10 — decoupled artifact summarization.
-- When a row is inserted into workspace_items (any kind), this trigger
-- POSTs to the backend's internal webhook
-- ``POST /internal/summarize-workspace-item``. The backend fetches the
-- row, runs the artifact_summarizer agent, and writes back to
-- ``workspace_items.summary`` + ``metadata.artifact_summary``.
--
-- Design notes:
--   * Fire-and-forget — pg_net.http_post returns immediately; the INSERT is
--     not blocked on the summarizer's latency.
--   * Idempotent on the backend side — it checks ``summary IS NULL`` before
--     calling the LLM, so trigger retries / re-deliveries are safe.
--   * Trigger guarded ``WHEN (NEW.summary IS NULL)`` so a future
--     resummarize flow that sets ``summary`` then re-INSERTs (e.g. from a
--     batch backfill) does not loop.
--   * Skip when content_md is empty — attachment/note rows with no body
--     have nothing to summarize. Backend will short-circuit anyway, but
--     skipping the HTTP call here saves a round-trip.
--   * Webhook URL + secret are read from session-level GUC settings,
--     configured per-environment via ``ALTER DATABASE ... SET ...``. The
--     migration itself is env-agnostic.
--
-- Operator steps after applying this migration:
--   ALTER DATABASE postgres
--     SET app.webhook_url = 'https://luna-backend-.../internal/summarize-workspace-item';
--   ALTER DATABASE postgres
--     SET app.webhook_secret = '<value matching backend INTERNAL_WEBHOOK_SECRET env>';
--   -- Reload to pick up the new GUCs in existing sessions.
--   SELECT pg_reload_conf();
--
-- Dependencies:
--   * 026_workspace_items.sql           (creates workspace_items)
--   * 037_workspace_items_summary.sql   (adds summary column the trigger guards on)
--   * pg_net extension                  (Supabase ships pg_net; this migration
--                                        enables it into the ``extensions``
--                                        schema. Functions land at ``net.*``.)
--
-- This migration is idempotent.

-- pg_net's installer always creates the ``net`` schema for its functions;
-- the ``WITH SCHEMA extensions`` clause governs the extension catalog entry
-- only. Both schemas are managed by Supabase.
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;


-- ---------------------------------------------------------------------------
-- 1. Trigger function — fires the webhook via pg_net.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_summarize_artifact()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, net, extensions
AS $$
DECLARE
    webhook_url    TEXT := current_setting('app.webhook_url',    true);
    webhook_secret TEXT := current_setting('app.webhook_secret', true);
BEGIN
    -- No webhook URL configured — silently skip. Lets the migration land in
    -- environments where the trigger is intentionally disabled (e.g. local
    -- dev without a public backend URL).
    IF webhook_url IS NULL OR webhook_url = '' THEN
        RETURN NEW;
    END IF;

    -- Nothing to summarize: short-circuit the HTTP call.
    IF COALESCE(NEW.content_md, '') = '' THEN
        RETURN NEW;
    END IF;

    -- pg_net.http_post on Supabase: positional signature is
    --   (url text, body jsonb, params jsonb, headers jsonb, timeout_milliseconds int)
    -- Using named args for forward-compat with future field additions.
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
    'row with a non-empty content_md and a NULL summary. Best-effort: '
    'webhook URL/secret are read from session GUCs; if absent the trigger no-ops.';


-- ---------------------------------------------------------------------------
-- 2. Trigger binding — AFTER INSERT, guarded on NULL summary.
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS summarize_artifact_on_insert ON public.workspace_items;

CREATE TRIGGER summarize_artifact_on_insert
    AFTER INSERT ON public.workspace_items
    FOR EACH ROW
    WHEN (NEW.summary IS NULL)
    EXECUTE FUNCTION public.notify_summarize_artifact();

COMMENT ON TRIGGER summarize_artifact_on_insert ON public.workspace_items IS
    'Fires the artifact_summarizer webhook on every INSERT where summary is '
    'still NULL. Idempotent on the backend side.';
