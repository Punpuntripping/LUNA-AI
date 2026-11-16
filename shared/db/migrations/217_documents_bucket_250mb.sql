-- 217_documents_bucket_250mb.sql
-- Chat attachments up to 250 MB (plan .claude/plans/ocr_page_budget.md §10).
--
-- The `documents` bucket has capped every object at 50 MB since 045. Chat
-- attachments may now be up to 250 MB: OCR no longer sends Mistral the whole
-- file — it cuts out only the pages the user's page budget covers
-- (agents/memory/ocr_extractor/big_file.py), each cut ≤ 20 MB, so Mistral's own
-- 50 MB / 1,000-page limits no longer bound what a user can attach.
--
-- The bucket is shared with case documents, which stay at 50 MB: that cap is
-- enforced at /init (upload_session_service.MAX_FILE_SIZE), the attachment cap
-- there is ATTACHMENT_MAX_FILE_SIZE. This bucket limit is the defense-in-depth
-- ceiling for both.
--
-- ⚠ The project-wide "Upload file size limit" (Supabase dashboard → Project
--   Settings → Storage) must be ≥ 250 MB too — a bucket limit above it has no
--   effect. It is not settable from SQL.
--
-- Idempotent.

UPDATE storage.buckets
   SET file_size_limit = 262144000                                -- 250 MB
 WHERE id = 'documents';
