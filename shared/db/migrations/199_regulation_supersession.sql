-- 199_regulation_supersession.sql
--
-- Supersession and transition periods: the schema, plus the first two pairs.
-- Spec: .claude/plans/regulation_supersession_transition.md
--
-- WHY
-- ---
-- `status_class='cancelled'` is one bit, and a replaced law is not one bit. The
-- new نظام التنفيذ left the old law alive for a transition period, and left two
-- of its parts alive with no end date. Flagged «ملغي» they are designed to be
-- dropped by the reranker, so the aggregator never sees law that still applies.
-- These columns record the dates and the surviving parts as data, and
-- shared/library/supersession.py turns them into behaviour.
--
-- WHAT THIS DOES
-- --------------
-- §1 Four nullable columns on regulation_v2.regulations. A row with none of
--    them set behaves exactly as before.
-- §2 CHECKs: `superseded_by` is an array; `superseded_dates` holds all three
--    dates in ascending order; the two columns are set together.
-- §3 Appends the columns to public.regulations_v2.
--    ⚠ The view is `security_invoker=on`, and CREATE OR REPLACE VIEW REPLACES
--    reloptions even when the statement names none. Restated below and
--    self-tested in §6: dropping it would run the view as its owner (postgres)
--    for every caller, past the base table's grants and RLS.
--    Grants (relacl) survive CREATE OR REPLACE; `library_regulations_ranked`
--    depends on this view and is untouched by appended columns.
-- §4 The two pairs, end dates INCLUSIVE:
--      17486_reg_008 -> 6000_reg_0002   (نظام)
--      17642_reg_026 -> 6000_reg_0001   (لائحة)
--    Successor titles are read from the live rows, not typed here.
-- §5 `transition_period_note` on both new rows: clauses ثانياً to سابعاً of the
--    royal decree, taken by substring() from `intro` of 6000_reg_0002 and never
--    retyped. FIRST occurrence on purpose: `intro` holds the decree and then the
--    Cabinet decision, which repeats the same clause numbers.
--
-- chunk_refs, checked against the chunk text on 2026-10-30
-- -------------------------------------------------------
-- The old law is clean: 17486_reg_008_chunk_005 is exactly مواد 23-33, and
-- chunk_010 opens with الفصل الأول (الإعسار, مواد 77-82). It also holds
-- الحبس التنفيذي (83-86); a mixed chunk stays eligible and the note says which
-- part is alive.
--
-- The old لائحة is NOT clean. Its chunks split by size, so both parts straddle:
--   الحجز التحفظي  chunk_007  from `# الباب الثاني` on: ١/٢٣ to ١/٢٨
--                  chunk_008  ٢/٢٨ to ٣/٣٣, ending on the bare marker «٤/٣٣»
--                  chunk_009  opens with the BODY of ٤/٣٣, then الباب الثالث
--   الإعسار        chunk_020  from `# الباب الخامس` on: ١/٧٧ to ٥/٧٧
--                  chunk_021  ٦/٧٧ to ٥/٨١, then الحبس التنفيذي
-- chunk_022 only cross-refers to الإعسار from inside ٣/٨٣, so it is out.
-- chunk_009 and chunk_020 are mostly ended text kept in for one and five
-- surviving paragraphs: wrongly flagging law in force is the worse error.
--
-- Idempotent: columns are IF NOT EXISTS, constraints are dropped and re-added,
-- updates are value-assignments.
--
-- ROLLBACK
-- --------
-- Agents, no deploy, back to today's behaviour within 5 minutes (the cache TTL):
--   update regulation_v2.regulations
--      set superseded_by = null, superseded_dates = null
--    where reg_ref in ('17486_reg_008', '17642_reg_026');
-- Then, to remove the data entirely:
--   update regulation_v2.regulations
--      set transition_period_note = null, effective_date = null
--    where reg_ref in ('6000_reg_0002', '6000_reg_0001');
-- Leave the columns in place: nullable and unused they cost nothing, and
-- CREATE OR REPLACE VIEW cannot remove view columns, so dropping them means
-- DROP VIEW and rebuilding `library_regulations_ranked` plus every grant.
--
-- AFTER: select refresh_search_index('regulation');
--        select refresh_bm25_stats('regulation');
-- Why: search_index still holds 17642_reg_026 under the plain title
-- «اللائحة التنفيذية لنظام التنفيذ», not its live «(القديم)» title, so BM25
-- exact-pins the old لائحة for that name (score 1006 on 2026-10-30).
-- ⚠ The refresh does NOT add the new laws: it indexes only rows with a slug in
-- seo_item_meta, and 6000_reg_0001 / 6000_reg_0002 have none until the library
-- step. Until then they are reachable by title only through the ILIKE rung.

