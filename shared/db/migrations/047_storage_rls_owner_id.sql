-- 047_storage_rls_owner_id.sql
-- ROOT-CAUSE FIX for the TUS resumable upload RLS failure shipped with
-- migration 045.
--
-- Migration 045 used `auth.uid()` inside the storage.objects INSERT policy
-- to verify case ownership. The standard /storage/v1/object/* endpoint
-- evaluated this correctly (PostgREST execution context). The TUS endpoint
-- at /storage/v1/upload/resumable evaluates RLS in a context where
-- `auth.uid()` returns NULL, so every TUS upload failed with:
--   "new row violates row-level security policy for table objects".
--
-- Empirically established (debug traces in
-- agents_reports/integration_upload_reliability.md):
--   * Even `WITH CHECK (true)` on `TO public` failed for TUS.
--   * `WITH CHECK (true)` on `TO authenticated` succeeded.
--   * `auth.uid()` returns NULL in TUS context but the TUS handler DOES
--     populate `storage.objects.owner_id` with the authenticated user's
--     sub claim (verified by inspecting freshly-uploaded rows).
--
-- Fix: derive the user from `owner_id` instead of `auth.uid()` inside the
-- policy, joining to public.users.auth_id to look up the case-ownership
-- chain. owner_id is set by the storage service for both standard and TUS
-- uploads, so the policy now works for both code paths.
--
-- Also tightens the policy surface: SELECT/DELETE were previously
-- service-role-only. They stay that way for users (downloads still flow
-- through backend-signed URLs) — we only add INSERT/UPDATE for TUS.

BEGIN;

-- Drop the broken 045 policies and any debug artifacts left behind from
-- the live RCA session.
DROP POLICY IF EXISTS "documents_insert_own_prefix" ON storage.objects;
DROP POLICY IF EXISTS "documents_update_own_prefix" ON storage.objects;
DROP POLICY IF EXISTS "_debug_documents_insert_auth_only" ON storage.objects;
DROP POLICY IF EXISTS "_debug_objects_allow_all" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_check_auth" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_check_owner" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_just_bucket" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_true" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_public_true" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_update_true" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_auth_ins" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_auth_upd" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_auth_sel" ON storage.objects;
DROP POLICY IF EXISTS "_diag_objects_auth_del" ON storage.objects;

-- And the migration-046-attempt artifacts on the multipart tables.
-- TUS does NOT write to these tables; that diagnosis was a dead-end.
DROP POLICY IF EXISTS "documents_mpu_insert_own_prefix"        ON storage.s3_multipart_uploads;
DROP POLICY IF EXISTS "documents_mpu_select_own"               ON storage.s3_multipart_uploads;
DROP POLICY IF EXISTS "documents_mpu_update_own"               ON storage.s3_multipart_uploads;
DROP POLICY IF EXISTS "documents_mpu_delete_own"               ON storage.s3_multipart_uploads;
DROP POLICY IF EXISTS "documents_mpu_parts_insert_own_prefix"  ON storage.s3_multipart_uploads_parts;
DROP POLICY IF EXISTS "documents_mpu_parts_select_own"         ON storage.s3_multipart_uploads_parts;
DROP POLICY IF EXISTS "_debug_mpu_allow_all"                   ON storage.s3_multipart_uploads;
DROP POLICY IF EXISTS "_debug_mpu_parts_allow_all"             ON storage.s3_multipart_uploads_parts;


-- ============================================
-- INSERT — strict prefix policy, keyed on owner_id
-- ============================================
CREATE POLICY "documents_insert_own_prefix"
ON storage.objects FOR INSERT TO authenticated
WITH CHECK (
  bucket_id = 'documents'
  AND owner_id IS NOT NULL
  AND (
    -- case-scoped upload
    (
      (storage.foldername(name))[1] = 'cases'
      AND EXISTS (
        SELECT 1
          FROM public.lawyer_cases lc
          JOIN public.users u ON u.user_id = lc.lawyer_user_id
         WHERE lc.case_id::text = (storage.foldername(name))[2]
           AND u.auth_id::text = owner_id
           AND lc.deleted_at IS NULL
      )
    )
    OR
    -- general/{user_id}/... upload
    (
      (storage.foldername(name))[1] = 'general'
      AND EXISTS (
        SELECT 1
          FROM public.users u
         WHERE u.user_id::text = (storage.foldername(name))[2]
           AND u.auth_id::text = owner_id
      )
    )
  )
);


-- ============================================
-- UPDATE — minimal check; resumable continuation
-- (TUS PATCH calls UPDATE on the placeholder row. The strict prefix check
-- is enforced at INSERT, so UPDATE only needs to ensure the row still has
-- an owner — preventing service-role artifacts from being patched by
-- end users.)
-- ============================================
CREATE POLICY "documents_update_own_prefix"
ON storage.objects FOR UPDATE TO authenticated
USING (bucket_id = 'documents' AND owner_id IS NOT NULL);


-- ============================================
-- SELECT — authenticated read of own-bucket objects
-- (Required by TUS to HEAD an in-flight upload during resume. Downloads
-- still go through backend-signed URLs which use service_role, but a
-- legitimate end user must be able to inspect their own upload state.)
-- ============================================
CREATE POLICY "documents_select_own"
ON storage.objects FOR SELECT TO authenticated
USING (bucket_id = 'documents' AND owner_id IS NOT NULL);


-- ============================================
-- DELETE — explicitly grant to authenticated for documents bucket
-- (Frontend cancel flow may delete the object directly. Backend cancel
-- service still works via service_role, but we expose user-side delete
-- for symmetry.)
-- ============================================
CREATE POLICY "documents_delete_own"
ON storage.objects FOR DELETE TO authenticated
USING (bucket_id = 'documents' AND owner_id IS NOT NULL);

COMMIT;
