-- Migration 212: allow ``domain='principles'`` on workspace_item_references
-- (official judicial principles as a deep-search reference, plan
-- .claude/plans/official_principles_refs.md §3.6).
--
-- Deep search now cites an official judicial principle («مبدأ قضائي») from
-- ``public.official_principles``: ``ura.principles.principle_from_row`` mints
-- ``principle:<official_principles.id>``, ``aggregator.preprocessor`` projects it
-- as ``domain='principles'`` / ``source_type='official_principle'``, and
-- ``references_service`` (read + write), ``reference_resolver`` (free reveal)
-- and the panel all handle it end to end. Only this CHECK is left.
--
-- WHY THIS MUST LAND BEFORE THE CODE (the migration-102 lesson, §9 trap 1).
-- ``persist_item_references`` writes all refs of a WI in ONE batch insert, and
-- the publisher swallows that exception by design. A single out-of-CHECK row
-- (23514) rejects the WHOLE batch: that is how the ``circulars`` gap of
-- migration 102 shipped agent_search items with metadata.ref_count=9 and ZERO
-- reference rows — the artifact rendered with no المراجع section at all, and
-- nothing anywhere said why. The per-row retry added since then localises the
-- loss to the offending ref, but it is still a SILENT loss (an ERROR log, a
-- return value the publisher ignores): every principle the answer cites would
-- render as a dead inline [n] with no card. So: apply this, then deploy.
--
-- ``articles`` stays in the list and now has a SECOND producer: the Article 40
-- pin (``ura/pinned.py``) emits ``article:<articles_v2.id>`` under
-- ``domain='articles'`` — the exact shape simple_search already writes.
--
-- NOT VALID + VALIDATE: the ADD skips the table scan (only a brief lock on the
-- table), and the VALIDATE scans under SHARE UPDATE EXCLUSIVE, which does not
-- block concurrent inserts. ~20k rows live (2026-11-07), so either form is
-- quick; this one simply never holds the write lock for a scan.
--
-- Dependencies:
--   * 049_workspace_item_references.sql (table + original CHECK)
--   * 050_workspace_item_references_uuid_and_ref_id.sql (two-key design)
--   * 102_workspace_item_references_circulars_domain.sql (circulars)
--   * 136_simple_search_ref_domains.sql (articles, regulation_docs — the live
--     6-domain CHECK this widens; verified live 2026-11-07)
--
-- Idempotent: drop-then-add by constraint name (same shape as 102 / 136).

ALTER TABLE public.workspace_item_references
    DROP CONSTRAINT IF EXISTS workspace_item_references_domain_check;

ALTER TABLE public.workspace_item_references
    ADD CONSTRAINT workspace_item_references_domain_check
    CHECK (domain IN (
        'regulations',
        'compliance',
        'cases',
        'circulars',
        'articles',
        'regulation_docs',
        'principles'
    )) NOT VALID;

ALTER TABLE public.workspace_item_references
    VALIDATE CONSTRAINT workspace_item_references_domain_check;

COMMENT ON COLUMN public.workspace_item_references.domain IS
    'Retrieval domain of the cited source, and the routing key for the read-path '
    'shell builder in backend/app/services/references_service.py. Each domain '
    'pins ONE backing table and ONE ref_id prefix, and item_id always holds that '
    'table''s PK: '
    'regulations -> chunks_v2 (reg:<chunks_v2.id>) | '
    'compliance -> services (compliance:<sha1[:16] of service_ref>) | '
    'cases -> cases (case:<cases.case_ref>) | '
    'circulars -> circulars (circular:<circulars.id>) | '
    'articles -> articles_v2 (article:<articles_v2.id>) | '
    'regulation_docs -> regulations_v2 (regdoc:<regulations_v2.id>) | '
    'principles -> official_principles (principle:<official_principles.id>). '
    'regulation_docs is the WHOLE نظام; regulations is a CHUNK of one — do not '
    'conflate them, and never mint a reg: ref_id for a regulations_v2 or '
    'articles_v2 uuid (it inserts cleanly and renders a dead stub). A domain '
    'added to the code must land HERE, in references_service.by_domain AND in '
    '_stub_reference together — a missing CHECK entry silently loses the ref.';
