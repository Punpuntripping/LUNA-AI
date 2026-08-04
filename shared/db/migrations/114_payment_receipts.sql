-- ════════════════════════════════════════════════════════════════════════════
-- 114 — payment receipts: sequential receipt numbers + email-sent stamps
-- ════════════════════════════════════════════════════════════════════════════
--
-- Context (2026-08-04): receipts are plain «إيصال دفع» emails — the business
-- has NO VAT registration, so receipts carry NO tax language whatsoever (no
-- فاتورة ضريبية, no VAT split, no percentages). The vat_amount_sar /
-- net_amount_sar columns from 113 remain INTERNAL bookkeeping only and are
-- displayed nowhere. If a VAT number is obtained later, the same email gains
-- the number + ZATCA QR and becomes a simplified tax invoice — the sequential
-- numbering below is deliberately continuous from day one for that future.
--
-- * receipt_no — assigned by trigger on the transition to status='paid', from
--   a dedicated sequence. Trigger-owned so no backend code path can forget it,
--   and a webhook retry can't double-assign (guarded on IS NULL).
-- * receipt_sent_at / refund_receipt_sent_at — atomic send-claim stamps. The
--   sender claims by `UPDATE ... WHERE ... IS NULL` and only sends when the
--   claim returns a row, so the verify-path and webhook-path can never send
--   the same email twice.

ALTER TABLE public.payment_transactions
    ADD COLUMN IF NOT EXISTS receipt_no             bigint,
    ADD COLUMN IF NOT EXISTS receipt_sent_at        timestamptz,
    ADD COLUMN IF NOT EXISTS refund_receipt_sent_at timestamptz;

CREATE SEQUENCE IF NOT EXISTS public.payment_receipt_no_seq START 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_receipt_no
    ON public.payment_transactions (receipt_no)
    WHERE receipt_no IS NOT NULL;

CREATE OR REPLACE FUNCTION public.assign_payment_receipt_no()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'paid' AND NEW.receipt_no IS NULL THEN
        NEW.receipt_no := nextval('public.payment_receipt_no_seq');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_payment_receipt_no ON public.payment_transactions;
CREATE TRIGGER trg_payment_receipt_no
    BEFORE UPDATE OF status ON public.payment_transactions
    FOR EACH ROW
    EXECUTE FUNCTION public.assign_payment_receipt_no();

COMMENT ON COLUMN public.payment_transactions.receipt_no IS
    'Sequential receipt number (114), assigned by trigger on the paid '
    'transition. Continuous numbering so receipts can become ZATCA invoices '
    'later without a numbering break.';
COMMENT ON COLUMN public.payment_transactions.receipt_sent_at IS
    'Atomic claim stamp for the purchase receipt email — claimed via '
    'conditional UPDATE before sending, so concurrent paid-paths cannot '
    'double-send.';
COMMENT ON COLUMN public.payment_transactions.refund_receipt_sent_at IS
    'Same claim mechanism for the refund receipt email.';
