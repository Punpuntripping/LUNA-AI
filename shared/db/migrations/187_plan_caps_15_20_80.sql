-- Migration 187: raise the session caps of the three sold plans; weekly = 5 × session.
--
-- Owner decision 2026-10-26. Deep search now costs 5–10 points a run (llm_calls
-- ledger since 2026-10-18, 92 turns: p25 4.16 · p50 5.10 · p75 6.84 · p90 8.98),
-- up from the 3–5 band the old caps were sized against.
--
--   plan    points_session    points_weekly
--   basic   10 → 15           50  → 75
--   pro     15 → 20           75  → 100
--   max     75 → 80           375 → 400
--
-- Prices, OCR pages, library unlocks and the promo plans are UNCHANGED. Capability
-- order still matches price order (15 < 20 < 80, 75 < 100 < 400), so the upgrade
-- ladder (`_upgrade_options`, `pricingPlansAbove`) cannot offer a downgrade.
--
-- Raising a cap is the safe direction to go DB-first: until the frontend ships,
-- /pricing under-states what the account enforces, never the reverse.
--
-- The `usage_limits` product doc (migration 126 — the copy the ROUTER quotes) is
-- synced in the same migration, as 148 did for max. Its عمليات columns are the cap
-- divided by the 5–10 band, floored on both ends:
--     15 → 1–3   ·  20 → 2–4   ·  80 → 8–16
--     75 → 7–15  ·  100 → 10–20 ·  400 → 40–80
-- Its «المجانية | 5 | 1 | 5 | 1» row has been wrong since 129 (free has no session
-- or weekly window, just 5 points / 30 days); it is replaced by the same footnote
-- /learn/usage-limits carries. Targeted replaces only — nothing else in the doc moves,
-- and no riyal amount enters product_docs.

UPDATE public.plans AS p SET
    points_session = v.points_session,
    points_weekly  = v.points_weekly,
    updated_at     = now()
FROM (VALUES
    ('basic', 15, 75),
    ('pro',   20, 100),
    ('max',   80, 400)
) AS v(plan_id, points_session, points_weekly)
WHERE p.plan_id = v.plan_id
  AND (p.points_session IS DISTINCT FROM v.points_session
    OR p.points_weekly  IS DISTINCT FROM v.points_weekly);

UPDATE public.product_docs
   SET content_md =
         replace(replace(replace(replace(replace(content_md,
           '| **البحث المعمّق** | **3–5 نقاط** — أثقل عملية في ريحان |',
           '| **البحث المعمّق** | **5–10 نقاط** — أثقل عملية في ريحان |'),
           E'| المجانية | 5 | 1 | 5 | 1 |\n',
           ''),
           '| الأساسية | 10 | 2–3 | 50 | 10–15 |',
           '| الأساسية | 15 | 1–3 | 75 | 7–15 |'),
           '| الاحترافية | 15 | 3–4 | 75 | 16–23 |',
           '| الاحترافية | 20 | 2–4 | 100 | 10–20 |'),
           '| القصوى | 75 | 16–23 | 375 | 81–117 |',
           E'| القصوى | 80 | 8–16 | 400 | 40–80 |\n\n'
           'الباقة المجانية خارج الجدول: نافذة واحدة بـ5 نقاط كل 30 يوماً — بحث معمّق واحد تقريباً، لتجربة ريحان قبل الاشتراك.'),
       updated_at = now()
 WHERE doc_key = 'usage_limits'
   AND content_md LIKE '%| القصوى | 75 | 16–23 | 375 | 81–117 |%';

-- Verification
--   SELECT plan_id, points_session, points_weekly FROM public.plans
--    WHERE plan_id IN ('basic','pro','max') ORDER BY points_session;
--   -- expect basic 15/75 · pro 20/100 · max 80/400
--   SELECT content_md LIKE '%5–10 نقاط%'                       AS cost_updated,
--          content_md LIKE '%| القصوى | 80 | 8–16 | 400 | 40–80 |%' AS max_updated,
--          content_md LIKE '%| المجانية |%'                     AS stale_free_row,
--          content_md LIKE '%3–5%'                               AS stale_cost
--     FROM public.product_docs WHERE doc_key = 'usage_limits';
--   -- expect t · t · f · f
