-- Migration 188: `max` session cap 80 → 100, weekly 400 → 500 (weekly = 5 × session).
--
-- Owner decision 2026-10-26, same day as 187 (which set max to 80/400). basic
-- (15/75) and pro (20/100) are untouched. Raising a cap is the safe direction to
-- go DB-first: /pricing under-states what the account enforces until the
-- frontend ships, never the reverse.
--
-- The `usage_limits` product doc (the copy the ROUTER quotes) is synced here as
-- 148/187 did. Its عمليات columns are the cap divided by the 5–10 deep_search
-- band, floored on both ends: 100 → 10–20 · 500 → 50–100.

UPDATE public.plans SET
    points_session = 100,
    points_weekly  = 500,
    updated_at     = now()
WHERE plan_id = 'max'
  AND (points_session IS DISTINCT FROM 100
    OR points_weekly  IS DISTINCT FROM 500);

UPDATE public.product_docs
   SET content_md = replace(
         content_md,
         '| القصوى | 80 | 8–16 | 400 | 40–80 |',
         '| القصوى | 100 | 10–20 | 500 | 50–100 |'
       ),
       updated_at = now()
 WHERE doc_key = 'usage_limits'
   AND content_md LIKE '%| القصوى | 80 | 8–16 | 400 | 40–80 |%';

-- Verification (expect max · 100 · 500, and the new doc row):
--   SELECT plan_id, points_session, points_weekly FROM public.plans WHERE plan_id = 'max';
--   SELECT content_md LIKE '%| القصوى | 100 | 10–20 | 500 | 50–100 |%' AS updated
--     FROM public.product_docs WHERE doc_key = 'usage_limits';