begin;

-- ---------------------------------------------------------------------------
-- §1  Columns
-- ---------------------------------------------------------------------------

alter table regulation_v2.regulations
  add column if not exists effective_date         date,
  add column if not exists superseded_by          jsonb,
  add column if not exists superseded_dates       jsonb,
  add column if not exists transition_period_note text;

-- ---------------------------------------------------------------------------
-- §2  Constraints
-- ---------------------------------------------------------------------------

alter table regulation_v2.regulations
  drop constraint if exists regulations_superseded_by_is_array;
alter table regulation_v2.regulations
  add constraint regulations_superseded_by_is_array
  check (superseded_by is null or jsonb_typeof(superseded_by) = 'array');

-- `is true` matters: a missing key or a JSON null makes the comparison NULL, and
-- a CHECK that evaluates to NULL PASSES.
alter table regulation_v2.regulations
  drop constraint if exists regulations_superseded_dates_ordered;
alter table regulation_v2.regulations
  add constraint regulations_superseded_dates_ordered
  check (
    superseded_dates is null
    or (
      jsonb_typeof(superseded_dates) = 'object'
      and (superseded_dates ->> 'superseded_on')::date < (superseded_dates ->> 'period_1_end')::date
      and (superseded_dates ->> 'period_1_end')::date  < (superseded_dates ->> 'period_2_end')::date
    ) is true
  );

alter table regulation_v2.regulations
  drop constraint if exists regulations_superseded_set_together;
alter table regulation_v2.regulations
  add constraint regulations_superseded_set_together
  check ((superseded_by is null) = (superseded_dates is null));

-- ---------------------------------------------------------------------------
-- §3  The view (existing 29 columns unchanged, new ones at the END)
-- ---------------------------------------------------------------------------

create or replace view public.regulations_v2
with (security_invoker = on) as
select id,
       reg_ref,
       entity_id,
       entity_ref,
       entity_name,
       title,
       clean_title,
       core_subject,
       doc_type_raw,
       doc_type_bucket,
       sectors,
       parent_law_id,
       doc_relation,
       status_class,
       status_raw,
       legal_authority,
       start_date,
       end_date,
       landing_url,
       fallback_url,
       pdf_url,
       intro,
       scope,
       obligations,
       summary,
       llm_summary,
       definitions,
       summary_embedding,
       ingested_at,
       effective_date,
       superseded_by,
       superseded_dates,
       transition_period_note
  from regulation_v2.regulations;

-- ---------------------------------------------------------------------------
-- §4  The two pairs
-- ---------------------------------------------------------------------------

update regulation_v2.regulations o
   set superseded_by = jsonb_build_array(
         jsonb_build_object('reg_ref', n.reg_ref,
                            'title',   coalesce(n.clean_title, n.title)),
         jsonb_build_object('reg_ref', null,
                            'title',   'نظام المرافعات الشرعية',
                            'part',    'الحجز التحفظي',
                            'pending', true,
                            'chunk_refs', jsonb_build_array('17486_reg_008_chunk_005')),
         jsonb_build_object('reg_ref', null,
                            'title',   'تشريع الإعسار المدني',
                            'part',    'الإعسار',
                            'pending', true,
                            'chunk_refs', jsonb_build_array('17486_reg_008_chunk_010'))
       ),
       superseded_dates = jsonb_build_object('superseded_on', '2026-10-28',
                                             'period_1_end',  '2027-01-28',
                                             'period_2_end',  '2027-10-28')
  from regulation_v2.regulations n
 where o.reg_ref = '17486_reg_008'
   and n.reg_ref = '6000_reg_0002';

