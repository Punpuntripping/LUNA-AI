-- 160_revoke_anon_corpus_access.sql
--
-- CRITICAL: close the anon PostgREST corpus exposure.
--
-- The publishable anon key ships inside the frontend JS bundle. Any visitor
-- could read it from devtools and page the entire corpus straight out of
-- PostgREST, bypassing the metered unlock ledger (library_unlocks/104), the
-- gate exposure budget, and the Cloudflare edge entirely.
--
-- Measured live on 2026-09-13 with the publishable anon key:
--   GET /rest/v1/cases?select=*                        -> 206, 30531 rows, full نص الحكم
--   GET /rest/v1/search_topics?select=*                -> 206, 176688 rows
--   GET /rest/v1/chunks  (Accept-Profile: regulation_v2) -> 206, 48429 rows, full article text
--
-- Two separate defects, both fixed here:
--   1. Permissive SELECT policies with USING (true) granted to role `anon`.
--   2. Table GRANTs to `anon` — including INSERT/UPDATE/DELETE/TRUNCATE on
--      circulars, search_topics and case_topics. Those writes were blocked only
--      by RLS having no permissive write policy; one careless policy away from
--      letting an anonymous visitor TRUNCATE the corpus.
--
-- Safe because nothing legitimate reads these tables with an anon or end-user
-- JWT (verified 2026-09-13):
--   * backend  — every corpus service takes `supabase` from `get_supabase`
--     (backend/app/deps.py:123) = app.state.supabase = the SERVICE-ROLE client,
--     which bypasses RLS and holds its own grants. Untouched by this migration.
--   * anon client — app.state.supabase_auth, used ONLY for GoTrue auth calls
--     (sign-in / sign-up / reset). Never issues a PostgREST table read.
--   * frontend — zero supabase-js `.from('<corpus table>')` calls; all corpus
--     content arrives through FastAPI.
--
-- Idempotent: safe to re-run.

begin;

-- ---------------------------------------------------------------------------
-- 1. Drop the permissive public-read policies.
-- ---------------------------------------------------------------------------
drop policy if exists cases_public_read          on public.cases;
drop policy if exists search_topics_public_read  on public.search_topics;
drop policy if exists service_guides_public_read on public.service_guides;

drop policy if exists chunks_public_read           on regulation_v2.chunks;
drop policy if exists chunk_titles_public_read     on regulation_v2.chunk_titles;
drop policy if exists regulations_public_read      on regulation_v2.regulations;
drop policy if exists articles_public_read         on regulation_v2.articles;
drop policy if exists cross_references_public_read on regulation_v2.cross_references;

-- ---------------------------------------------------------------------------
-- 2. Revoke every grant on corpus tables from anon and authenticated.
--    Neither role has any business touching the corpus directly — all reads
--    are mediated by FastAPI on the service-role client, which is where the
--    gate, the unlock ledger and the exposure budget are enforced.
-- ---------------------------------------------------------------------------
revoke all privileges on public.cases         from anon, authenticated;
revoke all privileges on public.case_topics   from anon, authenticated;
revoke all privileges on public.circulars     from anon, authenticated;
revoke all privileges on public.search_topics from anon, authenticated;
revoke all privileges on public.service_guides from anon, authenticated;

revoke all privileges on regulation_v2.chunks           from anon, authenticated;
revoke all privileges on regulation_v2.chunk_titles     from anon, authenticated;
revoke all privileges on regulation_v2.regulations      from anon, authenticated;
revoke all privileges on regulation_v2.articles         from anon, authenticated;
revoke all privileges on regulation_v2.cross_references from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. Stop PostgREST reaching the regulation_v2 schema as anon/authenticated
--    at all. Without USAGE the schema cannot be addressed via Accept-Profile,
--    which is how the 48,429 full-text chunks were being read.
-- ---------------------------------------------------------------------------
revoke usage on schema regulation_v2 from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 4. Make sure a future table in regulation_v2 does not silently inherit
--    grants to these roles.
-- ---------------------------------------------------------------------------
alter default privileges in schema regulation_v2
  revoke all on tables from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 5. RLS stays ON everywhere. With the policies gone and the grants revoked,
--    anon/authenticated are denied twice over; service_role bypasses both.
-- ---------------------------------------------------------------------------
alter table public.cases         enable row level security;
alter table public.case_topics   enable row level security;
alter table public.circulars     enable row level security;
alter table public.search_topics enable row level security;

commit;
