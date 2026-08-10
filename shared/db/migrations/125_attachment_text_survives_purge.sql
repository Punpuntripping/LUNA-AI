-- 125_attachment_text_survives_purge.sql
--
-- The attachment retention sweep must destroy the FILE, never the extracted text.
--
-- Dependencies:
--   - 026_workspace_items.sql  (creates the `workspace_content_shape` CHECK)
--
-- WHAT WENT WRONG
-- ---------------
-- `attachment_cleanup.cleanup_old_pdf_attachments` hard-deleted the whole
-- `workspace_items` row once a PDF attachment passed 24h. Because
-- `message_attachments.document_id` references `workspace_items(item_id)`
-- ON DELETE CASCADE, that one DELETE also erased the message→upload link. So a
-- single sweep destroyed, together:
--
--   * `content_md` — the OCR text written by `agents.memory.ocr_extractor`,
--     which is the EXACT text every agent reads (the PDF itself is never sent
--     to a model),
--   * the `WI-{seq}` alias the agents cite out loud,
--   * the history tag from `agents.utils.history.build_user_attachment_tag`,
--     which is how "حلل العقد المرفق" three turns later still resolves.
--
-- Measured on prod 2026-08-10, before this change: 46 successful OCR
-- extractions ever (93 pages), only 24 still readable — all 24 of them images,
-- because PNG/JPEG never matched the PDF-only filter. Every PDF extraction ever
-- made had been deleted, and the storage objects went with them, so those 22
-- are unrecoverable.
--
-- THE NEW SHAPE
-- -------------
-- The sweep now NULLs `storage_path` and stamps `metadata.original_purged_at`,
-- keeping the row and its text. `workspace_content_shape` forbade precisely
-- that shape: an attachment had to carry a storage_path or a document_id. This
-- relaxes it so a text-only attachment — an upload whose original has been
-- purged — is a legal row.
--
-- ORDERING: apply this BEFORE deploying the new attachment_cleanup.py. The
-- sweep updates the row before it deletes the object, so on the old constraint
-- the UPDATE fails, the delete is skipped, and the pass is a safe no-op — but
-- only in that order.
--
-- Strictly more permissive than what it replaces, so no existing row can
-- violate it. Idempotent; safe to re-run.

BEGIN;

ALTER TABLE public.workspace_items
    DROP CONSTRAINT IF EXISTS workspace_content_shape;

ALTER TABLE public.workspace_items
    ADD CONSTRAINT workspace_content_shape CHECK (
        (kind = 'attachment'
         AND (storage_path IS NOT NULL
              OR document_id IS NOT NULL
              OR content_md IS NOT NULL
              OR metadata ? 'original_purged_at'))
        OR
        (kind <> 'attachment' AND content_md IS NOT NULL)
    );

COMMENT ON COLUMN public.workspace_items.storage_path IS
    'Supabase Storage path for kind=attachment uploads. Mutually compatible with document_id (one or the other at upload time). Goes NULL once the retention sweep purges the original file — metadata.original_purged_at is then set, and content_md (the OCR text) survives as the item''s only content.';

COMMIT;
