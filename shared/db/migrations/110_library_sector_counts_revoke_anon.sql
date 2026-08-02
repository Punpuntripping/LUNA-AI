-- ============================================================================
-- Migration 110 — take EXECUTE on library_sector_counts() back off anon
-- Security review 2026-08-01, finding F1 (MEDIUM). Amends migration 109.
--
-- 109 granted EXECUTE to `anon, authenticated, service_role` on the reasoning
-- that the sector counts are public information — they are printed on the browse
-- grid, in the tab chips and in the nav copy. That conflated two different
-- questions. The counts being PUBLISHABLE says nothing about who should be able
-- to COMPUTE them.
--
-- The only caller is the backend's SERVICE-ROLE client (`backend/app/deps.py`
-- `get_supabase()`), behind `_sector_counts()`'s 5-minute memo. The anon grant
-- added no capability the product uses and did add a real one for an attacker:
-- an unauthenticated, unmetered, un-memoised trigger for four full `unnest`
-- aggregations (30,531 `cases` rows among them), reachable on the Supabase
-- hostname — i.e. outside the origin lock, outside the rate limiter, outside
-- Cloudflare — against the same Postgres instance that serves chat. Cheap
-- amplification, bought for nothing.
--
-- SECURITY DEFINER stays: it is what lets the function read the pipeline-owned
-- corpora at all, and it is not the exposure. The grant was.
--
-- Idempotent: REVOKE on a role that no longer holds the privilege is a no-op,
-- and the GRANT below re-asserts the one role that must keep it.
-- ============================================================================

REVOKE EXECUTE ON FUNCTION public.library_sector_counts() FROM anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.library_sector_counts() TO service_role;

COMMENT ON FUNCTION public.library_sector_counts() IS
  'Per-sector item counts across the four public-library corpora '
  '(regulations_v2 / cases / services / circulars), one row per sector — the '
  'ONE grouped query behind the 152 sector×wing counts of library_sectors.md '
  '§5. SECURITY DEFINER because the corpora are pipeline-owned; SERVICE_ROLE '
  'ONLY (migration 110 — an anon grant is an unmetered amplification lever, not '
  'a feature: nothing outside the backend calls this). Takes no arguments and '
  'never returns row data.';