update regulation_v2.regulations o
   set superseded_by = jsonb_build_array(
         jsonb_build_object('reg_ref', n.reg_ref,
                            'title',   coalesce(n.clean_title, n.title)),
         jsonb_build_object('reg_ref', null,
                            'title',   'نظام المرافعات الشرعية',
                            'part',    'الحجز التحفظي',
                            'pending', true,
                            'chunk_refs', jsonb_build_array('17642_reg_026_chunk_007',
                                                            '17642_reg_026_chunk_008',
                                                            '17642_reg_026_chunk_009')),
         jsonb_build_object('reg_ref', null,
                            'title',   'تشريع الإعسار المدني',
                            'part',    'الإعسار',
                            'pending', true,
                            'chunk_refs', jsonb_build_array('17642_reg_026_chunk_020',
                                                            '17642_reg_026_chunk_021'))
       ),
       superseded_dates = jsonb_build_object('superseded_on', '2026-10-28',
                                             'period_1_end',  '2027-01-28',
                                             'period_2_end',  '2027-10-28')
  from regulation_v2.regulations n
 where o.reg_ref = '17642_reg_026'
   and n.reg_ref = '6000_reg_0001';

-- ---------------------------------------------------------------------------
-- §5  The note, and the new rows' effective date
-- ---------------------------------------------------------------------------

update regulation_v2.regulations n
   set effective_date         = date '2026-10-28',
       transition_period_note = s.note
  from (
    -- First line names the instrument, copied from the row's own legal_authority,
    -- so the aggregator can cite the clause with its source. The clauses themselves
    -- never carry the decree's number.
    select (legal_authority::jsonb ->> 'authority_basis') || E':\n' ||
           btrim(
             substring(intro
                       from position('ثانياً:' in intro)
                       for  position('ثامناً:' in intro) - position('ثانياً:' in intro)),
             E' \n\r\t'
           ) as note
      from regulation_v2.regulations
     where reg_ref = '6000_reg_0002'
  ) s
 where n.reg_ref in ('6000_reg_0002', '6000_reg_0001');

-- ---------------------------------------------------------------------------
-- §6  Self-tests: any failure aborts the whole migration
-- ---------------------------------------------------------------------------

do $$
declare
  v_note     text;
  v_old_rows integer;
  v_refs     integer;
  v_options  text[];
begin
  select transition_period_note into v_note
    from regulation_v2.regulations where reg_ref = '6000_reg_0002';

  if v_note is null or v_note = '' then
    raise exception '199: transition_period_note is empty';
  end if;
  if position('م/237' in split_part(v_note, E'\n', 1)) = 0 then
    raise exception '199: note does not open with its instrument';
  end if;
  if position('ثانياً:' in split_part(v_note, E'\n', 2)) <> 1 then
    raise exception '199: note body does not start at clause two';
  end if;
  if position('الحجز التحفظي' in v_note) = 0 or position('الإعسار' in v_note) = 0 then
    raise exception '199: note is missing a surviving part';
  end if;
  if position('ثامناً:' in v_note) > 0 then
    raise exception '199: note runs past clause seven';
  end if;

  if (select transition_period_note from regulation_v2.regulations
       where reg_ref = '6000_reg_0001') is distinct from v_note then
    raise exception '199: the two new rows do not share the note';
  end if;

  select count(*) into v_old_rows
    from regulation_v2.regulations
   where reg_ref in ('17486_reg_008', '17642_reg_026')
     and jsonb_array_length(superseded_by) = 3
     and superseded_by -> 0 ->> 'title' <> ''
     and superseded_dates is not null;
  if v_old_rows <> 2 then
    raise exception '199: expected 2 old rows with a full superseded_by, got %', v_old_rows;
  end if;

  -- Every chunk_ref must exist AND belong to the row that names it.
  select count(*) into v_refs
    from regulation_v2.regulations r
    cross join lateral jsonb_array_elements(r.superseded_by) e
    cross join lateral jsonb_array_elements_text(e -> 'chunk_refs') ref
    join regulation_v2.chunks c on c.chunk_ref = ref and c.regulation_id = r.id
   where r.reg_ref in ('17486_reg_008', '17642_reg_026');
  if v_refs <> 7 then
    raise exception '199: expected 7 valid chunk_refs, got %', v_refs;
  end if;

  select c.reloptions into v_options
    from pg_class c
    join pg_namespace ns on ns.oid = c.relnamespace
   where ns.nspname = 'public' and c.relname = 'regulations_v2';
  if v_options is null or not ('security_invoker=on' = any (v_options)) then
    raise exception '199: public.regulations_v2 lost security_invoker (%)', v_options;
  end if;

  if not has_table_privilege('service_role', 'public.regulations_v2', 'select') then
    raise exception '199: service_role lost SELECT on public.regulations_v2';
  end if;
end;
$$;

commit;
