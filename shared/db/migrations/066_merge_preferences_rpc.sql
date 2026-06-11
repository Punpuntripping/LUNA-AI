-- Migration 066: merge_preferences RPC — atomic JSONB patch merge
-- APPLIED TO PROD 2026-06-11 via Supabase MCP (apply_migration: merge_preferences_rpc).
--
-- PATCH /preferences was read-merge-write with no lock: two concurrent
-- PATCHes silently dropped one patch. The frontend sends PARTIAL patches,
-- so last-write-wins on the whole blob (plain upsert) is NOT acceptable —
-- the merge must happen in SQL: preferences || patch inside one
-- INSERT..ON CONFLICT statement.
--
-- Shallow merge (||) deliberately matches the previous Python semantics
-- {**existing, **patch}: top-level keys in the patch replace, others persist.
--
-- Pre-flight verified live: UNIQUE constraint user_preferences_user_id_key
-- exists, so ON CONFLICT (user_id) is valid.
--
-- This migration is idempotent.

CREATE OR REPLACE FUNCTION public.merge_preferences(
    p_user_id UUID,
    p_patch   JSONB
)
RETURNS jsonb
LANGUAGE sql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
    INSERT INTO public.user_preferences (user_id, preferences)
    VALUES (p_user_id, COALESCE(p_patch, '{}'::jsonb))
    ON CONFLICT (user_id) DO UPDATE
        SET preferences = user_preferences.preferences || EXCLUDED.preferences
    RETURNING jsonb_build_object(
        'user_id', user_id,
        'preferences', preferences
    );
$$;

COMMENT ON FUNCTION public.merge_preferences(UUID, JSONB) IS
    'Atomic upsert-merge of a partial preferences patch into the user''s JSONB blob (shallow merge, patch keys win). Replaces the racy read-merge-write in preferences_service.update_preferences.';

REVOKE ALL ON FUNCTION public.merge_preferences(UUID, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.merge_preferences(UUID, JSONB) TO service_role;
