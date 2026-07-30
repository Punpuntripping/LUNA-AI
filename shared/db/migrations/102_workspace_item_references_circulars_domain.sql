-- Migration 102: allow ``domain='circulars'`` on workspace_item_references.
--
-- Bug: migration 049 pinned the domain CHECK to the three domains that existed
-- then — ('regulations', 'compliance', 'cases'). The reg_search unified-topics
-- work (search_topics RPC, 2026-07-17) added ministerial circulars as a fourth
-- retrieval domain: ``ura.reg_adapter`` mints ``circular:<uuid>`` refs,
-- ``aggregator.preprocessor`` projects them as ``domain="circulars"``, and both
-- ``references_service`` (read) and ``ReferencePanel`` (frontend) already handle
-- them end to end. Only the CHECK was never widened.
--
-- Failure mode this fixes: ``persist_item_references`` writes all refs of a WI
-- in ONE batch insert, so a single circular ref raised 23514 and Postgres
-- rejected the WHOLE batch. The publisher swallows that exception by design
-- (a refs hiccup must not fail the user-visible publish), so the workspace item
-- was created with metadata.ref_count=9 but ZERO reference rows — the artifact
-- rendered with no المراجع section at all.
--
-- Observed blast radius before this fix (agent_search WIs, ref_count>0 yet no
-- rows): 4 items, all of them URAs containing at least one ``circular`` result
-- — f22554d9 (2026-07-25), f920081f + f59ea977 (2026-07-19), 8f9b6166
-- (2026-07-18). Those panels are not backfilled here: the per-ref ``n`` /
-- ``used`` state lived only in the aggregator output, which is not persisted.
--
-- Dependencies:
--   * 049_workspace_item_references.sql (table + original CHECK)
--   * 050_workspace_item_references_uuid_and_ref_id.sql (two-key design)
--
-- Idempotent: drop-then-add by constraint name.

ALTER TABLE public.workspace_item_references
    DROP CONSTRAINT IF EXISTS workspace_item_references_domain_check;

ALTER TABLE public.workspace_item_references
    ADD CONSTRAINT workspace_item_references_domain_check
    CHECK (domain IN ('regulations', 'compliance', 'cases', 'circulars'));

COMMENT ON COLUMN public.workspace_item_references.domain IS
    'Retrieval domain of the cited source: regulations (chunks_v2) | compliance '
    '(services) | cases (cases) | circulars (circulars). Routes the read-path '
    'shell builder in backend/app/services/references_service.py.';
