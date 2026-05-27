-- Migration 049: workspace_item_references — relational refs per agent_search WI.
--
-- Replaces the JSONB blob ``workspace_items.metadata.references`` with a
-- normalised table whose columns are pure per-WI state. Display payload
-- (titles, URLs, snippets, source_view, cross_refs) is reconstructed on read
-- by joining to the existing source tables (chunks_v2 / cases / services and
-- friends). See ``backend/app/services/references_service.py``.
--
-- Schema rationale:
--   * ``wi_id``       FK to workspace_items(item_id) — the WI that cites the ref.
--   * ``item_id``     source row PK — chunks_v2.id | cases.case_ref | services.service_ref.
--                     This is what the read service JOINs on. Stored as TEXT
--                     because cases / services use string keys, not uuids.
--   * ``domain``      routing column for the read service; also denormalises
--                     the ref_id prefix so we never have to parse strings at
--                     read time.
--   * ``n``           1-based citation number anchoring the [n] tokens in
--                     ``workspace_items.content_md``. ``UNIQUE(wi_id, n)``
--                     enforces contiguity at write time.
--   * ``relevance``   high/medium — per-retrieval reranker tag. Not derivable
--                     after the fact (re-running retrieval would tag it
--                     differently).
--   * ``used``        whether the synthesis body cited [n] at least once.
--                     Derivable from a regex over content_md, but stored so
--                     "WHERE used=true" is an index lookup.
--   * ``sub_queries`` which sub-query indices produced this ref in this run.
--                     Not recoverable for old rows (postvalidator never
--                     persisted the mapping). Default '{}'.
--
-- Dependencies:
--   * 001_extensions.sql      (pgcrypto for sha1 hash during compliance backfill)
--   * 026_workspace_items.sql (workspace_items table + RLS)
--
-- This migration is idempotent and runs the JSONB → row backfill in the
-- same transaction that drops ``metadata.references``.

-- ---------------------------------------------------------------------------
-- 1. Table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.workspace_item_references (
    ref_pk       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wi_id        UUID NOT NULL REFERENCES public.workspace_items(item_id) ON DELETE CASCADE,
    item_id      TEXT NOT NULL,
    domain       TEXT NOT NULL CHECK (domain IN ('regulations', 'compliance', 'cases')),
    n            INTEGER NOT NULL CHECK (n > 0),
    relevance    TEXT NOT NULL CHECK (relevance IN ('high', 'medium')),
    used         BOOLEAN NOT NULL DEFAULT FALSE,
    sub_queries  INTEGER[] NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (wi_id, n)
);

COMMENT ON TABLE public.workspace_item_references IS
    'Relational per-WI ref state. Replaces workspace_items.metadata.references '
    '(migration 049). Display payload (title, urls, snippet, source_view, '
    'cross_refs) is JOIN-reconstructed at read time from chunks_v2 / cases / '
    'services via the references_service. n is local to wi_id and anchors '
    'the [n] tokens in workspace_items.content_md.';

COMMENT ON COLUMN public.workspace_item_references.wi_id IS
    'Workspace item that cites this ref. ON DELETE CASCADE — refs die with the WI.';
COMMENT ON COLUMN public.workspace_item_references.item_id IS
    'Source row PK. domain=regulations: chunks_v2.id. domain=cases: cases.case_ref. '
    'domain=compliance: services.service_ref.';
COMMENT ON COLUMN public.workspace_item_references.domain IS
    'Routing column for the read-side service (regulations | compliance | cases).';
COMMENT ON COLUMN public.workspace_item_references.n IS
    '1-based citation number, local to wi_id. Anchors the inline [n] tokens '
    'in workspace_items.content_md.';
COMMENT ON COLUMN public.workspace_item_references.used IS
    'TRUE if the synthesis body cited [n] at least once. Maintained at write '
    'time by the publisher from extract_cited_numbers(content_md).';
COMMENT ON COLUMN public.workspace_item_references.sub_queries IS
    'Sub-query indices (0-based) that surfaced this ref in this run. Empty '
    'for rows backfilled from the legacy JSONB — that mapping was never '
    'persisted before migration 049.';

-- ---------------------------------------------------------------------------
-- 2. Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_workspace_item_references_wi_used
    ON public.workspace_item_references (wi_id, used);

CREATE INDEX IF NOT EXISTS idx_workspace_item_references_item_id
    ON public.workspace_item_references (item_id);

CREATE INDEX IF NOT EXISTS idx_workspace_item_references_domain_item
    ON public.workspace_item_references (domain, item_id);

