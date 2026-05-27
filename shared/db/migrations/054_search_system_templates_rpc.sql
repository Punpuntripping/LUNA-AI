-- Migration 054: search_system_templates RPC — pgvector cosine retrieval
-- over system_templates.summary_embedding with an optional type filter.
--
-- Called by agents/writing_executor/planner/distill/template_search.py.
-- v1 ships with zero ingested rows (system_templates ingestion is a
-- separate follow-up plan); this RPC will silently return an empty set
-- until ingestion populates `summary_embedding`. The Python caller's
-- empty-result path is exercised on every clean turn until then.
--
-- Returns rows ordered by ascending cosine distance (best match first),
-- capped at top_n.
--
-- Score convention: returns `1 - cosine_distance` so 1.0 = perfect match,
-- 0.0 = orthogonal, < 0 = opposite. The caller (TemplateRef.score) treats
-- this as a debug/telemetry value only — ranking is already encoded in row
-- order.
--
-- Dependencies:
--   - 046_system_templates.sql (system_templates table + HNSW index)
--
-- This migration is idempotent.

CREATE OR REPLACE FUNCTION public.search_system_templates(
    query_embedding vector(1024),
    type_filter     TEXT DEFAULT NULL,
    top_n           INT  DEFAULT 5
)
RETURNS TABLE (
    template_id   UUID,
    template_type TEXT,
    title         TEXT,
    body_md       TEXT,
    score         FLOAT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, extensions
AS $$
    SELECT
        st.template_id,
        st.type::TEXT                                  AS template_type,
        st.title,
        st.content                                     AS body_md,
        (1.0 - (st.summary_embedding <=> query_embedding))::FLOAT  AS score
    FROM public.system_templates st
    WHERE st.deleted_at        IS NULL
      AND st.summary_embedding IS NOT NULL
      AND (type_filter IS NULL OR st.type::TEXT = type_filter)
    ORDER BY st.summary_embedding <=> query_embedding
    LIMIT GREATEST(COALESCE(top_n, 5), 1);
$$;

COMMENT ON FUNCTION public.search_system_templates(vector, TEXT, INT) IS
    'pgvector cosine retrieval over system_templates.summary_embedding. '
    'Returns up to top_n rows matching the optional type_filter (Arabic enum value), '
    'ordered best-first. Score = 1 - cosine_distance (1.0 = perfect, 0.0 = orthogonal).';

GRANT EXECUTE ON FUNCTION public.search_system_templates(vector, TEXT, INT) TO authenticated;
