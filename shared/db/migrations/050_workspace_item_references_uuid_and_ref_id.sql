-- Migration 050: workspace_item_references — item_id → UUID, add ref_id fallback.
--
-- 049 stored ``item_id`` as TEXT because two of the three source tables
-- (cases, services) use TEXT handles (case_ref, service_ref) at the URA
-- layer. But all three source PKs are UUID — chunks_v2.id, cases.id,
-- services.id. This migration:
--
--   1. Adds ``ref_id TEXT NOT NULL`` — always carries the URA-emitted
--      identifier (``reg:<uuid>`` / ``case:<case_ref>`` / ``compliance:<sha1[:16]>``).
--      This is the durable fallback when item_id can't be resolved (source
--      row deleted / re-keyed) AND the forensic-traceability key into
--      retrieval_artifacts.
--
--   2. Converts ``item_id`` from TEXT → UUID, populated by:
--        regulations -> cast the existing UUID-shaped text
--        cases       -> JOIN cases ON case_ref = item_id_text  → cases.id
--        compliance  -> JOIN services ON service_ref = item_id_text → services.id
--
--   3. Adds a CHECK constraint so every row has at least one resolvable
--      join key (item_id OR a non-empty ref_id).
--
--   4. Reindexes ``(domain, item_id)`` (now UUID-typed) and adds
--      ``ref_id`` index for the fallback lookup path.
--
-- Dependencies:
--   * 049_workspace_item_references.sql
--
-- This migration is idempotent (re-running it is a no-op once item_id is uuid).

-- ---------------------------------------------------------------------------
-- 1. Add ref_id column (nullable for backfill, NOT NULL afterwards).
-- ---------------------------------------------------------------------------
ALTER TABLE public.workspace_item_references
    ADD COLUMN IF NOT EXISTS ref_id TEXT;

-- Backfill ref_id from the current text item_id by re-prefixing.
-- Idempotent: only runs while ref_id is still NULL.
UPDATE public.workspace_item_references r
SET ref_id = CASE
    WHEN r.domain = 'regulations' THEN 'reg:' || r.item_id
    WHEN r.domain = 'cases'       THEN 'case:' || r.item_id
    WHEN r.domain = 'compliance'  THEN 'compliance:' || substring(
        encode(digest(r.item_id, 'sha1'), 'hex') FROM 1 FOR 16
    )
    ELSE NULL
END
WHERE r.ref_id IS NULL
  AND pg_typeof(r.item_id)::TEXT = 'text';

-- Now make it NOT NULL.
DO $$ BEGIN
    ALTER TABLE public.workspace_item_references
        ALTER COLUMN ref_id SET NOT NULL;
EXCEPTION WHEN others THEN
    -- Already NOT NULL or column missing — fine for a re-run.
    NULL;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Convert item_id from TEXT to UUID via lookups.
-- ---------------------------------------------------------------------------
-- Guarded: only runs while item_id is still text. The whole block is a
-- no-op on a second apply.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'workspace_item_references'
          AND column_name  = 'item_id'
          AND data_type    = 'text'
    ) THEN
        -- Stash the text column under a temporary name.
        EXECUTE 'ALTER TABLE public.workspace_item_references RENAME COLUMN item_id TO item_id_text';
        EXECUTE 'ALTER TABLE public.workspace_item_references ADD COLUMN item_id UUID';

        -- regulations: cast the uuid-shaped text directly.
        EXECUTE $sql$
            UPDATE public.workspace_item_references r
            SET item_id = r.item_id_text::uuid
            WHERE r.domain = 'regulations'
              AND r.item_id_text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        $sql$;

        -- cases: JOIN cases.case_ref → cases.id.
        EXECUTE $sql$
            UPDATE public.workspace_item_references r
            SET item_id = c.id
            FROM public.cases c
            WHERE r.domain = 'cases'
              AND r.item_id IS NULL
              AND c.case_ref = r.item_id_text
        $sql$;

        -- compliance: JOIN services.service_ref → services.id.
        EXECUTE $sql$
            UPDATE public.workspace_item_references r
            SET item_id = s.id
            FROM public.services s
            WHERE r.domain = 'compliance'
              AND r.item_id IS NULL
              AND s.service_ref = r.item_id_text
        $sql$;

        -- Drop the staging column.
        EXECUTE 'ALTER TABLE public.workspace_item_references DROP COLUMN item_id_text';
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. Constraint — at least one resolvable join key per row.
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    ALTER TABLE public.workspace_item_references
        ADD CONSTRAINT workspace_item_references_has_key
        CHECK (item_id IS NOT NULL OR (ref_id IS NOT NULL AND ref_id <> ''));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- 4. Indexes — recreate (domain, item_id) since type changed, add ref_id.
-- ---------------------------------------------------------------------------
DROP INDEX IF EXISTS public.idx_workspace_item_references_item_id;
DROP INDEX IF EXISTS public.idx_workspace_item_references_domain_item;

CREATE INDEX IF NOT EXISTS idx_workspace_item_references_item_id
    ON public.workspace_item_references (item_id)
    WHERE item_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workspace_item_references_domain_item
    ON public.workspace_item_references (domain, item_id)
    WHERE item_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workspace_item_references_ref_id
    ON public.workspace_item_references (ref_id);

-- ---------------------------------------------------------------------------
-- 5. Column comments — keep them honest about the new contract.
-- ---------------------------------------------------------------------------
COMMENT ON COLUMN public.workspace_item_references.item_id IS
    'Source row UUID PK (chunks_v2.id | cases.id | services.id). NULL when '
    'the source row could not be resolved at write time — readers must then '
    'fall back to parsing ref_id for the URA-level handle.';

COMMENT ON COLUMN public.workspace_item_references.ref_id IS
    'URA-emitted identifier — "reg:<uuid>" | "case:<case_ref>" | '
    '"compliance:<sha1[:16]>". Always populated. item_id is the preferred '
    'join key; ref_id is the durable fallback and the forensic-traceability '
    'key into retrieval_artifacts.';
