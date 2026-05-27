-- Migration 048: workspace_items.word_count column + auto-maintained trigger.
--
-- Adds a denormalized whitespace-word count of ``content_md`` to every
-- workspace_items row so the frontend can show "N words" badges and the
-- backend can budget context windows without recomputing on every read.
--
-- Design notes:
--   * Pure-DB maintenance — a BEFORE INSERT/UPDATE-OF-content_md trigger
--     keeps the count in sync. No backend writers need to set it.
--   * Whitespace-split is language-agnostic and works for Arabic, English,
--     and mixed RTL/LTR content. NULL / empty / all-whitespace → 0.
--   * NOT NULL DEFAULT 0 so consumers never have to coalesce.
--   * Backfill happens in the same migration; existing rows are not left NULL.
--
-- Dependencies:
--   * 026_workspace_items.sql  (creates workspace_items + content_md column)
--
-- This migration is idempotent.

-- ---------------------------------------------------------------------------
-- 1. Column
-- ---------------------------------------------------------------------------
ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS word_count INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.workspace_items.word_count IS
    'Whitespace-split word count of content_md. Auto-maintained by the '
    'set_workspace_item_word_count BEFORE INSERT/UPDATE trigger. '
    'Works for Arabic, English, and mixed content. 0 for empty/NULL bodies.';


-- ---------------------------------------------------------------------------
-- 2. Helper — pure function, reusable by backfill + trigger.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.compute_word_count(p_text TEXT)
RETURNS INTEGER
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN p_text IS NULL OR btrim(p_text) = '' THEN 0
        ELSE COALESCE(
            array_length(
                regexp_split_to_array(btrim(p_text), '\s+'),
                1
            ),
            0
        )
    END;
$$;

COMMENT ON FUNCTION public.compute_word_count(TEXT) IS
    'Whitespace-split word count. Returns 0 for NULL / empty / all-whitespace '
    'input. Language-agnostic — works for Arabic, English, mixed RTL/LTR.';


-- ---------------------------------------------------------------------------
-- 3. Trigger function — sets NEW.word_count from NEW.content_md.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.set_workspace_item_word_count()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.word_count := public.compute_word_count(NEW.content_md);
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.set_workspace_item_word_count() IS
    'BEFORE INSERT/UPDATE trigger function: keeps workspace_items.word_count '
    'in sync with content_md.';


-- ---------------------------------------------------------------------------
-- 4. Trigger binding — fires only when content_md is touched (or on INSERT).
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS set_workspace_item_word_count ON public.workspace_items;

CREATE TRIGGER set_workspace_item_word_count
    BEFORE INSERT OR UPDATE OF content_md ON public.workspace_items
    FOR EACH ROW
    EXECUTE FUNCTION public.set_workspace_item_word_count();

COMMENT ON TRIGGER set_workspace_item_word_count ON public.workspace_items IS
    'Recomputes word_count whenever content_md is inserted or updated. '
    'Does not fire on metadata-only updates (summary, locked_by_agent_until, etc.).';


-- ---------------------------------------------------------------------------
-- 5. Backfill — populate word_count for all existing rows.
-- ---------------------------------------------------------------------------
UPDATE public.workspace_items
SET word_count = public.compute_word_count(content_md)
WHERE word_count = 0
  AND content_md IS NOT NULL
  AND btrim(content_md) <> '';
