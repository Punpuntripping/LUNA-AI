-- 067_security_rls_lockdown.sql
-- Fixes the two CRITICAL findings from the 2026-06-11 security audit
-- (agents_reports/security_review_2026-06-11.md):
--
--   C-1  storage.objects `documents` bucket: SELECT/UPDATE/DELETE policies
--        only checked `owner_id IS NOT NULL` (not that the owner is the
--        caller), so any authenticated user could list/download/overwrite/
--        delete every other lawyer's case files via the Storage REST API.
--
--   C-2  public.cases / case_sections / entities / services  AND  (H-1)
--        regulation_v2.* corpus tables: RLS disabled while anon/authenticated
--        held INSERT/UPDATE/DELETE/TRUNCATE. Reachable with the public anon
--        key → unauthenticated corpus destruction + RAG poisoning.
--
-- Design notes:
--   * The backend touches storage and the corpus ONLY via the service-role
--     client, which bypasses RLS and is unaffected by REVOKE on anon/
--     authenticated. So none of this changes backend behaviour.
--   * The frontend touches storage ONLY via TUS resumable upload (INSERT +
--     PATCH/UPDATE) with the user JWT. Migration 047 established that
--     `auth.uid()` returns NULL inside the TUS execution context while
--     `owner_id` is still populated and the role is `authenticated`. The new
--     SELECT/UPDATE/DELETE predicates therefore tolerate `auth.uid() IS NULL`
--     (only reachable through the TUS endpoint, which exposes upload state,
--     not arbitrary object bodies) but otherwise REQUIRE owner == caller.
--     - Normal-context attacker (valid JWT, auth.uid() populated) GET/DELETE
--       of another user's object: owner_id <> auth.uid() -> DENIED.
--     - Legit TUS path (auth.uid() NULL): allowed via the NULL branch.
--     - If a future Supabase release populates auth.uid() in TUS, the strict
--       branch matches and the NULL branch is simply never taken. Robust both
--       ways.
--   * Corpus tables hold PUBLIC legal reference data (published regulations,
--     court judgments, government entities/e-services) with no user columns,
--     so public SELECT is preserved (USING (true)); only writes are removed.

BEGIN;

-- =====================================================================
-- C-1 — storage.objects: bind documents-bucket read/update/delete to owner
-- =====================================================================
DROP POLICY IF EXISTS "documents_select_own" ON storage.objects;
DROP POLICY IF EXISTS "documents_update_own_prefix" ON storage.objects;
DROP POLICY IF EXISTS "documents_delete_own" ON storage.objects;

-- SELECT — only the owner may read their own objects.
-- (Normal downloads go through backend service-role signed URLs; this policy
-- exists for the TUS resume HEAD. NULL-auth branch is TUS-only.)
CREATE POLICY "documents_select_own"
ON storage.objects FOR SELECT TO authenticated
USING (
  bucket_id = 'documents'
  AND owner_id IS NOT NULL
  AND (auth.uid() IS NULL OR owner_id = (auth.uid())::text)
);

-- UPDATE — TUS PATCH continuation. Owner-bound, NULL-auth-tolerant for TUS.
CREATE POLICY "documents_update_own_prefix"
ON storage.objects FOR UPDATE TO authenticated
USING (
  bucket_id = 'documents'
  AND owner_id IS NOT NULL
  AND (auth.uid() IS NULL OR owner_id = (auth.uid())::text)
);

-- DELETE — owner-bound. Backend cancel still works via service-role.
CREATE POLICY "documents_delete_own"
ON storage.objects FOR DELETE TO authenticated
USING (
  bucket_id = 'documents'
  AND owner_id IS NOT NULL
  AND (auth.uid() IS NULL OR owner_id = (auth.uid())::text)
);

-- (INSERT policy "documents_insert_own_prefix" from 047 is already correctly
--  scoped by the folder/case-ownership join keyed on owner_id — left intact.)

-- =====================================================================
-- C-2 / H-1 — corpus tables: enable RLS, keep public read, revoke writes
-- =====================================================================

-- public.* legal corpus -------------------------------------------------
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.cases         FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.case_sections FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.entities      FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.services      FROM anon, authenticated;

ALTER TABLE public.cases         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_sections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.entities      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.services      ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "cases_public_read"         ON public.cases;
DROP POLICY IF EXISTS "case_sections_public_read" ON public.case_sections;
DROP POLICY IF EXISTS "entities_public_read"      ON public.entities;
DROP POLICY IF EXISTS "services_public_read"      ON public.services;

CREATE POLICY "cases_public_read"         ON public.cases         FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "case_sections_public_read" ON public.case_sections FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "entities_public_read"      ON public.entities      FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "services_public_read"      ON public.services      FOR SELECT TO anon, authenticated USING (true);

-- regulation_v2.* legal corpus -----------------------------------------
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON regulation_v2.regulations      FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON regulation_v2.chunks           FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON regulation_v2.chunk_titles     FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON regulation_v2.articles         FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON regulation_v2.cross_references FROM anon, authenticated;

ALTER TABLE regulation_v2.regulations      ENABLE ROW LEVEL SECURITY;
ALTER TABLE regulation_v2.chunks           ENABLE ROW LEVEL SECURITY;
ALTER TABLE regulation_v2.chunk_titles     ENABLE ROW LEVEL SECURITY;
ALTER TABLE regulation_v2.articles         ENABLE ROW LEVEL SECURITY;
ALTER TABLE regulation_v2.cross_references ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "regulations_public_read"      ON regulation_v2.regulations;
DROP POLICY IF EXISTS "chunks_public_read"           ON regulation_v2.chunks;
DROP POLICY IF EXISTS "chunk_titles_public_read"     ON regulation_v2.chunk_titles;
DROP POLICY IF EXISTS "articles_public_read"         ON regulation_v2.articles;
DROP POLICY IF EXISTS "cross_references_public_read" ON regulation_v2.cross_references;

CREATE POLICY "regulations_public_read"      ON regulation_v2.regulations      FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "chunks_public_read"           ON regulation_v2.chunks           FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "chunk_titles_public_read"     ON regulation_v2.chunk_titles     FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "articles_public_read"         ON regulation_v2.articles         FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "cross_references_public_read" ON regulation_v2.cross_references FOR SELECT TO anon, authenticated USING (true);

COMMIT;
