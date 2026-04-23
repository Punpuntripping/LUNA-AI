-- Migration 021: Hybrid search performance fixes
-- Date: 2026-04-05
-- Context: Re-embedded all tables with Qwen4 at 1024 dimensions (was 4096)
--
-- Changes:
--   1. Role timeouts: 3s/8s/8s → 30s/30s/30s
--   2. All 5 hybrid search functions rewritten:
--      - Removed subvector(embedding,1,2000)::halfvec(2000) → native embedding <=>
--      - hybrid_search_articles: removed relevant_reg_ids CTE (was 4s brute force)
--      - hybrid_search_sections: removed JOIN from semantic CTE
--   3. HNSW indexes on all 5 tables (run AFTER ingestion completes)

-- ============================================================
-- Fix 1: Increase role-level statement timeouts
-- ============================================================
ALTER ROLE authenticator SET statement_timeout = '30s';
ALTER ROLE authenticated SET statement_timeout = '30s';
ALTER ROLE anon SET statement_timeout = '30s';
NOTIFY pgrst, 'reload config';

-- ============================================================
-- Fix 2: hybrid_search_articles — native 1024-dim, no relevant_reg_ids
-- ============================================================
CREATE OR REPLACE FUNCTION public.hybrid_search_articles(
  query_text text,
  query_embedding vector,
  match_count integer DEFAULT 10,
  full_text_weight double precision DEFAULT 1.0,
  semantic_weight double precision DEFAULT 1.0,
  rrf_k integer DEFAULT 60,
  filter_regulation_id uuid DEFAULT NULL::uuid,
  filter_entity_id uuid DEFAULT NULL::uuid
)
RETURNS TABLE(
  id uuid, chunk_ref text, title text, content text,
  article_context text, article_num integer, regulation_id uuid,
  regulation_ref text, regulation_title text,
  section_title text, section_context text,
  score double precision
)
LANGUAGE sql STABLE
AS $function$
WITH
full_text AS (
  SELECT a.id,
    ROW_NUMBER() OVER (ORDER BY ts_rank(a.fts, websearch_to_tsquery('arabic', query_text)) DESC) AS rank
  FROM articles a
  JOIN regulations r ON r.id = a.regulation_id
  WHERE a.fts @@ websearch_to_tsquery('arabic', query_text)
    AND (filter_regulation_id IS NULL OR a.regulation_id = filter_regulation_id)
    AND (filter_entity_id IS NULL OR r.entity_id = filter_entity_id)
  LIMIT match_count * 2
),
semantic AS (
  SELECT a.id,
    ROW_NUMBER() OVER (ORDER BY a.embedding <=> query_embedding) AS rank
  FROM articles a
  WHERE a.embedding IS NOT NULL
    AND (filter_regulation_id IS NULL OR a.regulation_id = filter_regulation_id)
  ORDER BY a.embedding <=> query_embedding
  LIMIT match_count * 2
)
SELECT
  a.id, a.chunk_ref, a.title, a.content,
  a.article_context, a.article_num, a.regulation_id,
  r.regulation_ref, r.title AS regulation_title,
  s.title AS section_title, s.section_context,
  (COALESCE(1.0 / (rrf_k + ft.rank), 0.0) * full_text_weight +
   COALESCE(1.0 / (rrf_k + sem.rank), 0.0) * semantic_weight)::double precision AS score
FROM full_text ft
FULL OUTER JOIN semantic sem ON ft.id = sem.id
JOIN articles a ON a.id = COALESCE(ft.id, sem.id)
JOIN regulations r ON r.id = a.regulation_id
LEFT JOIN sections s ON s.id = a.section_id
WHERE (filter_entity_id IS NULL OR r.entity_id = filter_entity_id)
ORDER BY score DESC
LIMIT match_count;
$function$;

