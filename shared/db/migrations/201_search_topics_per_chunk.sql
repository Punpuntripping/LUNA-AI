-- 201: deep search can take more than one chunk of a regulation per sub-query.
--
-- public.search_topics keeps one row per doc_id (DISTINCT ON), so for each
-- sub-query only a law's best chunk reached the reranker, however strong the
-- others were. See agents_reports/search_one_chunk_per_regulation_2026-11-01.md.
--
-- This is a NEW function on purpose. search_topics stays exactly as it is, so
-- simple_search (manual_search, compliance) keeps one result per document, and
-- rolling back means pointing deep search at the old name again.
--
-- Regulation and appendix rows: best topic per chunk, then the top p_per_type,
-- with no limit per regulation. Circulars and services are one item per
-- document, so they come from search_topics exactly as today.
--
-- The window stays at today's 60 topic matches per type, and the sector filter
-- still applies after it. A wider window cost more database work and did not
-- improve the candidates when replayed on 573 real sub-queries, so more rows
-- here cost the index nothing.

BEGIN;

CREATE OR REPLACE FUNCTION public.search_topics_per_chunk(
    p_query_embedding vector,
    p_per_type        integer DEFAULT 25,
    p_sectors         text[]  DEFAULT NULL::text[],
    p_window          integer DEFAULT 60
)
RETURNS TABLE(
    source_type text, topic_id uuid, topic_text text, score real, source_ref text,
    source_id uuid, doc_ref text, doc_id uuid, title text, sectors text[], entity_ref text
)
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
    v_per_type INT;
    v_window   INT;
    v_ef       INT;
BEGIN
    v_per_type := GREATEST(1, COALESCE(p_per_type, 25));
    v_window   := GREATEST(1, COALESCE(p_window, 60));
    v_ef       := LEAST(1000, GREATEST(80, v_window * 2));
    PERFORM set_config('hnsw.ef_search', v_ef::text, true);

    RETURN QUERY

    (
        SELECT d.source_type, d.topic_id, d.topic_text, d.score, d.source_ref,
               d.source_id, d.doc_ref, d.doc_id, d.title, d.sectors, d.entity_ref
        FROM (
            SELECT DISTINCT ON (h.source_id)
                   h.source_type, h.id AS topic_id, h.topic_text, h.score,
                   h.source_ref, h.source_id, h.doc_ref, h.doc_id,
                   c.title, r.sectors, r.entity_ref
            FROM (
                SELECT st.id, st.source_type, st.source_ref, st.source_id,
                       st.doc_ref, st.doc_id, st.topic_text,
                       (1 - (st.topic_embedding <=> p_query_embedding))::REAL AS score
                FROM public.search_topics st
                WHERE st.source_type = 'regulation'
                  AND st.topic_embedding IS NOT NULL
                ORDER BY st.topic_embedding <=> p_query_embedding
                LIMIT v_window
            ) h
            JOIN regulation_v2.chunks      c ON c.id = h.source_id
            JOIN regulation_v2.regulations r ON r.id = h.doc_id
            WHERE p_sectors IS NULL OR r.sectors && p_sectors
            ORDER BY h.source_id, h.score DESC
        ) d
        ORDER BY d.score DESC
        LIMIT v_per_type
    )

    UNION ALL

    (
        SELECT d.source_type, d.topic_id, d.topic_text, d.score, d.source_ref,
               d.source_id, d.doc_ref, d.doc_id, d.title, d.sectors, d.entity_ref
        FROM (
            SELECT DISTINCT ON (h.source_id)
                   h.source_type, h.id AS topic_id, h.topic_text, h.score,
                   h.source_ref, h.source_id, h.doc_ref, h.doc_id,
                   c.title, r.sectors, r.entity_ref
            FROM (
                SELECT st.id, st.source_type, st.source_ref, st.source_id,
                       st.doc_ref, st.doc_id, st.topic_text,
                       (1 - (st.topic_embedding <=> p_query_embedding))::REAL AS score
                FROM public.search_topics st
                WHERE st.source_type = 'appendix'
                  AND st.topic_embedding IS NOT NULL
                ORDER BY st.topic_embedding <=> p_query_embedding
                LIMIT v_window
            ) h
            JOIN regulation_v2.chunks      c ON c.id = h.source_id
            JOIN regulation_v2.regulations r ON r.id = h.doc_id
            WHERE p_sectors IS NULL OR r.sectors && p_sectors
            ORDER BY h.source_id, h.score DESC
        ) d
        ORDER BY d.score DESC
        LIMIT v_per_type
    )

    UNION ALL

    (
        SELECT t.source_type, t.topic_id, t.topic_text, t.score, t.source_ref,
               t.source_id, t.doc_ref, t.doc_id, t.title, t.sectors, t.entity_ref
        FROM public.search_topics(
            p_query_embedding, ARRAY['circular', 'service'], 15,
            p_sectors, NULL::text, 4
        ) t
    )

    ORDER BY 4 DESC;
END;
$function$;

-- Deep search runs as service_role. New functions in public are open to
-- anon/authenticated by default, so close that here.
REVOKE ALL ON FUNCTION public.search_topics_per_chunk(vector, integer, text[], integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.search_topics_per_chunk(vector, integer, text[], integer)
    TO service_role;

DO $$
BEGIN
    IF has_function_privilege('anon', 'public.search_topics_per_chunk(vector, integer, text[], integer)', 'EXECUTE')
       OR has_function_privilege('authenticated', 'public.search_topics_per_chunk(vector, integer, text[], integer)', 'EXECUTE') THEN
        RAISE EXCEPTION '201: search_topics_per_chunk must not be callable by anon or authenticated';
    END IF;
    IF NOT has_function_privilege('service_role', 'public.search_topics_per_chunk(vector, integer, text[], integer)', 'EXECUTE') THEN
        RAISE EXCEPTION '201: search_topics_per_chunk must be callable by service_role';
    END IF;
    RAISE NOTICE '201: grants ok';
END $$;

NOTIFY pgrst, 'reload schema';

COMMIT;
