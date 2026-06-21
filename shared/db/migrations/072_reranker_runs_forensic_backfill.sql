-- Migration 072: reranker_runs forensic backfill (OPTIONAL / historical).
--
-- No DDL — kept_results / dropped_results are already JSONB. This only rewrites
-- the EXISTING rows so their kept_results entries match the new forensic shape
-- the code now emits:
--
--   before:  {"ref_id": "reg:<uuid>",  "title": "", ...}
--   after:   {"source_table": "chunks", "ref_id": "<uuid>", "title": "<chunk title>", ...}
--
-- Resolvable domains only:
--   reg_search  -> source_table=chunks; ref_id already the chunks_v2.id UUID;
--                  title backfilled from chunks_v2.title.
--   case_search -> source_table=cases;  ref_id resolved case_ref -> cases.id UUID;
--                  title left as-is (case rows already carry a title).
--
-- compliance_search is SKIPPED on purpose: its historical ref_id is
-- sha1(service_ref) (irreversible) so neither the real services.id nor the
-- title can be recovered from stored data. Those rows are corrected naturally
-- as new turns are written by the updated pipeline.
--
-- dropped_results is NOT touched: 0 historical rows have any (the column was
-- always written as []).
--
-- Idempotent: each UPDATE is guarded by a LIKE on the old prefixed shape, so
-- re-running is a no-op once a row has been migrated. COALESCE(..., <original>)
-- preserves the row if the rebuild ever yields NULL (e.g. empty array).

-- NOTE: every rebuild uses `WITH ORDINALITY` + `ORDER BY ord` so the LEFT JOIN
-- can't scramble the (relevance/RRF-ranked) array order.

-- -- reg_search: strip "reg:" prefix, add source_table, backfill chunk title --
UPDATE reranker_runs r
SET kept_results = COALESCE((
    SELECT jsonb_agg(
        elem || jsonb_build_object(
            'source_table', 'chunks',
            'ref_id', regexp_replace(elem->>'ref_id', '^reg:', ''),
            'title', COALESCE(c.title, NULLIF(elem->>'title', ''), '')
        )
        ORDER BY ord
    )
    FROM jsonb_array_elements(r.kept_results) WITH ORDINALITY AS t(elem, ord)
    LEFT JOIN chunks_v2 c
        ON c.id::text = regexp_replace(elem->>'ref_id', '^reg:', '')
), r.kept_results)
WHERE r.agent_family = 'reg_search'
  AND r.kept_results::text LIKE '%"reg:%';

-- -- case_search: strip "case:" prefix, resolve case_ref -> cases.id UUID ------
UPDATE reranker_runs r
SET kept_results = COALESCE((
    SELECT jsonb_agg(
        elem || jsonb_build_object(
            'source_table', 'cases',
            'ref_id', COALESCE(
                c.id::text,
                regexp_replace(elem->>'ref_id', '^case:', '')
            ),
            'title', COALESCE(NULLIF(elem->>'title', ''), '')
        )
        ORDER BY ord
    )
    FROM jsonb_array_elements(r.kept_results) WITH ORDINALITY AS t(elem, ord)
    LEFT JOIN cases c
        ON c.case_ref = regexp_replace(elem->>'ref_id', '^case:', '')
), r.kept_results)
WHERE r.agent_family = 'case_search'
  AND r.kept_results::text LIKE '%"case:%';

-- -- compliance_search: tag source_table only (ref_id/title unrecoverable) ----
-- Adds source_table='services' so the row is self-describing; the hashed
-- ref_id and empty title are left untouched. (No join → order already stable,
-- but keep ORDINALITY for symmetry/safety.)
UPDATE reranker_runs r
SET kept_results = COALESCE((
    SELECT jsonb_agg(
        elem || jsonb_build_object('source_table', 'services')
        ORDER BY ord
    )
    FROM jsonb_array_elements(r.kept_results) WITH ORDINALITY AS t(elem, ord)
), r.kept_results)
WHERE r.agent_family = 'compliance_search'
  AND jsonb_array_length(r.kept_results) > 0
  AND NOT (r.kept_results->0 ? 'source_table');
