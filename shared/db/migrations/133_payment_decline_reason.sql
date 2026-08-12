-- ============================================================================
-- 133 — payment_transactions.decline_reason
--
-- WHY: a failed payment already retains the provider's answer in `raw_payload`
-- (jsonb), so nothing is being *lost* today. But answering "how many renewals
-- declined for an expired card vs an empty balance" means digging through JSON,
-- and that question is the whole point of a dunning ladder: a card-expiry
-- problem and a balance problem call for different copy, different retry timing,
-- and different expectations about recovery. This promotes the one field worth
-- grouping by into a column.
--
-- Populated by `payment_service._mark_failed`, which is the single function BOTH
-- failure paths run — a declined browser purchase and a declined renewal — so
-- this covers renewals and ordinary checkouts alike.
--
-- ⚠ RAW PROVIDER TEXT, NOT A TAXONOMY. This stores Moyasar's own message
-- verbatim (truncated at the writer). It is deliberately NOT a normalised enum
-- like 'insufficient_funds': nobody has yet observed what strings this account
-- actually returns, and inventing categories before seeing the vocabulary would
-- produce a column that quietly mis-bins the real world. Once a few hundred real
-- declines exist, GROUP BY this column to learn the vocabulary, THEN add a
-- derived category if it still earns its place.
--
-- Additive and nullable — no backfill, no behaviour change. Existing failed rows
-- keep NULL; their reason is still in raw_payload. Safe to apply at any time,
-- before or after the backend that writes it (a NULL column and a missing column
-- both read as "no reason recorded" to every consumer).
--
-- Idempotent: re-runnable.
-- ============================================================================

ALTER TABLE public.payment_transactions
    ADD COLUMN IF NOT EXISTS decline_reason text;

COMMENT ON COLUMN public.payment_transactions.decline_reason IS
    'The provider''s own decline message, verbatim and truncated (133). Set by '
    '_mark_failed on any terminal failure — declined browser purchase OR declined '
    'renewal. NULL means no reason was recorded: either the row never failed, it '
    'failed before 133 shipped, or the provider sent no message. NOT a normalised '
    'category — group by it to learn the real vocabulary before deriving one. The '
    'full provider response remains in raw_payload either way.';

-- Deliberately NO index. Grouping over the handful of failed rows this table will
-- hold for a long time is a trivial scan, and an index on free-text provider
-- messages with unknown cardinality is a guess. Add one when a real query is
-- slow, not in anticipation of one.
