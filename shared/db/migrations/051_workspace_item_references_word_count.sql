-- Migration 051: per-ref content word count + denormalised total on the WI.
--
-- Adds two columns:
--
--   workspace_item_references.content_word_count INTEGER NOT NULL DEFAULT 0
--     Whitespace-split word count of the aggregator-view content for the
--     ref's source row (chunks_v2.content + .context for regulations,
--     cases.content for cases, services.service_context for compliance).
--     Populated at publish time by ``persist_item_references`` from the
--     URA aggregator-view content. Backfilled here by joining to the
--     source tables.
--
--   workspace_items.used_ref_words_total INTEGER NOT NULL DEFAULT 0
--     SUM of content_word_count over the WI's ``used=true`` refs. The
--     denormalised number the writer agent reads when budgeting context
--     against the grounding surface this WI actually consumed.
--     Maintained by an AFTER INSERT/UPDATE/DELETE trigger on
--     ``workspace_item_references`` — exactly the same pattern migration
--     048 uses for ``workspace_items.word_count`` vs. content_md.
--
-- Dependencies:
--   * 048_workspace_items_word_count.sql  (compute_word_count helper)
--   * 049_workspace_item_references.sql   (table)
--   * 050_workspace_item_references_uuid_and_ref_id.sql (item_id is UUID)
--
-- Backfill ordering:
--   1. Add columns (both default 0 so the table stays consistent during
--      the migration).
--   2. Populate content_word_count from the source tables.
--   3. Populate used_ref_words_total from the just-backfilled per-ref
--      column.
--   4. ONLY THEN create the trigger — so the backfill doesn't fire it
--      792× during step 2.
--
-- This migration is idempotent.

-- ---------------------------------------------------------------------------
-- 1. Columns
-- ---------------------------------------------------------------------------
ALTER TABLE public.workspace_item_references
    ADD COLUMN IF NOT EXISTS content_word_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE public.workspace_items
    ADD COLUMN IF NOT EXISTS used_ref_words_total INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.workspace_item_references.content_word_count IS
    'Whitespace word count of the source row''s aggregator-view content '
    '(chunks_v2.content+context | cases.content | services.service_context). '
    'Computed at publish time. Used in conjunction with the AFTER trigger '
    'that maintains workspace_items.used_ref_words_total.';

COMMENT ON COLUMN public.workspace_items.used_ref_words_total IS
    'Sum of content_word_count over the WI''s used=true refs. The '
    'grounding-surface budget the writer agent reads when planning '
    'context. Auto-maintained by the recompute_used_ref_words_total '
    'trigger on workspace_item_references — never set by hand.';

-- ---------------------------------------------------------------------------
-- 2. Backfill content_word_count by joining to the source tables.
--
-- Regulations: chunks_v2.content + ' ' + chunks_v2.context — same shape
--              the aggregator-view content uses (cross_refs are aux and
--              skipped here to keep the backfill pure-SQL).
-- Cases:       cases.content.
-- Compliance:  services.service_context.
--
-- Rows whose item_id failed to resolve (NULL) stay at the column default 0.
-- ---------------------------------------------------------------------------
UPDATE public.workspace_item_references r
SET content_word_count = public.compute_word_count(
    coalesce(c.content, '') ||
    CASE WHEN coalesce(c.context, '') <> '' THEN ' ' || c.context ELSE '' END
)
FROM public.chunks_v2 c
WHERE r.domain = 'regulations'
  AND r.item_id IS NOT NULL
  AND c.id = r.item_id;

UPDATE public.workspace_item_references r
SET content_word_count = public.compute_word_count(coalesce(c.content, ''))
FROM public.cases c
WHERE r.domain = 'cases'
  AND r.item_id IS NOT NULL
  AND c.id = r.item_id;

UPDATE public.workspace_item_references r
SET content_word_count = public.compute_word_count(coalesce(s.service_context, ''))
FROM public.services s
WHERE r.domain = 'compliance'
  AND r.item_id IS NOT NULL
  AND s.id = r.item_id;

-- ---------------------------------------------------------------------------
-- 3. Backfill used_ref_words_total on workspace_items.
-- ---------------------------------------------------------------------------
UPDATE public.workspace_items wi
SET used_ref_words_total = COALESCE(t.total, 0)
FROM (
    SELECT r.wi_id, SUM(r.content_word_count)::INTEGER AS total
    FROM public.workspace_item_references r
    WHERE r.used = TRUE
    GROUP BY r.wi_id
) t
WHERE wi.item_id = t.wi_id;

-- ---------------------------------------------------------------------------
-- 4. Trigger function — keeps used_ref_words_total in sync.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.recompute_used_ref_words_total()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    affected_wi UUID;
BEGIN
    -- Pick the affected wi(s) by trigger op. UPDATE may need to recompute
    -- both NEW.wi_id and OLD.wi_id if a row migrated WIs (never in
    -- practice — refs are append-only — but the code stays correct).
    IF TG_OP = 'DELETE' THEN
        affected_wi := OLD.wi_id;
    ELSE
        affected_wi := NEW.wi_id;
    END IF;

    UPDATE public.workspace_items
    SET used_ref_words_total = COALESCE((
        SELECT SUM(r.content_word_count)::INTEGER
        FROM public.workspace_item_references r
        WHERE r.wi_id = affected_wi AND r.used = TRUE
    ), 0)
    WHERE item_id = affected_wi;

    IF TG_OP = 'UPDATE' AND OLD.wi_id IS DISTINCT FROM NEW.wi_id THEN
        UPDATE public.workspace_items
        SET used_ref_words_total = COALESCE((
            SELECT SUM(r.content_word_count)::INTEGER
            FROM public.workspace_item_references r
            WHERE r.wi_id = OLD.wi_id AND r.used = TRUE
        ), 0)
        WHERE item_id = OLD.wi_id;
    END IF;

    RETURN NULL;  -- AFTER trigger; return value is ignored.
END;
$$;

COMMENT ON FUNCTION public.recompute_used_ref_words_total() IS
    'AFTER INSERT/UPDATE/DELETE trigger on workspace_item_references. '
    'Recomputes workspace_items.used_ref_words_total for the affected '
    'wi_id(s) from the current ref-table state. Idempotent and side-effect '
    'free — recomputing twice yields the same value.';

-- ---------------------------------------------------------------------------
-- 5. Trigger binding — fires AFTER the row change so the SUM reads the
--    post-update state.
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS recompute_used_ref_words_total
    ON public.workspace_item_references;

CREATE TRIGGER recompute_used_ref_words_total
    AFTER INSERT OR UPDATE OR DELETE ON public.workspace_item_references
    FOR EACH ROW
    EXECUTE FUNCTION public.recompute_used_ref_words_total();

COMMENT ON TRIGGER recompute_used_ref_words_total
    ON public.workspace_item_references IS
    'Maintains workspace_items.used_ref_words_total whenever a per-WI '
    'ref row is inserted, updated, or deleted. Fires AFTER the row '
    'change so the SUM reads post-update state.';
