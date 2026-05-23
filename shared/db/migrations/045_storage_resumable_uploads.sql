-- 045_storage_resumable_uploads.sql
-- Storage hardening for the new direct-to-Supabase resumable upload flow
-- (see .claude/plans/upload_reliability.md).
--
-- Three things:
--   1. Pin file size + allowed MIME types on the `documents` bucket
--      (defense-in-depth — FastAPI's /init route validates too, but the
--      bucket policy stops a forged client from bypassing it).
--   2. Add a storage.objects INSERT policy so authenticated users can
--      upload directly via TUS to their own case/general prefixes.
--      Today there are zero policies on storage.objects, so RLS denies
--      everything except service_role — direct browser uploads need this.
--   3. Keep SELECT/UPDATE/DELETE service-role-only: downloads still flow
--      through the backend's signed-URL endpoint and deletes happen via
--      backend cleanup jobs (no policy = default deny under RLS).
--
-- Path layout (set in shared.storage.client.build_storage_path):
--   cases/{case_id}/{file_id}_{filename}
--   cases/{case_id}/convos/{conv_id}/{file_id}_{filename}
--   general/{user_id}/convos/{conv_id}/{file_id}_{filename}
--   general/{user_id}/{file_id}_{filename}
--
-- storage.foldername(name) returns the directory parts as a text array,
-- so element [1] is the top-level prefix and [2] is either the case_id
-- (under cases/) or the user_id (under general/).

BEGIN;

-- ============================================
-- 1. Bucket configuration
-- ============================================
UPDATE storage.buckets
   SET file_size_limit = 52428800,                                -- 50 MB
       allowed_mime_types = ARRAY['application/pdf','image/png','image/jpeg']
 WHERE id = 'documents';


-- ============================================
-- 2. Storage RLS — INSERT for own prefixes only
-- ============================================
-- Idempotent re-runnable drop:
DROP POLICY IF EXISTS "documents_insert_own_prefix" ON storage.objects;

CREATE POLICY "documents_insert_own_prefix"
ON storage.objects
FOR INSERT
TO authenticated
WITH CHECK (
  bucket_id = 'documents'
  AND (
    -- (a) case-scoped uploads: the cases/{case_id}/... prefix
    (
      (storage.foldername(name))[1] = 'cases'
      AND EXISTS (
        SELECT 1
          FROM public.lawyer_cases lc
          JOIN public.users u ON u.user_id = lc.lawyer_user_id
         WHERE lc.case_id::text = (storage.foldername(name))[2]
           AND u.auth_id = auth.uid()
           AND lc.deleted_at IS NULL
      )
    )
    OR
    -- (b) general (no case): general/{user_id}/...
    (
      (storage.foldername(name))[1] = 'general'
      AND EXISTS (
        SELECT 1
          FROM public.users u
         WHERE u.user_id::text = (storage.foldername(name))[2]
           AND u.auth_id = auth.uid()
      )
    )
  )
);


-- ============================================
-- 3. Storage RLS — UPDATE for resumable continuation
-- ============================================
-- TUS chunk PATCH after a browser restart re-checks RLS. Same prefix rules.
DROP POLICY IF EXISTS "documents_update_own_prefix" ON storage.objects;

CREATE POLICY "documents_update_own_prefix"
ON storage.objects
FOR UPDATE
TO authenticated
USING (
  bucket_id = 'documents'
  AND (
    (
      (storage.foldername(name))[1] = 'cases'
      AND EXISTS (
        SELECT 1
          FROM public.lawyer_cases lc
          JOIN public.users u ON u.user_id = lc.lawyer_user_id
         WHERE lc.case_id::text = (storage.foldername(name))[2]
           AND u.auth_id = auth.uid()
           AND lc.deleted_at IS NULL
      )
    )
    OR
    (
      (storage.foldername(name))[1] = 'general'
      AND EXISTS (
        SELECT 1
          FROM public.users u
         WHERE u.user_id::text = (storage.foldername(name))[2]
           AND u.auth_id = auth.uid()
      )
    )
  )
);

-- SELECT / DELETE intentionally NOT granted to `authenticated`:
--   * downloads go through backend signed-URL endpoint (service_role)
--   * deletes happen via document_service / attachment_cleanup
--     (service_role). Default deny under RLS keeps it that way.

COMMIT;
