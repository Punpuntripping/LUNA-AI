-- 149_labor_law_supersession_cleanup.sql
--
-- Retire the library surface of the superseded نظام العمل and hand its URL,
-- SEO equity and user state to the current copy.
--
-- CONTEXT
-- -------
-- On 2026-08-29 six regulations were re-ingested from their owning entities and
-- the old copies retired. Five were merely flagged (`status_class='cancelled'`),
-- but one -- `17609_reg_122` / نظام العمل, uuid da51024f-… -- was HARD DELETED
-- from `regulation_v2.regulations` and archived into
-- `regulation_v2.superseded_archive_20260829` (745 rows, 16:23Z).
--
-- Deleting the corpus row removed the regulation from the API (which now 404s
-- «النظام غير موجود») but touched NOTHING on the public library, because the
-- library surface is a set of SIDECAR tables keyed by the regulation uuid and
-- deliberately NOT foreign-keyed to the pipeline-owned corpus (095: "the corpus
-- tables/views are pipeline-owned and may be re-ingested -- SEO state must
-- survive reloads"). That design is right; it just has no retirement path, so
-- the surface outlived its regulation:
--
--   * seo_item_meta      slug=نظام-العمل, rank 6 of 1686, seo_tier='open'
--   * seo_articles       233 rows, each carrying a MATERIALIZED `article_text`
--   * seo_sharh          229 rows -- every row in the table
--   * seo_item_meta      5 article rows (المواد 74/75/77/80/84)
--   * search_index       1 row (BM25 navigation search)
--   * related_items      2 rows
--   * document_relations 2 rows still pointing at 17609_reg_122
--   * library_items      5 users' shelves · library_unlocks 1 article
--
-- Because `seo_articles.article_text` is materialized, the page kept RENDERING:
-- /regulations/نظام-العمل served 233 articles of the PRE-M/44-1446هـ text at the
-- library's 6th-ranked slot, free (`seo_tier='open'`) and indexable, with the
-- sitemap still advertising it and all five مادة pages returning 200. The three
-- articles the current law adds (107 الأجر الإضافي · 139 · 172) were absent.
--
-- WHAT THIS MIGRATION DOES
-- ------------------------
-- §1  Archive every library-surface row it is about to remove or rewrite.
-- §2  Hand the sidecar identity -- slug, seo_tier, rank, usage_score -- to the
--     current copy `17900_reg_549` (cc23fc13-…), so /regulations/نظام-العمل and
--     the five /المادة-N URLs keep resolving to the same law at the same rank.
--     The slug is FREED before it is re-granted: `idx_seo_item_meta_slug_unique`
--     is UNIQUE on (content_type, slug) WHERE slug IS NOT NULL.
-- §3  Drop the derived rows -- they are rebuilt from the new corpus by
--     scripts/build_seo_article_index.py and the refresh_* RPCs.
-- §4  Carry user state across: library_items is BEHAVIOUR (updated in place);
--     library_unlocks is the BILLING LEDGER and is append-only by contract
--     (104), so the old row is LEFT INTACT and a new one is INSERTED.
--
-- WHY seo_sharh IS DELETED, NOT CARRIED
-- -------------------------------------
-- The 229 cached شرح rows are AI commentary written against the SUPERSEDED
-- article text. 93 of 233 articles differ between the two copies. Re-pointing
-- them at the new regulation would move commentary about repealed wording onto
-- current law -- silently, since a cached row is what suppresses the live LLM
-- call in the anon path (100). They are archived and dropped; re-generate with
--     python scripts/generate_sharh.py --reg cc23fc13-7078-4e54-8543-dc386fa43a74
-- Until then the anon مادة pages simply render no شرح teaser, which is the
-- correct failure mode.
--
-- NOT IN SCOPE: cross_references / document_relations / core_subjects for the
-- six new regulations. Those are corpus-ingest outputs, not library state, and
-- their builder does not live in this repo -- see the report in
-- agents_reports/regulation_supersession_2026-08-29.md §5.
--
-- Idempotent: re-running is a no-op (archive insert is ON CONFLICT DO NOTHING,
-- every delete is keyed on the old uuid, every upsert is a MERGE).

begin;

-- ---------------------------------------------------------------------------
-- §1  Archive
-- ---------------------------------------------------------------------------

create table if not exists regulation_v2.library_surface_archive_20260829 (
  id          bigserial primary key,
  reg_ref     text        not null,
  reg_uuid    text        not null,
  source_table text       not null,
  row_data    jsonb       not null,
  archived_at timestamptz not null default now()
);

comment on table regulation_v2.library_surface_archive_20260829 is
  'Library-side (public schema) rows retired by migration 149 when نظام العمل '
  '17609_reg_122 / da51024f-… was superseded by 17900_reg_549 / cc23fc13-…. '
  'Companion to superseded_archive_20260829, which holds the CORPUS rows. '
  'Restore-from is the reason this exists; nothing reads it at runtime.';

-- Guard: only archive once (the table has no natural key, so key on emptiness).
insert into regulation_v2.library_surface_archive_20260829 (reg_ref, reg_uuid, source_table, row_data)
select '17609_reg_122', 'da51024f-a713-48e7-af87-b6a541f055e4', t.src, t.row_data
from (
  select 'seo_item_meta' as src, to_jsonb(m) as row_data
    from public.seo_item_meta m
   where (m.content_type = 'regulation' and m.content_id = 'da51024f-a713-48e7-af87-b6a541f055e4')
      or (m.content_type = 'article'    and m.content_id like 'da51024f-a713-48e7-af87-b6a541f055e4#%')
  union all
  select 'seo_articles', to_jsonb(sa)
    from public.seo_articles sa
   where sa.regulation_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4'
  union all
  select 'seo_sharh', to_jsonb(sh)
    from public.seo_sharh sh
   where sh.regulation_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4'
  union all
  select 'search_index', to_jsonb(si)
    from public.search_index si
   where si.content_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4'
  union all
  select 'related_items', to_jsonb(ri)
    from public.related_items ri
   where ri.source_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4'
      or ri.target_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4'
  union all
  select 'document_relations', to_jsonb(d)
    from regulation_v2.document_relations d
   where d.source_ref = '17609_reg_122' or d.target_ref = '17609_reg_122'
  union all
  select 'library_items', to_jsonb(li)
    from public.library_items li
   where li.content_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4'
  union all
  select 'library_unlocks', to_jsonb(lu)
    from public.library_unlocks lu
   where lu.content_id::text like 'da51024f-a713-48e7-af87-b6a541f055e4%'
) t
where not exists (
  select 1 from regulation_v2.library_surface_archive_20260829
   where reg_uuid = 'da51024f-a713-48e7-af87-b6a541f055e4'
);

-- ---------------------------------------------------------------------------
-- §2  Hand the sidecar identity to the current copy
--
--     Order matters: the old slugged rows are removed FIRST so the unique
--     partial index on (content_type, slug) is free when the new rows land.
-- ---------------------------------------------------------------------------

-- 2a. Capture what we are transferring, then free it.
create temporary table _sidecar_carry on commit drop as
select
  'regulation'::text as content_type,
  'cc23fc13-7078-4e54-8543-dc386fa43a74'::text as content_id,
  m.slug, m.seo_tier, m.gate_override, m.indexable, m.rank, m.usage_score
from public.seo_item_meta m
where m.content_type = 'regulation'
  and m.content_id = 'da51024f-a713-48e7-af87-b6a541f055e4'
union all
select
  'article',
  'cc23fc13-7078-4e54-8543-dc386fa43a74' || substring(m.content_id from '#.*'),
  m.slug, m.seo_tier, m.gate_override, m.indexable, m.rank, m.usage_score
from public.seo_item_meta m
where m.content_type = 'article'
  and m.content_id like 'da51024f-a713-48e7-af87-b6a541f055e4#%';

delete from public.seo_item_meta
where (content_type = 'regulation' and content_id = 'da51024f-a713-48e7-af87-b6a541f055e4')
   or (content_type = 'article'    and content_id like 'da51024f-a713-48e7-af87-b6a541f055e4#%');

-- 2b. Grant it to 17900_reg_549. MERGE on the composite PK so a row that
--     already exists (none today) keeps anything not carried here.
insert into public.seo_item_meta
      (content_type, content_id, slug, seo_tier, gate_override, indexable, rank, usage_score, updated_at)
select content_type, content_id, slug, seo_tier, gate_override, indexable, rank, usage_score, now()
from _sidecar_carry
on conflict (content_type, content_id) do update
set slug        = excluded.slug,
    seo_tier    = excluded.seo_tier,
    indexable   = excluded.indexable,
    rank        = excluded.rank,
    usage_score = excluded.usage_score,
    updated_at  = now();

-- ---------------------------------------------------------------------------
-- §3  Drop the derived rows of the retired copy
--
--     seo_articles + search_index + related_items are all fully rebuildable
--     (build_seo_article_index.py · refresh_search_index · refresh_related_items).
--     seo_sharh is regenerated -- see the header note.
-- ---------------------------------------------------------------------------

delete from public.seo_articles
 where regulation_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4';

delete from public.seo_sharh
 where regulation_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4';

delete from public.search_index
 where content_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4';

delete from public.related_items
 where source_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4'
    or target_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4';

-- The corpus row is gone, so an edge naming it can never resolve again.
delete from regulation_v2.document_relations
 where source_ref = '17609_reg_122' or target_ref = '17609_reg_122';

-- ---------------------------------------------------------------------------
-- §4  Carry user state across
-- ---------------------------------------------------------------------------

-- 4a. library_items is BEHAVIOUR (the مكتبتي shelf), not money: update in place
--     so the five readers keep the item they opened, with its counters. Skip a
--     user who somehow already holds the new id (unique on user+type+content).
update public.library_items li
   set content_id = 'cc23fc13-7078-4e54-8543-dc386fa43a74'
 where li.content_id::text = 'da51024f-a713-48e7-af87-b6a541f055e4'
   and not exists (
     select 1 from public.library_items x
      where x.user_id = li.user_id
        and x.content_type = li.content_type
        and x.content_id::text = 'cc23fc13-7078-4e54-8543-dc386fa43a74'
   );

-- 4b. library_unlocks: DELIBERATELY NOT CARRIED. Two reasons, either sufficient.
--
--     (i)  It is the BILLING LEDGER -- "Never UPDATE this table" (104) -- and it
--          CHECKs `cost >= 1`, so a carry-over row cannot be written at cost 0.
--          Writing one at cost 1 would record a charge that never happened.
--     (ii) It is moot. §2 carries `seo_tier='open'` to the new regulation, and
--          resolve_gate() step (b) reads an article's gate from its PARENT's
--          tier: every مادة of an open-tier نظام renders free. The one row here
--          (article #77, a dev period_key) buys access that the open tier now
--          grants for nothing.
--
--     The old row stays where it is -- an accurate record of what was charged
--     against a regulation that no longer exists. Nothing reads it.

commit;

-- ---------------------------------------------------------------------------
-- AFTER THIS MIGRATION, run (in order):
--   python scripts/build_seo_article_index.py --apply --reg cc23fc13-7078-4e54-8543-dc386fa43a74
--   python scripts/build_seo_slugs.py --type regulation --ids-file <the other 5> --apply
--   select public.refresh_search_index('regulation'); select public.refresh_bm25_stats('regulation');
--   select public.refresh_related_axis_weights(); select public.refresh_related_items('regulation');
--   python scripts/generate_sharh.py --reg cc23fc13-7078-4e54-8543-dc386fa43a74
--   ISR purge of /regulations/نظام-العمل + the five مادة paths (REVALIDATE_SECRET)
-- ---------------------------------------------------------------------------