-- ---------------------------------------------------------------------------
-- 3. RLS — inherits ownership via workspace_items.user_id.
-- ---------------------------------------------------------------------------
ALTER TABLE public.workspace_item_references ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    CREATE POLICY workspace_item_references_select ON public.workspace_item_references
        FOR SELECT
        USING (EXISTS (
            SELECT 1 FROM public.workspace_items wi
            WHERE wi.item_id = workspace_item_references.wi_id
              AND wi.user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY workspace_item_references_insert ON public.workspace_item_references
        FOR INSERT
        WITH CHECK (EXISTS (
            SELECT 1 FROM public.workspace_items wi
            WHERE wi.item_id = workspace_item_references.wi_id
              AND wi.user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY workspace_item_references_update ON public.workspace_item_references
        FOR UPDATE
        USING (EXISTS (
            SELECT 1 FROM public.workspace_items wi
            WHERE wi.item_id = workspace_item_references.wi_id
              AND wi.user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE POLICY workspace_item_references_delete ON public.workspace_item_references
        FOR DELETE
        USING (EXISTS (
            SELECT 1 FROM public.workspace_items wi
            WHERE wi.item_id = workspace_item_references.wi_id
              AND wi.user_id = (SELECT u.user_id FROM public.users u WHERE u.auth_id = (SELECT auth.uid()))
        ));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- 4. Backfill — expand existing workspace_items.metadata.references JSONB.
--
-- Strategy:
--   * For each agent_search WI whose metadata carries a non-empty
--     ``references`` array, expand one row per array element.
--   * ``item_id`` is derived from ``ref.ref_id``:
--       regulations -> strip "reg:" prefix
--       cases       -> strip "case:" prefix
--       compliance  -> JOIN services to find service_ref whose sha1(service_ref)[:16]
--                      matches the "compliance:<hash>" suffix. NULL if not
--                      resolvable; the row is skipped (compliance refs that
--                      cannot be re-located are lost — flagged in the
--                      migration notes for old WIs).
--   * ``used`` is computed by regex-matching ``[n]`` and ``[n,m]`` tokens in
--     ``content_md`` against this ref's ``n``. Mirrors the runtime regex
--     ``_CITATION_RE`` in postvalidator.py.
--   * ``sub_queries`` is set to '{}' — the mapping was never persisted on
--     old rows.
-- ---------------------------------------------------------------------------
WITH expanded AS (
    SELECT
        wi.item_id                                  AS wi_id,
        wi.content_md                               AS content_md,
        ref->>'ref_id'                              AS ref_id,
        ref->>'domain'                              AS domain_raw,
        ref->>'relevance'                           AS relevance_raw,
        (ref->>'n')::INTEGER                        AS n
    FROM public.workspace_items wi
    CROSS JOIN LATERAL jsonb_array_elements(wi.metadata->'references') AS ref
    WHERE wi.kind = 'agent_search'
      AND wi.deleted_at IS NULL
      AND wi.metadata ? 'references'
      AND jsonb_typeof(wi.metadata->'references') = 'array'
      AND ref ? 'n'
      AND ref ? 'domain'
      AND ref ? 'ref_id'
),
resolved AS (
    SELECT
        e.wi_id,
        e.n,
        e.content_md,
        -- Normalise domain (legacy rows may omit it; default to regulations).
        COALESCE(NULLIF(e.domain_raw, ''), 'regulations') AS domain,
        -- Normalise relevance (legacy rows may have empty; default to medium).
        CASE
            WHEN e.relevance_raw IN ('high', 'medium') THEN e.relevance_raw
            ELSE 'medium'
        END AS relevance,
        -- Derive source-table key from ref_id by stripping the domain prefix.
        -- Compliance is the exception: the prefix is a hash, not the
        -- service_ref — recover the service_ref via the digest join below.
        CASE
            WHEN e.domain_raw = 'regulations' AND e.ref_id LIKE 'reg:%'
                THEN substring(e.ref_id FROM 5)
            WHEN e.domain_raw = 'cases' AND e.ref_id LIKE 'case:%'
                THEN substring(e.ref_id FROM 6)
            WHEN e.domain_raw = 'compliance' AND e.ref_id LIKE 'compliance:%'
                THEN (
                    SELECT s.service_ref
                    FROM public.services s
                    WHERE 'compliance:' || substring(
                              encode(digest(s.service_ref, 'sha1'), 'hex')
                              FROM 1 FOR 16
                          ) = e.ref_id
                    LIMIT 1
                )
            ELSE NULL
        END AS item_id
    FROM expanded e
)
INSERT INTO public.workspace_item_references (
    wi_id, item_id, domain, n, relevance, used, sub_queries
)
SELECT
    r.wi_id,
    r.item_id,
    r.domain,
    r.n,
    r.relevance,
    -- ``used`` = does any [n] / [n,m,...] group in content_md contain this n?
    -- Regex mirrors agents/deep_search_v4/aggregator/postvalidator.py
    -- (``_CITATION_RE``). The Arabic comma U+060C is also a valid separator.
    EXISTS (
        SELECT 1
        FROM regexp_matches(
            COALESCE(r.content_md, ''),
            '\[(\d+(?:\s*[,،]\s*\d+)*)\]',
            'g'
        ) AS m
        WHERE r.n = ANY (
            SELECT NULLIF(btrim(part), '')::INTEGER
            FROM unnest(string_to_array(m[1], ',')) AS part
            WHERE btrim(part) ~ '^\d+$'
        )
    ) AS used,
    '{}'::INTEGER[] AS sub_queries
FROM resolved r
WHERE r.item_id IS NOT NULL
ON CONFLICT (wi_id, n) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 5. Strip ``references`` key from workspace_items.metadata.
--
-- Once backfilled, the JSONB key is the source of bugs (dual-write traps,
-- stale reads). Drop it from every row. Other metadata keys (subtype,
-- confidence, detail_level, ura_log_id, prompt_key, model_used, ref_count,
-- cited_count) are preserved.
-- ---------------------------------------------------------------------------
UPDATE public.workspace_items
SET metadata = metadata - 'references'
WHERE kind = 'agent_search'
  AND metadata ? 'references';
