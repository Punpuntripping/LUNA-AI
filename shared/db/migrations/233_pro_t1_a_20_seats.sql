-- =============================================================================
-- 233: Pro tier 1 «19.90 × 2 months» batch — 500 seats → 20
-- =============================================================================
-- Owner, 2026-11-23: cut the 19.90-for-2-months seats; once they are gone, new
-- buyers move to pro_t1_b (19.90 × 1 month, 500 seats). promo_active_batch picks
-- the lowest batch_order whose live seats (early_adopter_seats, released_at IS
-- NULL) are below seat_limit, so this is a data change only.
--
-- Applied to prod on 2026-11-23 as TWO recorded migrations: 233_pro_t1_a_50_seats
-- (50) and, minutes later at the owner's revision, 233b_pro_t1_a_20_seats (20).
-- This file is the final state. 13 live seats at the change → 7 left.
--
-- Held seats keep their price and months (promo terms §2). The web copy takes
-- price AND months from the quote, so it says «لأول شهر» once the batch rolls.
-- Only operator telemetry reads seat_limit in the backend (promo.batch_near_full
-- warns at ≥ 90%).
--
-- Rollback: UPDATE public.promo_batches SET seat_limit = 500, updated_at = now()
--            WHERE batch_id = 'pro_t1_a';

BEGIN;

SET LOCAL lock_timeout = '5s';

UPDATE public.promo_batches
   SET seat_limit = 20,
       updated_at = now()
 WHERE batch_id = 'pro_t1_a';

DO $selftest$
BEGIN
    IF (SELECT seat_limit FROM public.promo_batches WHERE batch_id = 'pro_t1_a') IS DISTINCT FROM 20 THEN
        RAISE EXCEPTION '233: pro_t1_a seat_limit is not 20';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.promo_batches
                    WHERE batch_id = 'pro_t1_b' AND enabled AND price_sar = 19.90 AND months = 1
                      AND batch_order > (SELECT batch_order FROM public.promo_batches WHERE batch_id = 'pro_t1_a')) THEN
        RAISE EXCEPTION '233: pro_t1_b is not the enabled 19.90 x 1 month batch after pro_t1_a';
    END IF;
END;
$selftest$;

COMMIT;
