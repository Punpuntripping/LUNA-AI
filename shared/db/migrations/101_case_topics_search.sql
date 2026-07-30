-- 101_case_topics_search.sql
--
-- Retarget deep_search_v4/case_search from `case_sections` onto the new
-- `case_topics` corpus (ingested 2026-07-21/22).
--
-- Why:
--   `case_sections` (built 2026-05-16) covers only 20,669 of 30,531 cases.
--   `case_topics` covers 29,734 — so 9,861 cases are currently unreachable by
--   the `search_case_sections` path. See .claude/plans/case_topics_loop.md §1.1.
--
--   `case_topics` shipped WITHOUT a vector index. Measured on prod: one top-60
--   ANN query against kind='principle' alone took 21,619 ms (parallel bitmap
--   heap scan over 61k rows). This migration is the blocking prerequisite for
--   every downstream wave.
--
-- Contents:
--   1. Three PARTIAL HNSW indexes, one per `case_topic_kind`. Partial-per-kind
--      (not one global index) so the kind restriction happens INSIDE the ANN
--      traversal rather than as a post-filter — this is what "filter by basis
--      first, then search" means physically. Mirrors the existing
--      idx_case_sections_{principle,facts,basis}_vec shape exactly
--      (m=24, ef_construction=256).
--   2. `search_case_topics` RPC.
--
-- NOTE ON APPLICATION:
--   `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block, and the
--   Supabase MCP `apply_migration` wraps statements in one. Apply the index
--   statements individually via `execute_sql` (or psql), not through
--   apply_migration. `case_topics` is append-only from a batch ingester with no
--   live writers, so a plain (locking) build is acceptable if CONCURRENTLY is
--   unavailable.
--
-- Idempotent: IF NOT EXISTS on indexes, CREATE OR REPLACE on the function.

-- ---------------------------------------------------------------------------
-- 1. Partial HNSW indexes (one per kind)
-- ---------------------------------------------------------------------------

-- Names follow the table's existing `idx_ct_*` convention (idx_ct_case,
-- idx_ct_kind, idx_ct_entity) rather than the `idx_case_sections_*_vec` style,
-- and MATCH THE INDEXES ALREADY BUILT ON PROD (2026-07-24). Do not rename:
-- these three names are what exists live, and IF NOT EXISTS only protects
-- against a duplicate under the SAME name — a renamed copy would build a
-- second 470 MB graph per kind and double every write.

CREATE INDEX IF NOT EXISTS idx_ct_vec_principle
    ON public.case_topics USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 256)
    WHERE (kind = 'principle'::case_topic_kind);

CREATE INDEX IF NOT EXISTS idx_ct_vec_fact
    ON public.case_topics USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 256)
    WHERE (kind = 'fact'::case_topic_kind);

CREATE INDEX IF NOT EXISTS idx_ct_vec_basis
    ON public.case_topics USING hnsw (embedding vector_cosine_ops)
    WITH (m = 24, ef_construction = 256)
    WHERE (kind = 'basis'::case_topic_kind);

-- ---------------------------------------------------------------------------
-- 2. search_case_topics RPC
-- ---------------------------------------------------------------------------
--
-- Returns FLAT topic rows, deliberately NOT deduplicated by case.
--
-- This is the intentional difference from the regulation-side
-- `public.search_topics(...)` RPC, which does `DISTINCT ON (doc_id)`. Here a
-- case may legitimately surface via more than one matched topic within a single
-- sub-query (a case averages 2.1 principle / 3.6 fact / 3.7 basis topics), and
-- the reranker is shown all of them. Case-level grouping therefore happens in
-- Python (case_search/search.py), which keeps this function dumb and reusable.
--
-- `p_sectors` is retained but the case executor always passes NULL. Sector
-- filtering is disabled for cases on purpose: the 9,860 cases with an empty
-- `legal_domains` array are exactly the batch missing from `case_sections`, so
-- `legal_domains && p_sectors` silently drops all of them; and 91% of the
-- tagged cases carry المعاملات التجارية, so the filter buys almost no
-- selectivity. The parameter stays so the filter can be re-enabled after a
-- `legal_domains` backfill without a signature change.
--
-- WHY plpgsql WITH THREE LITERAL BRANCHES (and not one `WHERE t.kind = p_kind`):
--   The three HNSW indexes are PARTIAL (`WHERE kind = '<x>'`). Postgres can only
--   use a partial index when it can PROVE the index predicate holds. With
--   `WHERE t.kind = p_kind` the predicate is provable only while the planner is
--   producing a *custom* plan (parameter value substituted); once plan caching
--   promotes the statement to a *generic* plan, `kind = $1` is no longer provable
--   and the partial index silently drops out — degrading to a full scan of the
--   kind (measured: 21.6 s for `principle`). Neither this function nor the older
--   `search_case_sections` gets inlined (both plan as `Function Scan`), so that
--   risk is live.
--   Writing the kind as a LITERAL inside each branch makes the predicate
--   unconditionally provable, so the partial index is used on every plan shape.
--   The reg-side `public.search_topics` RPC uses the same literal-per-branch
--   construction for `source_type`.
--   Only the branch matching `p_kind` executes — unlike `search_topics`, which
--   gates its four branches *after* the ANN scan and therefore always runs all
--   four.

CREATE OR REPLACE FUNCTION public.search_case_topics(
    p_kind            case_topic_kind,
    p_query_embedding vector(1024),
    p_sectors         text[] DEFAULT NULL,
    p_match_count     int    DEFAULT 60
)
RETURNS TABLE (
    topic_id      uuid,
    topic_ref     text,
    case_id       uuid,
    case_ref      text,
    entity_ref    text,
    kind          text,
    topic_index   int,
    topic_text    text,
    attrs         jsonb,
    score         real,
    -- Case header, joined once so there is no N+1 enrichment hop downstream.
    court         text,
    city          text,
    court_level   text,
    case_number   text,
    date_hijri    text,
    short_summary text
)
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
    v_ef INT;
BEGIN
    -- pgvector HNSW returns AT MOST `hnsw.ef_search` candidates per scan, and
    -- the default is 40. MEASURED: without this line, `p_match_count = 60`
    -- returns only ~44 rows. That under-fetch is invisible at the call site —
    -- it reads as "the corpus had no more matches" rather than as a tuning
    -- knob, and it silently starves the case-grouping step downstream (which
    -- needs >= 25 distinct cases per sub-query).
    -- Same construction as the reg-side public.search_topics RPC. The third
    -- arg (true) makes it transaction-local, so it cannot leak into other
    -- queries on a pooled connection.
    v_ef := LEAST(1000, GREATEST(80, COALESCE(p_match_count, 60) * 2));
    PERFORM set_config('hnsw.ef_search', v_ef::text, true);

    IF p_kind = 'principle'::case_topic_kind THEN
        RETURN QUERY
        SELECT t.id, t.topic_ref, t.case_id, t.case_ref, t.entity_ref,
               t.kind::text, t.topic_index, t.text, t.attrs,
               (1 - (t.embedding <=> p_query_embedding))::REAL,
               c.court, c.city, c.court_level, c.case_number, c.date_hijri,
               c.short_summary
        FROM public.case_topics t JOIN public.cases c ON c.id = t.case_id
        WHERE t.kind = 'principle'::case_topic_kind
          AND t.embedding IS NOT NULL
          AND (p_sectors IS NULL OR c.legal_domains && p_sectors)
        ORDER BY t.embedding <=> p_query_embedding
        LIMIT p_match_count;

    ELSIF p_kind = 'fact'::case_topic_kind THEN
        RETURN QUERY
        SELECT t.id, t.topic_ref, t.case_id, t.case_ref, t.entity_ref,
               t.kind::text, t.topic_index, t.text, t.attrs,
               (1 - (t.embedding <=> p_query_embedding))::REAL,
               c.court, c.city, c.court_level, c.case_number, c.date_hijri,
               c.short_summary
        FROM public.case_topics t JOIN public.cases c ON c.id = t.case_id
        WHERE t.kind = 'fact'::case_topic_kind
          AND t.embedding IS NOT NULL
          AND (p_sectors IS NULL OR c.legal_domains && p_sectors)
        ORDER BY t.embedding <=> p_query_embedding
        LIMIT p_match_count;

    ELSIF p_kind = 'basis'::case_topic_kind THEN
        RETURN QUERY
        SELECT t.id, t.topic_ref, t.case_id, t.case_ref, t.entity_ref,
               t.kind::text, t.topic_index, t.text, t.attrs,
               (1 - (t.embedding <=> p_query_embedding))::REAL,
               c.court, c.city, c.court_level, c.case_number, c.date_hijri,
               c.short_summary
        FROM public.case_topics t JOIN public.cases c ON c.id = t.case_id
        WHERE t.kind = 'basis'::case_topic_kind
          AND t.embedding IS NOT NULL
          AND (p_sectors IS NULL OR c.legal_domains && p_sectors)
        ORDER BY t.embedding <=> p_query_embedding
        LIMIT p_match_count;
    END IF;
END;
$function$;

COMMENT ON FUNCTION public.search_case_topics(case_topic_kind, vector, text[], int) IS
    'Per-kind ANN search over case_topics for deep_search_v4/case_search. '
    'Returns flat topic rows (NOT deduped by case) joined to the case header; '
    'case-level grouping happens in Python so a case can carry >1 matched '
    'topic. p_sectors is always NULL from the executor — see migration header.';