-- ============================================================
-- Fix 3: hybrid_search_sections — native 1024-dim, no JOIN in semantic CTE
-- ============================================================
CREATE OR REPLACE FUNCTION public.hybrid_search_sections(
  query_text text,
  query_embedding vector,
  match_count integer DEFAULT 10,
  full_text_weight double precision DEFAULT 1.0,
  semantic_weight double precision DEFAULT 1.0,
  rrf_k integer DEFAULT 60,
  filter_entity_id uuid DEFAULT NULL::uuid,
  filter_regulation_id uuid DEFAULT NULL::uuid
)
RETURNS TABLE(
  id uuid, chunk_ref text, title text, content text,
  section_summary text, section_context text, section_keyword text,
  regulation_ref text, regulation_title text,
  score double precision
)
LANGUAGE sql STABLE
AS $function$
WITH full_text AS (
  SELECT s.id,
    ROW_NUMBER() OVER (ORDER BY ts_rank(s.fts, websearch_to_tsquery('arabic', query_text)) DESC) AS rank
  FROM sections s
  JOIN regulations r ON r.id = s.regulation_id
  WHERE s.fts @@ websearch_to_tsquery('arabic', query_text)
    AND (filter_entity_id IS NULL OR r.entity_id = filter_entity_id)
    AND (filter_regulation_id IS NULL OR s.regulation_id = filter_regulation_id)
  LIMIT match_count * 2
),
semantic AS (
  SELECT s.id,
    ROW_NUMBER() OVER (ORDER BY s.embedding <=> query_embedding) AS rank
  FROM sections s
  WHERE s.embedding IS NOT NULL
    AND (filter_regulation_id IS NULL OR s.regulation_id = filter_regulation_id)
  ORDER BY s.embedding <=> query_embedding
  LIMIT match_count * 2
)
SELECT
  s.id, s.chunk_ref, s.title, s.content,
  s.section_summary, s.section_context, s.section_keyword,
  r.regulation_ref, r.title AS regulation_title,
  (COALESCE(1.0 / (rrf_k + ft.rank), 0.0) * full_text_weight +
   COALESCE(1.0 / (rrf_k + sem.rank), 0.0) * semantic_weight)::double precision AS score
FROM full_text ft
FULL OUTER JOIN semantic sem ON ft.id = sem.id
JOIN sections s ON s.id = COALESCE(ft.id, sem.id)
JOIN regulations r ON r.id = s.regulation_id
WHERE (filter_entity_id IS NULL OR r.entity_id = filter_entity_id)
ORDER BY score DESC
LIMIT match_count;
$function$;

-- ============================================================
-- Fix 4: hybrid_search_regulations — native 1024-dim
-- ============================================================
CREATE OR REPLACE FUNCTION public.hybrid_search_regulations(
  query_text text,
  query_embedding vector,
  match_count integer DEFAULT 5,
  full_text_weight double precision DEFAULT 1.0,
  semantic_weight double precision DEFAULT 1.0,
  rrf_k integer DEFAULT 60,
  filter_entity_id uuid DEFAULT NULL::uuid,
  filter_main_category text DEFAULT NULL::text
)
RETURNS TABLE(
  id uuid, regulation_ref text, title text, type text,
  main_category text, sub_category text, regulation_summary text,
  authority_level text, authority_score integer,
  score double precision
)
LANGUAGE sql STABLE
AS $function$
WITH full_text AS (
  SELECT r.id,
    ROW_NUMBER() OVER (ORDER BY ts_rank(r.fts, websearch_to_tsquery('arabic', query_text)) DESC) AS rank
  FROM regulations r
  WHERE r.fts @@ websearch_to_tsquery('arabic', query_text)
    AND (filter_entity_id IS NULL OR r.entity_id = filter_entity_id)
    AND (filter_main_category IS NULL OR r.main_category = filter_main_category)
  LIMIT match_count * 2
),
semantic AS (
  SELECT r.id,
    ROW_NUMBER() OVER (ORDER BY r.embedding <=> query_embedding) AS rank
  FROM regulations r
  WHERE r.embedding IS NOT NULL
    AND (filter_entity_id IS NULL OR r.entity_id = filter_entity_id)
    AND (filter_main_category IS NULL OR r.main_category = filter_main_category)
  ORDER BY r.embedding <=> query_embedding
  LIMIT match_count * 2
)
SELECT
  r.id, r.regulation_ref, r.title, r.type,
  r.main_category, r.sub_category, r.regulation_summary,
  r.authority_level, r.authority_score,
  (COALESCE(1.0 / (rrf_k + ft.rank), 0.0) * full_text_weight +
   COALESCE(1.0 / (rrf_k + sem.rank), 0.0) * semantic_weight)::double precision AS score
FROM full_text ft
FULL OUTER JOIN semantic sem ON ft.id = sem.id
JOIN regulations r ON r.id = COALESCE(ft.id, sem.id)
ORDER BY score DESC
LIMIT match_count;
$function$;

