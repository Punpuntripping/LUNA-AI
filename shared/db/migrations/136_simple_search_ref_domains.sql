-- Migration 136: allow ``domain='articles'`` and ``domain='regulation_docs'``
-- on workspace_item_references (simple_search family, plan §6.1a / §6.3).
--
-- The `simple_search` lookup family cites two objects deep_search never could:
--
--   * a single مادة       — ref_id ``article:<articles_v2.id>``   → domain 'articles'
--   * a whole نظام        — ref_id ``regdoc:<regulations_v2.id>`` → domain 'regulation_docs'
--
-- WHY NEW DOMAINS AND NEW PREFIXES (plan §6.2, trap 4) — the existing
-- ``domain='regulations'`` hard-assumes ``item_id`` is a **chunks_v2.id**
-- (``references_service._reg_chunk_id_from_row`` / ``_build_reg_shells``). A
-- regulations_v2 or articles_v2 uuid smuggled in under ``reg:`` passes the uuid
-- check, inserts cleanly, then finds nothing on read: the shell is pruned and the
-- card renders as a dead stub with no «عرض المصدر». Zero errors anywhere in the
-- chain. Distinct domains + distinct ref_id prefixes are what make that
-- impossible.
--
-- ``regulation_docs``, NOT ``regulations`` — that name is taken and means
-- **a chunk**.
--
-- MIGRATION BEFORE CODE (plan §6.3 / §9 trap 1). ``persist_item_references``
-- writes all refs of a WI in ONE batch insert and the publisher swallows the
-- exception by design. Before the per-row retry existed (references_service.py
-- ~:1029), a single out-of-CHECK row therefore took the WHOLE panel down —
-- that is how migration 102's circulars gap shipped four agent_search items with
-- metadata.ref_count=9 and ZERO reference rows. The retry now localises the loss
-- to the offending ref, but it is still a SILENT loss (ERROR log only, return
-- value ignored by the publisher). So this CHECK widens before any code emits
-- either new domain.
--
-- Dependencies:
--   * 049_workspace_item_references.sql (table + original CHECK)
--   * 050_workspace_item_references_uuid_and_ref_id.sql (two-key design)
--   * 102_workspace_item_references_circulars_domain.sql (the 4-domain CHECK)
--
-- Idempotent: drop-then-add by constraint name (same shape as 102).

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
        'regulation_docs'
    ));

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
    'regulation_docs -> regulations_v2 (regdoc:<regulations_v2.id>). '
    'regulation_docs is the WHOLE نظام; regulations is a CHUNK of one — do not '
    'conflate them, and never mint a reg: ref_id for a regulations_v2 or '
    'articles_v2 uuid (it inserts cleanly and renders a dead stub).';
