-- 161_close_remaining_anon_corpus_leaks.sql
--
-- Completes 160. That migration revoked the BASE tables, which was necessary
-- but not sufficient — a live re-probe with the publishable anon key right
-- afterwards still returned:
--
--   GET /rest/v1/chunks_v2?select=*     -> 206, 48,429 rows, full article text
--   GET /rest/v1/case_sections?select=* -> 206, 61,939 rows
--   GET /rest/v1/bm25_terms?select=*    -> 206, 66,538 rows
--   GET /rest/v1/services?select=*      -> 206,  4,985 rows
--   GET /rest/v1/entities?select=*      -> 206,    400 rows
--
-- Two things 160 missed:
--
-- 1. THE VIEW. `chunks_v2` is a VIEW over regulation_v2.chunks carrying
--    `security_invoker = false`, so it executes as its owner (postgres,
--    BYPASSRLS) and never consults the base table's RLS. Revoking the base
--    table in 160 therefore changed nothing for anyone reading the view — the
--    entire regulation text stayed one anonymous GET away. Its four sibling
--    views (articles_v2, regulations_v2, chunk_titles_v2, cross_references_v2)
--    already carry security_invoker = on, which is exactly why those started
--    returning 401 the moment 160 landed. `chunks_v2` was the odd one out.
--
--    This is the same footgun 129a fixed on `user_subscriptions_live`.
--
-- 2. MORE PERMISSIVE POLICIES. case_sections / entities / services carry
--    `USING (true)` for anon+authenticated; bm25_terms / bm25_corpus_stats
--    carry it for role `public`, which is broader still — it covers every role
--    including anon. bm25_terms is the search vocabulary: 66,538 lexemes with
--    document frequencies, i.e. a readable map of the corpus.
--
-- `search_index` is deliberately left alone: its policies are already scoped
-- (`owner_user_id IS NULL` for public rows, owner-match for private ones), so
-- it leaks no private row. Its anon grant is narrowed anyway, since the browser
-- never queries it directly.
--
-- Safe for the same reason as 160, re-verified: the frontend makes ZERO
-- supabase-js `.from()` calls against any of these relations, and every backend
-- reader uses the service-role client, which bypasses both grants and RLS.
--
-- Idempotent: safe to re-run.

begin;

-- ---------------------------------------------------------------------------
-- 1. The view that bypassed everything. security_invoker makes it run as the
--    CALLER, so regulation_v2.chunks' RLS (and 160's revoked grant) finally
--    apply to it. Brings it in line with its four siblings.
-- ---------------------------------------------------------------------------
alter view public.chunks_v2 set (security_invoker = true);

-- Belt and braces: the browser has no business reading it under any role.
revoke all privileges on public.chunks_v2 from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. Remaining permissive corpus policies.
-- ---------------------------------------------------------------------------
drop policy if exists case_sections_public_read on public.case_sections;
drop policy if exists entities_public_read      on public.entities;
drop policy if exists services_public_read      on public.services;

-- These two were granted to role `public` — broader than anon, so they must be
-- dropped by name rather than revoked per-role.
drop policy if exists bm25_terms_read        on public.bm25_terms;
drop policy if exists bm25_corpus_stats_read on public.bm25_corpus_stats;

revoke all privileges on public.case_sections     from anon, authenticated;
revoke all privileges on public.entities          from anon, authenticated;
revoke all privileges on public.services          from anon, authenticated;
revoke all privileges on public.bm25_terms        from anon, authenticated;
revoke all privileges on public.bm25_corpus_stats from anon, authenticated;

-- search_index keeps its scoped policies; only the direct grant goes.
revoke all privileges on public.search_index from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. RLS stays on for every base table touched here.
-- ---------------------------------------------------------------------------
alter table public.case_sections     enable row level security;
alter table public.entities          enable row level security;
alter table public.services          enable row level security;
alter table public.bm25_terms        enable row level security;
alter table public.bm25_corpus_stats enable row level security;
alter table public.search_index      enable row level security;

commit;
