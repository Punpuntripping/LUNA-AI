-- Migration 170: give the free plan 3 OCR pages / 30d (was 0 = "not included").
--
-- The quota gate reads plans.ocr_pages_monthly, so free users can now attach a
-- scanned document up to 3 pages per rolling 30-day window. QuotaUpgradeDialog
-- keys «غير متاحة في الباقة المجانية» on limit <= 0, so with a positive limit it
-- shows the normal "limit reached" copy on its own.
-- Plan rows are data; idempotent UPDATE (plan cache refreshes within 5 min).
-- Dependencies: 068, 129.

UPDATE public.plans SET
    ocr_pages_monthly = 3,
    updated_at        = now()
WHERE plan_id = 'free'
  AND ocr_pages_monthly IS DISTINCT FROM 3;
