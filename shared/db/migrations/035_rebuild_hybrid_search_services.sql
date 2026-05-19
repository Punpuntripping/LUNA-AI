-- 035_rebuild_hybrid_search_services.sql
-- Rebuild hybrid_search_services to match the re-shaped `services` table.
--
-- The `services` table was re-shaped (service_markdown -> original_markdown,
-- category/platform_name/target_audience/service_channels/service_name_en
-- dropped, sectors/is_proactive added) but the RPC was never regenerated.
-- Both surviving overloads still SELECT dropped columns, so every call raised
-- `column does not exist`, search.py swallowed it, and compliance_search
-- returned zero results in production. This migration is a fix, not an
-- enhancement.
--
-- Note: the compliance pipeline uses the compact `service_context` for both
-- the reranker view and the URA/aggregator view, so the full
-- `original_markdown` body is intentionally NOT returned by this RPC.
--
-- Strategy: drop EVERY overload by catalog signature (a wrong-arg-list
-- `DROP ... IF EXISTS` no-ops silently and would leave a stale overload,
-- re-triggering PGRST203), then a plain CREATE (OR REPLACE cannot change a
-- return signature or collapse overloads). `filter_category` is replaced by
-- `filter_sectors text[]`.

DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.proname = 'hybrid_search_services'
  LOOP
    EXECUTE 'DROP FUNCTION ' || r.sig::text;
  END LOOP;
END $$;

CREATE FUNCTION public.hybrid_search_services(
  query_text        text,
  query_embedding   vector(1024),
  match_count       integer          DEFAULT 10,
  full_text_weight  double precision DEFAULT 1.0,
  semantic_weight   double precision DEFAULT 1.0,
  rrf_k             integer          DEFAULT 60,
  filter_entity_id  uuid             DEFAULT NULL,
  filter_sectors    text[]           DEFAULT NULL
)
RETURNS TABLE(
  id uuid, service_ref text, service_name_ar text, provider_name text,
  service_context text, url text, service_url text,
  is_most_used boolean, is_proactive boolean, sectors text[],
  entity_id uuid, score double precision
)
LANGUAGE sql STABLE
AS $function$
WITH
full_text AS (
  SELECT s.id,
    ROW_NUMBER() OVER (ORDER BY ts_rank(s.fts, websearch_to_tsquery('arabic', query_text)) DESC) AS rank
  FROM services s
  WHERE s.fts @@ websearch_to_tsquery('arabic', query_text)
    AND (filter_entity_id IS NULL OR s.entity_id = filter_entity_id)
    AND (filter_sectors  IS NULL OR s.sectors && filter_sectors)
  LIMIT match_count * 2
),
semantic AS (
  SELECT s.id,
    ROW_NUMBER() OVER (ORDER BY s.embedding <=> query_embedding) AS rank
  FROM services s
  WHERE s.embedding IS NOT NULL
    AND (filter_entity_id IS NULL OR s.entity_id = filter_entity_id)
    AND (filter_sectors  IS NULL OR s.sectors && filter_sectors)
  ORDER BY s.embedding <=> query_embedding
  LIMIT match_count * 2
)
SELECT
  s.id, s.service_ref, s.service_name_ar, s.provider_name,
  LEFT(s.service_context, 2000)     AS service_context,
  s.url, s.service_url, s.is_most_used, s.is_proactive, s.sectors, s.entity_id,
  (COALESCE(1.0/(rrf_k+ft.rank),0.0)*full_text_weight +
   COALESCE(1.0/(rrf_k+sem.rank),0.0)*semantic_weight)::double precision AS score
FROM full_text ft
FULL OUTER JOIN semantic sem ON ft.id = sem.id
JOIN services s ON s.id = COALESCE(ft.id, sem.id)
ORDER BY score DESC
LIMIT match_count;
$function$;
