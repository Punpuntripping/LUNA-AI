-- 178: cases.year_gregorian — a Gregorian year for every judgment.
--
-- The aggregator weighs a ruling by its age («سنة القضية: 1989»), but only 60%
-- of cases carry date_gregorian, and the dateless 40% are mostly the OLD rows
-- (1402–1435هـ volumes) — exactly the ones the age signal is for.
--
-- A separate YEAR column, not a backfill of date_gregorian: ~4k rows only have a
-- year (volume year, decision number «IR-2020-86»), and date_gregorian is
-- published on /judgments («التاريخ الميلادي», JSON-LD, hub ordering) — filling
-- it with invented days would publish fabricated dates. date_gregorian stays
-- truthful; this column is the agents' year.
--
-- year_source records where the year came from, best precision first:
--   date_gregorian   extract(year) of the real date
--   date_hijri       exact Umm al-Qura conversion of a full Hijri date
--   date_hijri_year  Hijri year only (mid-year conversion, ±1)
--   gregorian_ref    Gregorian year in the decision number / source folder
--   decision_number  Hijri year printed in the decision number («201/ر/1446ه»)
--   volume_year      year of the published volume the ruling appears in
--   appeal_date      the appeal decision's date (first-instance date missing)
--   content_date     latest full Hijri date in the ruling text
--   volume_range     midpoint of a multi-year volume («1402–1426هـ»), ±12
-- The date_gregorian rows are filled below; the rest by
-- scripts/backfill_case_year_gregorian.py.
--
-- PROD NOTE (applied 2026-10-17): run as one transaction, the UPDATE held the
-- ALTER's exclusive lock on cases for >1 min (every row re-enters the HNSW
-- index) and was cancelled. Prod got the DDL alone, then the UPDATE in
-- 3–6k-row batches. Do the same on any large database.

ALTER TABLE public.cases
  ADD COLUMN IF NOT EXISTS year_gregorian smallint
    CHECK (year_gregorian BETWEEN 1900 AND 2100),
  ADD COLUMN IF NOT EXISTS year_source text;

COMMENT ON COLUMN public.cases.year_gregorian IS
  'Gregorian year of the judgment for agents (age weighting). See migration 178 / year_source.';
COMMENT ON COLUMN public.cases.year_source IS
  'Provenance of year_gregorian: date_gregorian | date_hijri | date_hijri_year | gregorian_ref | decision_number | volume_year | appeal_date | content_date | volume_range';

UPDATE public.cases
SET year_gregorian = extract(year FROM date_gregorian)::smallint,
    year_source = 'date_gregorian'
WHERE date_gregorian IS NOT NULL AND year_gregorian IS NULL;
