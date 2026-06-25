-- Migration 077: shrink the free plan to 3 points per month.
--
-- `free` is the post-expiry fallback tier (an expired time-boxed plan falls back
-- to free's limits — it is NOT a self-serve signup plan; new accounts are LOCKED
-- until assigned). Drop it to a 3-point monthly trickle.
--
-- The monthly (30d) window is the binding cap. session (5h) and weekly (7d) are
-- sub-windows CONTAINED within the month, so they are also set to 3 — a
-- sub-window must never grant more than the window that contains it, otherwise
-- the gate would block on the hidden monthly while the UI session/weekly bars
-- still show headroom (looks like a bug). With all three = 3 every meter is
-- honest and the effective cap is exactly 3 points per rolling 30 days.
--
--   plan   session  weekly  monthly  ocr/30d  web/30d  duration
--   free   3        3       3        0        0        — (non-expiring)
--
-- ocr + web stay 0 (not included). Plan rows are data; idempotent UPDATE
-- (in-process plan cache refreshes within 5 min). Dependencies: 068, 076.

UPDATE public.plans SET
    points_session = 3,
    points_weekly  = 3,
    points_monthly = 3,
    updated_at     = now()
WHERE plan_id = 'free';
