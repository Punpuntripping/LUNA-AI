-- Migration 057: split quota windows per meter — ord stays daily+weekly,
-- OCR + web flip to a single monthly cap.
--
-- Frontend design (Settings → حدود الاستخدام popup) shows:
--   * ord (الاستهلاك العادي)  — daily and weekly bars (chat tokens spike fast)
--   * ocr (الاستخراج)         — monthly bar (heavy bursts; per-day cap was too tight)
--   * web (البحث)             — monthly bar (future skill; same pattern as OCR)
--
-- This migration brings the schema in line with the new gate behaviour:
-- shared/quota.check now reads monthly windows for ocr + web. The underlying
-- daily Redis buckets stay (still the storage unit); only the limits + the
-- aggregation window change.
--
-- Defaults: monthly = roughly the prior weekly × 3, matching the relaxed
-- window length:
--   ocr_pages_monthly_limit   = 600 pages   (was 200 weekly)
--   web_calls_monthly_limit   = 300 calls   (was 80 weekly)
--
-- Dependencies:
--   - 056_user_quotas.sql (defines the columns this migration replaces)
--
-- This migration is idempotent.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS ocr_pages_monthly_limit  INTEGER NOT NULL DEFAULT 600,
    ADD COLUMN IF NOT EXISTS web_calls_monthly_limit  INTEGER NOT NULL DEFAULT 300;

ALTER TABLE public.users
    DROP COLUMN IF EXISTS ocr_pages_daily_limit,
    DROP COLUMN IF EXISTS ocr_pages_weekly_limit,
    DROP COLUMN IF EXISTS web_calls_daily_limit,
    DROP COLUMN IF EXISTS web_calls_weekly_limit;

COMMENT ON COLUMN public.users.ocr_pages_monthly_limit IS
    'Max OCR pages per rolling 30 days. Gate sums the last 30 daily Redis '
    'buckets and rejects when (current + projected) > limit.';
COMMENT ON COLUMN public.users.web_calls_monthly_limit IS
    'Max web-search calls per rolling 30 days. Future skill — scaffold only, '
    'not enforced until the skill ships.';
