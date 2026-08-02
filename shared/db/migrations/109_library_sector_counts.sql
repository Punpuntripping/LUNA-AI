-- ============================================================================
-- Migration 109 — per-sector corpus counts for the public library
-- Plan: .claude/plans/library_sectors.md §5 (cap-policy amendment) · §7.2
--
-- §5 turns a sector from a FILTER into a SECTION: the 38-value vocabulary is
-- validated server-side, so a sector page gets REAL counts and a REAL page
-- count instead of the flat anon ceiling. That is 152 numbers (38 sectors × 4
-- wings) and §5 is explicit that they must come from ONE grouped query per
-- refresh, not 152 count queries.
--
-- PostgREST cannot express `unnest(sectors) ... GROUP BY 1` — the array columns
-- have to be expanded server-side — so the grouped query needs an RPC. This is
-- it. `backend/app/services/library_service.sector_counts()` calls it once per
-- 5-minute memo refresh and the route derives every sector page count from the
-- result.
--
-- ⚠ WHY SECURITY DEFINER — AND WHY THE ORIGINAL GRANT WAS THE MISTAKE.
-- The four corpora are pipeline-owned and are NOT readable by `anon`; the public
-- library reads them through the SERVICE-ROLE client (`backend/app/deps.py`
-- `get_supabase()`), which is the only caller this function has. DEFINER is
-- therefore about the function reading the corpora, not about who may call it.
--
-- This migration originally granted EXECUTE to `anon, authenticated` as well, on
-- the reasoning that the counts are public information anyway. That was wrong,
-- and migration 110 revokes it. The counts being publishable says nothing about
-- who should be able to COMPUTE them: the grant put an unauthenticated,
-- unmetered, un-memoised trigger for four full `unnest` aggregations on the
-- Supabase hostname — outside the origin lock, outside the rate limiter and
-- outside the backend's 5-minute memo, against the same Postgres that serves
-- chat. A cheap amplification lever bought for no benefit, since no anon client
-- ever calls it.
--
-- What remains true: the function takes ZERO arguments (nothing to inject),
-- returns ONLY aggregate counts over a closed 38-value vocabulary, never row
-- data, and `SET search_path = public` pins the tables it can see.
--
-- ⚠ T6 — DO NOT add a GIN index to `regulations_v2` to make this faster. It is a
-- VIEW over the pipeline-owned `regulation_v2` schema: the DDL fails, and the
-- underlying schema is not ours to alter. 3,373 rows seq-scan fine.
--
-- Idempotent (CREATE OR REPLACE). Returns 38 rows, one per sector present in at
-- least one corpus; a sector with zero rows everywhere is simply absent and the
-- backend seeds it to zero (`shared/library/sectors.py` owns the 38-slug
-- vocabulary, this function does not).
--
-- ⚠ THESE COLUMNS DO NOT SUM TO THE CORPUS TOTALS, in either direction, and no
-- caller may treat them as if they did:
--   * `unnest` counts a row ONCE PER SECTOR, and rows are multi-sector, so the
--     columns OVER-count (verified 2026-08-01: regulations sum 8,971 over a
--     3,373-row corpus; judgments sum 31,924).
--   * `cases.legal_domains` is only 67.7% populated (20,671 rows of 30,531 carry
--     at least one domain — plan D10), so the judgment columns also MISS 9,860
--     judgments entirely.
-- The unfiltered hub totals are counted separately — see
-- `library_service.library_corpus_counts()`.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.library_sector_counts()
RETURNS TABLE (
    sector      text,
    regulations bigint,
    judgments   bigint,
    compliance  bigint,
    circulars   bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO 'public'
AS $$
    WITH r AS (
        SELECT unnest(sectors) AS s, count(*) AS c
        FROM public.regulations_v2 GROUP BY 1
    ),
    j AS (
        SELECT unnest(legal_domains) AS s, count(*) AS c
        FROM public.cases GROUP BY 1
    ),
    v AS (
        SELECT unnest(sectors) AS s, count(*) AS c
        FROM public.services GROUP BY 1
    ),
    k AS (
        SELECT unnest(sectors) AS s, count(*) AS c
        FROM public.circulars GROUP BY 1
    )
    SELECT coalesce(r.s, j.s, v.s, k.s) AS sector,
           coalesce(r.c, 0) AS regulations,
           coalesce(j.c, 0) AS judgments,
           coalesce(v.c, 0) AS compliance,
           coalesce(k.c, 0) AS circulars
    FROM r
    FULL JOIN j ON j.s = r.s
    FULL JOIN v ON v.s = coalesce(r.s, j.s)
    FULL JOIN k ON k.s = coalesce(r.s, j.s, v.s);
$$;

COMMENT ON FUNCTION public.library_sector_counts() IS
  'Per-sector item counts across the four public-library corpora '
  '(regulations_v2 / cases / services / circulars), one row per sector — the '
  'ONE grouped query behind the 152 sector×wing counts of library_sectors.md '
  '§5. SECURITY DEFINER because the corpora are pipeline-owned and anon has no '
  'row access, but the function returns aggregate counts over a closed, '
  'server-validated 38-value vocabulary and takes no arguments. Never returns '
  'row data.';

-- ⚠ The `anon, authenticated` half of this grant is REVOKED by migration 110 —
-- see the header. Left here as applied history; do not re-add it.
REVOKE EXECUTE ON FUNCTION public.library_sector_counts() FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION public.library_sector_counts()
    TO anon, authenticated, service_role;