-- ============================================================
-- Fix 5: hybrid_search_cases — native 1024-dim
-- ============================================================
CREATE OR REPLACE FUNCTION public.hybrid_search_cases(
  query_text text,
  query_embedding vector,
  match_count integer DEFAULT 10,
  full_text_weight double precision DEFAULT 1.0,
  semantic_weight double precision DEFAULT 1.0,
  rrf_k integer DEFAULT 60
)
RETURNS TABLE(
  id uuid, case_ref text, court text, court_level text, case_variant text,
  case_number text, judgment_number text, city text, date_hijri text, details_url text,
  content text, legal_domains jsonb, referenced_regulations jsonb, reference_count integer,
  appeal_court text, appeal_result text, appeal_date_hijri text,
  score double precision
)
LANGUAGE sql STABLE
AS $function$
WITH full_text AS (
  SELECT c.id,
    ROW_NUMBER() OVER (ORDER BY ts_rank(c.fts, websearch_to_tsquery('arabic', query_text)) DESC) AS rank
  FROM cases c
  WHERE c.fts @@ websearch_to_tsquery('arabic', query_text)
  LIMIT match_count * 2
),
semantic AS (
  SELECT c.id,
    ROW_NUMBER() OVER (ORDER BY c.embedding <=> query_embedding) AS rank
  FROM cases c
  WHERE c.embedding IS NOT NULL
  ORDER BY c.embedding <=> query_embedding
  LIMIT match_count * 2
)
SELECT
  c.id, c.case_ref, c.court, c.court_level, c.case_variant,
  c.case_number, c.judgment_number, c.city, c.date_hijri, c.details_url,
  c.content, c.legal_domains, c.referenced_regulations, c.reference_count,
  c.appeal_court, c.appeal_result, c.appeal_date_hijri,
  (COALESCE(1.0 / (rrf_k + ft.rank), 0.0) * full_text_weight +
   COALESCE(1.0 / (rrf_k + sem.rank), 0.0) * semantic_weight)::float AS score
FROM full_text ft
FULL OUTER JOIN semantic sem ON ft.id = sem.id
JOIN cases c ON c.id = COALESCE(ft.id, sem.id)
ORDER BY score DESC
LIMIT match_count;
$function$;

-- ============================================================
-- Fix 6: hybrid_search_services — native 1024-dim
-- ============================================================
CREATE OR REPLACE FUNCTION public.hybrid_search_services(
  query_text text,
  query_embedding vector,
  match_count integer DEFAULT 10,
  full_text_weight double precision DEFAULT 1.0,
  semantic_weight double precision DEFAULT 1.0,
  rrf_k integer DEFAULT 60
)
RETURNS TABLE(
  id uuid, service_ref text, service_name_ar text, provider_name text,
  category text, platform_name text, url text, service_url text,
  service_markdown text, service_context text,
  score double precision
)
LANGUAGE sql STABLE
AS $function$
WITH full_text AS (
  SELECT s.id,
    ROW_NUMBER() OVER (ORDER BY ts_rank(s.fts, websearch_to_tsquery('arabic', query_text)) DESC) AS rank
  FROM services s
  WHERE s.fts @@ websearch_to_tsquery('arabic', query_text)
  LIMIT match_count * 2
),
semantic AS (
  SELECT s.id,
    ROW_NUMBER() OVER (ORDER BY s.embedding <=> query_embedding) AS rank
  FROM services s
  WHERE s.embedding IS NOT NULL
  ORDER BY s.embedding <=> query_embedding
  LIMIT match_count * 2
)
SELECT
  s.id, s.service_ref, s.service_name_ar, s.provider_name,
  s.category, s.platform_name, s.url, s.service_url,
  s.service_markdown, s.service_context,
  (COALESCE(1.0 / (rrf_k + ft.rank), 0.0) * full_text_weight +
   COALESCE(1.0 / (rrf_k + sem.rank), 0.0) * semantic_weight)::float AS score
FROM full_text ft
FULL OUTER JOIN semantic sem ON ft.id = sem.id
JOIN services s ON s.id = COALESCE(ft.id, sem.id)
ORDER BY score DESC
LIMIT match_count;
$function$;

-- ============================================================
-- Fix 7: HNSW indexes — RUN AFTER INGESTION COMPLETES
-- All tables are now vector(1024), native HNSW (no subvector needed)
-- ============================================================
-- CREATE INDEX CONCURRENTLY idx_articles_embedding_hnsw
--   ON articles USING hnsw (embedding vector_cosine_ops);
-- CREATE INDEX CONCURRENTLY idx_sections_embedding_hnsw
--   ON sections USING hnsw (embedding vector_cosine_ops);
-- CREATE INDEX CONCURRENTLY idx_regulations_embedding_hnsw
--   ON regulations USING hnsw (embedding vector_cosine_ops);
-- CREATE INDEX CONCURRENTLY idx_cases_embedding_hnsw
--   ON cases USING hnsw (embedding vector_cosine_ops);
-- CREATE INDEX CONCURRENTLY idx_services_embedding_hnsw
--   ON services USING hnsw (embedding vector_cosine_ops);
