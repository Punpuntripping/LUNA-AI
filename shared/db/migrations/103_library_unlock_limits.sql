-- ============================================================================
-- Migration 103 — library unlock allowances
-- Plan: .claude/plans/access_tiers_gating.md  PART 3 "Migration 102"
--       (renumbered to 103 — 102 was already taken by the circulars-domain fix;
--        see access_tiers_gating_DECISIONS.md D1)
--
-- Adds the per-period library-unlock allowance to the plans catalog and the
-- per-user escape hatch on user_subscriptions, following the established
-- convention exactly: limits live on `plans`, overrides live on
-- `user_subscriptions` as `*_override` columns (mirrors points_*_override,
-- ocr_pages_monthly_override).
--
-- NULL = unlimited. 0 would mean "not included in your plan" — no plan uses it
-- today, but shared/quota renders that case ("باقتك الحالية لا تشمل …").
-- ============================================================================

ALTER TABLE public.plans
  ADD COLUMN IF NOT EXISTS library_unlocks_period INTEGER;

COMMENT ON COLUMN public.plans.library_unlocks_period IS
  'Library unlocks allowed per subscription period (NULL = unlimited). '
  'The period is the plan duration_days window; for plans with no duration '
  '(free) it is the UTC calendar month. Caps NEW unlocks only — previously '
  'unlocked items are permanent and never re-charged.';

ALTER TABLE public.user_subscriptions
  ADD COLUMN IF NOT EXISTS library_unlocks_override INTEGER;

COMMENT ON COLUMN public.user_subscriptions.library_unlocks_override IS
  'Per-user override for plans.library_unlocks_period (NULL = use the plan value).';

-- Real limits from day one (user decision 2026-07-27 — no soak period).
UPDATE public.plans SET library_unlocks_period = 10   WHERE plan_id = 'free';
UPDATE public.plans SET library_unlocks_period = 100  WHERE plan_id IN ('basic', 'marketing_lawyer');
UPDATE public.plans SET library_unlocks_period = 200  WHERE plan_id = 'pro';
UPDATE public.plans SET library_unlocks_period = 1000 WHERE plan_id = 'max';
-- 'dev' deliberately left NULL = unlimited.
