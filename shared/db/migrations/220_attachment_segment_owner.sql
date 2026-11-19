-- ════════════════════════════════════════════════════════════════════════════
-- 220 — who owns each attachment section (segmenter seg-v5.2)
-- ════════════════════════════════════════════════════════════════════════════
--
-- Depends on: 215 (attachment_segments).
-- Idempotent: ADD COLUMN IF NOT EXISTS; no data change; RLS unchanged (the
-- table's policies are row-level, new columns inherit them).
--
-- WHY ──────────────────────────────────────────────────────────────────────────
-- OWNER DECISION 2026-11-18: every section says whose words, findings or
-- decision it carries — المدعي، المدعى عليه الثاني، المحكمة، الخبير، الشاهد… —
-- so deep search and the writer read 〔what — owner〕 instead of guessing who
-- said a passage. Plan: .claude/plans/attachment_segmentation.md §13.
--
-- THE CHANGE ───────────────────────────────────────────────────────────────────
--   owner_class — fixed key (agents/memory/attachment_segmenter/models.py
--                 OWNER_CLASSES; kept in code, no CHECK, so a new key needs no
--                 migration)
--   owner       — the document's own role words («المدعى عليه الثاني»)
--   brought_by  — the party that filed or relies on the section, when it is not
--                 the owner («المدعي» for the plaintiff's witness)
-- All NULL on maps made before seg-v5.2.
--
-- DEPLOY ORDER: apply BEFORE the seg-v5.2 code — the runner inserts these
-- columns, so code first = every new map fails to store (failed_store).

BEGIN;

ALTER TABLE public.attachment_segments
  ADD COLUMN IF NOT EXISTS owner_class text,
  ADD COLUMN IF NOT EXISTS owner text,
  ADD COLUMN IF NOT EXISTS brought_by text;

COMMENT ON COLUMN public.attachment_segments.owner_class IS
  'seg-v5.2+: who the section belongs to — a key of OWNER_CLASSES (models.py); NULL on older maps';
COMMENT ON COLUMN public.attachment_segments.owner IS
  'seg-v5.2+: the owner in the document''s own role words, with its number';
COMMENT ON COLUMN public.attachment_segments.brought_by IS
  'seg-v5.2+: the party that filed or relies on the section, when not the owner';

DO $$
BEGIN
  IF (SELECT count(*) FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'attachment_segments'
        AND column_name IN ('owner_class', 'owner', 'brought_by')) <> 3 THEN
    RAISE EXCEPTION '220 self-check: owner columns missing';
  END IF;
  RAISE NOTICE '220 self-check OK: attachment_segments has owner_class, owner, brought_by';
END $$;

COMMIT;
