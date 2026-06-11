-- Migration 065: Transactional case RPCs + batched counts
-- APPLIED TO PROD 2026-06-11 via Supabase MCP (apply_migration: case_transactional_rpcs).
--
-- Fixes from reliability audit 2026-06-11 (§2 CRUD):
--   1. create_case + first conversation were two PostgREST round-trips with
--      no transaction (case_service.py) — failure between them left an
--      orphaned case with zero conversations.
--   2. delete_case soft-deleted the case then its conversations in two
--      statements — failure between them left live conversations under a
--      deleted case.
--   3. list_cases did 1 + 2N count queries (41 round-trips per page of 20).
--      case_counts() returns both counts for a batch of case_ids in ONE call.
--
-- SECURITY INVOKER on purpose: the backend connects as service_role
-- (bypassrls); EXECUTE revoked from PUBLIC so PostgREST anon/authenticated
-- roles cannot call the write RPCs.
--
-- This migration is idempotent (CREATE OR REPLACE).

CREATE OR REPLACE FUNCTION public.create_case_with_conversation(
    p_user_id     UUID,
    p_case_name   TEXT,
    p_case_type   TEXT DEFAULT 'عام',
    p_description TEXT DEFAULT NULL,
    p_case_number TEXT DEFAULT NULL,
    p_court_name  TEXT DEFAULT NULL,
    p_priority    TEXT DEFAULT 'medium'
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_case lawyer_cases%ROWTYPE;
    v_conversation_id UUID;
BEGIN
    INSERT INTO public.lawyer_cases
        (lawyer_user_id, case_name, case_type, priority, status,
         description, case_number, court_name)
    VALUES
        (p_user_id, p_case_name,
         p_case_type::case_type_enum,
         p_priority::case_priority_enum,
         'active'::case_status_enum,
         p_description, p_case_number, p_court_name)
    RETURNING * INTO v_case;

    INSERT INTO public.conversations (user_id, case_id, title_ar)
    VALUES (p_user_id, v_case.case_id,
            left('محادثة - ' || p_case_name, 500))
    RETURNING conversation_id INTO v_conversation_id;

    -- Whole function body is one transaction: any failure rolls back both.
    RETURN jsonb_build_object(
        'case', to_jsonb(v_case) - 'embedding',
        'first_conversation_id', v_conversation_id
    );
END;
$$;

COMMENT ON FUNCTION public.create_case_with_conversation(UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) IS
    'Atomically inserts a lawyer_case and its first conversation. Replaces the two-step non-transactional write in case_service.create_case.';

REVOKE ALL ON FUNCTION public.create_case_with_conversation(UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_case_with_conversation(UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) TO service_role;

CREATE OR REPLACE FUNCTION public.soft_delete_case_cascade(
    p_case_id UUID,
    p_user_id UUID
)
RETURNS INT
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_now TIMESTAMPTZ := now();
    v_case_rows INT;
    v_conv_rows INT;
BEGIN
    UPDATE public.lawyer_cases
       SET deleted_at = v_now, updated_at = v_now
     WHERE case_id = p_case_id
       AND lawyer_user_id = p_user_id
       AND deleted_at IS NULL;
    GET DIAGNOSTICS v_case_rows = ROW_COUNT;

    IF v_case_rows = 0 THEN
        RETURN -1;  -- not found / not owned / already deleted -> caller maps to 404
    END IF;

    UPDATE public.conversations
       SET deleted_at = v_now, updated_at = v_now
     WHERE case_id = p_case_id
       AND user_id = p_user_id
       AND deleted_at IS NULL;
    GET DIAGNOSTICS v_conv_rows = ROW_COUNT;

    RETURN v_conv_rows;
END;
$$;

COMMENT ON FUNCTION public.soft_delete_case_cascade(UUID, UUID) IS
    'Atomically soft-deletes a case and all its live conversations. Returns -1 if the case was not found/owned (caller maps to 404), else the number of conversations soft-deleted.';

REVOKE ALL ON FUNCTION public.soft_delete_case_cascade(UUID, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.soft_delete_case_cascade(UUID, UUID) TO service_role;

CREATE OR REPLACE FUNCTION public.case_counts(p_case_ids UUID[])
RETURNS TABLE (
    case_id            UUID,
    conversation_count BIGINT,
    document_count     BIGINT
)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
    SELECT
        ids.case_id,
        COALESCE(conv.cnt, 0) AS conversation_count,
        COALESCE(doc.cnt, 0)  AS document_count
    FROM unnest(p_case_ids) AS ids(case_id)
    LEFT JOIN (
        SELECT c.case_id, count(*) AS cnt
        FROM public.conversations c
        WHERE c.case_id = ANY(p_case_ids) AND c.deleted_at IS NULL
        GROUP BY c.case_id
    ) conv USING (case_id)
    LEFT JOIN (
        SELECT d.case_id, count(*) AS cnt
        FROM public.case_documents d
        WHERE d.case_id = ANY(p_case_ids) AND d.deleted_at IS NULL
        GROUP BY d.case_id
    ) doc USING (case_id);
$$;

COMMENT ON FUNCTION public.case_counts(UUID[]) IS
    'Batched non-deleted conversation + document counts per case. Replaces the per-case count loop in case_service.list_cases (1+2N -> 2 queries).';

REVOKE ALL ON FUNCTION public.case_counts(UUID[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.case_counts(UUID[]) TO service_role;
