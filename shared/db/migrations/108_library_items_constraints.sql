-- ============================================================================
-- Migration 108 — bound the «مكتبتي» shelf write path
-- Found by the security review of the access-tiers build (2026-07-27), MEDIUM-3.
--
-- `library_items` is written by three AUTHED endpoints that have no entitlement
-- gate in front of them by design (saving is free at every tier, §5B.2), and
-- every field on the request body is attacker-controlled. Before this migration
-- the table accepted any `content_type` string and any `content_id` length, so a
-- single account could write unlimited junk rows at the 60/min ceiling
-- (~86k rows/day) — storage exhaustion, index bloat, and corruption of the
-- «الأكثر استخداماً» ranking signal §5B.3 says will later weight reference
-- ordering.
--
-- The API validates this too (`library_mine._resolve_ref`). Both layers are kept
-- deliberately: the API check gives a clean Arabic 400, and the constraint means
-- a future writer that forgets the check cannot corrupt the table.
--
-- NOT constrained: `content_id` shape. A مادة id is `'{uuid}#{article_no}'`
-- while everything else is a bare uuid, and forms/calculators may add more, so a
-- length bound is the honest limit — a regex here would break the next wing.
-- ============================================================================

-- Clean out anything that would block the constraints. Expected: zero rows —
-- the table is new and the API has always resolved slugs to canonical ids.
DELETE FROM public.library_items
 WHERE content_type NOT IN
       ('regulation', 'article', 'judgment', 'circular', 'service', 'form', 'calculator')
    OR length(content_id) > 200;

ALTER TABLE public.library_items
  DROP CONSTRAINT IF EXISTS library_items_content_type_valid;
ALTER TABLE public.library_items
  ADD CONSTRAINT library_items_content_type_valid
  CHECK (content_type IN
         ('regulation', 'article', 'judgment', 'circular', 'service', 'form', 'calculator'));

ALTER TABLE public.library_items
  DROP CONSTRAINT IF EXISTS library_items_content_id_len;
ALTER TABLE public.library_items
  ADD CONSTRAINT library_items_content_id_len
  CHECK (length(content_id) BETWEEN 1 AND 200);

-- Same bounds on the money ledger. It is only ever written by Layer B with a
-- server-resolved id, so this is defence in depth rather than a fix.
ALTER TABLE public.library_unlocks
  DROP CONSTRAINT IF EXISTS library_unlocks_content_type_valid;
ALTER TABLE public.library_unlocks
  ADD CONSTRAINT library_unlocks_content_type_valid
  CHECK (content_type IN
         ('regulation', 'article', 'judgment', 'circular', 'service', 'form', 'calculator'));

ALTER TABLE public.library_unlocks
  DROP CONSTRAINT IF EXISTS library_unlocks_content_id_len;
ALTER TABLE public.library_unlocks
  ADD CONSTRAINT library_unlocks_content_id_len
  CHECK (length(content_id) BETWEEN 1 AND 200);

COMMENT ON CONSTRAINT library_items_content_type_valid ON public.library_items IS
  'Mirrors library_items_service.SHELF_CONTENT_TYPES. The shelf write path is '
  'authed but ungated, so the DB keeps its own bound on what can land here.';
