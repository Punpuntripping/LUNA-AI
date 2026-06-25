-- Migration 078: lower the basic plan's OCR cap from 40 → 15 pages / 30d.
--
-- Display + enforcement stay in sync (pricing page shows "١٥ صفحة استخراج نص").
-- basic is a 7-day plan, so this is effectively 15 pages within the plan's life.
-- Plan rows are data; idempotent UPDATE (plan cache refreshes within 5 min).
-- Dependencies: 068, 076, 077.

UPDATE public.plans SET
    ocr_pages_monthly = 15,
    updated_at        = now()
WHERE plan_id = 'basic';
