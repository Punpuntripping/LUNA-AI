-- Migration 148: sync the `usage_limits` product doc to the new `max` caps.
--
-- Depends on 147 (the catalog change: max → 75 session / 375 weekly). This is
-- the SAME table /learn/usage-limits renders, but the copy the ROUTER reads
-- when a user asks ريحان about limits — `product_docs.usage_limits`, migration
-- 126. The two are maintained by hand and drift silently, which is exactly the
-- failure this migration exists to prevent: the agent quoting 50/250 to a
-- customer whose account enforces 75/375.
--
-- The عمليات columns are the measured deep_search range (3.19–4.61 points/run,
-- `llm_calls` ledger) divided into the new caps, floored on both ends the way
-- every other row in that table is:
--     75  / 4.61 = 16.2 → 16   ·  75  / 3.19 = 23.5 → 23
--     375 / 4.61 = 81.3 → 81   ·  375 / 3.19 = 117.5 → 117
--
-- A targeted replace, not a rewrite: everything else in the doc (the points
-- rationale, the windows, the «لا يذكر أسعاراً» rule) is untouched, so a doc
-- edited since 126 does not get reverted by running this.
--
-- ⚠ NO RIYAL AMOUNT ENTERS product_docs. `pricing` and `usage_limits` both
-- carry an explicit rule forbidding the agent from stating a price, and the
-- 289.90 from migration 147 must NOT be seeded here — the agent refers the user
-- to https://rayhanai.com/pricing, which is the only surface that can be right
-- about a promo. That is why this migration touches point caps only.

UPDATE public.product_docs
   SET content_md = replace(
         content_md,
         '| القصوى | 50 | 10–16 | 250 | 54–78 |',
         '| القصوى | 75 | 16–23 | 375 | 81–117 |'
       ),
       updated_at = now()
 WHERE doc_key = 'usage_limits'
   AND content_md LIKE '%| القصوى | 50 | 10–16 | 250 | 54–78 |%';

-- Verification (expect the new row, and no «250»/«10–16» left in the doc):
--   SELECT content_md LIKE '%| القصوى | 75 | 16–23 | 375 | 81–117 |%' AS updated,
--          content_md LIKE '%250%' AS stale_weekly
--     FROM public.product_docs WHERE doc_key = 'usage_limits';
