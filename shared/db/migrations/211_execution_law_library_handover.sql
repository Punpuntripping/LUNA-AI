-- 211_execution_law_library_handover.sql
--
-- Put the NEW نظام التنفيذ and its NEW لائحة into the public library, on the
-- URLs and first-page ranks the old pair held, and keep the old pair published
-- on its own URLs. Owner, 2026-11-03: «ingest نظام التنفيذ ولائحته الجديدة into
-- our library, do not delete the old one, move it to the first page».
--
-- THE FOUR ROWS (all in regulation_v2.regulations)
--   OLD نظام   17486_reg_008  a49cc765-…  «نظام التنفيذ (القديم)»          cancelled
--   OLD لائحة  17642_reg_026  98f66bdf-…  «اللائحة التنفيذية لنظام التنفيذ (القديم)» cancelled
--   NEW نظام   6000_reg_0002  287fcb1a-…  «نظام التنفيذ»  م/237, in force 2026-10-28
--   NEW لائحة  6000_reg_0001  267cef23-…  «اللائحة التنفيذية لنظام التنفيذ»  قرار وزاري 617
--
-- Before: old نظام held slug `نظام-التنفيذ` (rank 5, seo_tier open, indexable)
-- and old لائحة held `اللائحة-التنفيذية-لنظام-التنفيذ` (rank 7, indexable). The
-- new rows had sidecar rows with no slug (unpublished).
--
-- §2 OLD pair moves to the slug of its own display title (build_seo_slugs.py
--    `slugify_ar` of clean_title), rank NULL (end of the hub), seo_tier and
--    indexable KEPT. Still published, still reachable, not deleted. Its
--    seo_articles / seo_sharh / library_items stay on the old uuid: they
--    describe the old text.
-- §3 NEW pair takes the freed slugs, the old pair's rank and seo_tier, and
--    indexable = true (migration 210 defaults new rows to false; the URL was
--    already in Google and must stay there).
-- §4 «اقرأ تاليًا» edges new → old for each pair. `amendment` is the only
--    relation in document_relations_relation_chk that fits a replacement;
--    nothing renders the label (related_items only scores the edge). Evidence:
--    new نظام مادة 65 «يحل النظام محل نظام التنفيذ، الصادر بالمرسوم الملكي رقم
--    (م/53)…»; for the لائحة, the superseded_by pair from migration 199.
--    `one_way` (base 3.5) keeps each law's own لائحة (executive_regulation,
--    `both`, base 5.0) above the other version.
--
-- Article pages: none of the four has a published مادة page
-- (content_type='article' rows: 0), so nothing 404s or needs a redirect.
--
-- Idempotent: guarded archive, value assignments, ON CONFLICT DO NOTHING.
--
-- Rollback: restore the §1 archive rows (slug/rank/seo_tier/indexable on all
-- four, NEW slugs cleared FIRST for the unique index), delete the two §4 edges,
-- then the AFTER steps.
--
-- AFTER: build_seo_article_index.py --apply --reg <each new uuid>,
--        refresh_search_index('regulation') + refresh_bm25_stats('regulation'),
--        refresh_related_axis_weights() -> refresh_related_items('regulation'),
--        ISR purge (percent-encoded) of all four reg pages, /regulations,
--        /sitemaps/regulations.

begin;

-- ---------------------------------------------------------------------------
-- §0  Preconditions: either not yet applied, or already applied. Anything
--     else means someone moved these rows since 2026-11-03; stop.
-- ---------------------------------------------------------------------------
do $$
declare
  v_old_law  text := (select slug from public.seo_item_meta
                       where content_type = 'regulation'
                         and content_id = 'a49cc765-8023-4d21-88dd-27b6e720efe8');
  v_old_reg  text := (select slug from public.seo_item_meta
                       where content_type = 'regulation'
                         and content_id = '98f66bdf-87ec-40c9-adf2-0b3ab0cc2bc8');
begin
  if v_old_law is distinct from 'نظام-التنفيذ'
     and v_old_law is distinct from 'نظام-التنفيذ-القديم' then
    raise exception '211: old نظام التنفيذ slug is %, expected نظام-التنفيذ', v_old_law;
  end if;
  if v_old_reg is distinct from 'اللائحة-التنفيذية-لنظام-التنفيذ'
     and v_old_reg is distinct from 'اللائحة-التنفيذية-لنظام-التنفيذ-القديم' then
    raise exception '211: old لائحة slug is %, expected اللائحة-التنفيذية-لنظام-التنفيذ', v_old_reg;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- §1  Archive the four sidecar rows (once)
-- ---------------------------------------------------------------------------
insert into regulation_v2.library_surface_archive_20260829 (reg_ref, reg_uuid, source_table, row_data)
select v.reg_ref, v.reg_uuid, 'seo_item_meta@211', to_jsonb(m)
  from (values
    ('17486_reg_008', 'a49cc765-8023-4d21-88dd-27b6e720efe8'),
    ('17642_reg_026', '98f66bdf-87ec-40c9-adf2-0b3ab0cc2bc8'),
    ('6000_reg_0002', '287fcb1a-304b-4ec3-94a3-14e86e1ac404'),
    ('6000_reg_0001', '267cef23-cef6-4090-8f61-d31c646eebbb')
  ) v(reg_ref, reg_uuid)
  join public.seo_item_meta m
    on m.content_type = 'regulation' and m.content_id = v.reg_uuid
 where not exists (
   select 1 from regulation_v2.library_surface_archive_20260829
    where source_table = 'seo_item_meta@211'
 );

-- ---------------------------------------------------------------------------
-- §3a  New rows take rank / tier from the old rows (before §2 clears rank).
--      Guarded on the new row still being unpublished, so a re-run is a no-op.
-- ---------------------------------------------------------------------------
update public.seo_item_meta n
   set rank       = o.rank,
       seo_tier   = o.seo_tier,
       indexable  = true,
       updated_at = now()
  from public.seo_item_meta o
 where n.content_type = 'regulation' and o.content_type = 'regulation'
   and (n.content_id, o.content_id) in (
         ('287fcb1a-304b-4ec3-94a3-14e86e1ac404', 'a49cc765-8023-4d21-88dd-27b6e720efe8'),
         ('267cef23-cef6-4090-8f61-d31c646eebbb', '98f66bdf-87ec-40c9-adf2-0b3ab0cc2bc8'))
   and n.slug is null
   and o.rank is not null;

-- ---------------------------------------------------------------------------
-- §2  Old pair: own slug, end of the hub, everything else kept
-- ---------------------------------------------------------------------------
update public.seo_item_meta
   set slug = 'نظام-التنفيذ-القديم', rank = null, updated_at = now()
 where content_type = 'regulation'
   and content_id = 'a49cc765-8023-4d21-88dd-27b6e720efe8';

update public.seo_item_meta
   set slug = 'اللائحة-التنفيذية-لنظام-التنفيذ-القديم', rank = null, updated_at = now()
 where content_type = 'regulation'
   and content_id = '98f66bdf-87ec-40c9-adf2-0b3ab0cc2bc8';

-- ---------------------------------------------------------------------------
-- §3b  New pair takes the freed slugs
-- ---------------------------------------------------------------------------
update public.seo_item_meta
   set slug = 'نظام-التنفيذ', updated_at = now()
 where content_type = 'regulation'
   and content_id = '287fcb1a-304b-4ec3-94a3-14e86e1ac404';

update public.seo_item_meta
   set slug = 'اللائحة-التنفيذية-لنظام-التنفيذ', updated_at = now()
 where content_type = 'regulation'
   and content_id = '267cef23-cef6-4090-8f61-d31c646eebbb';

-- ---------------------------------------------------------------------------
-- §4  «اقرأ تاليًا»: each new version ↔ its old version
-- ---------------------------------------------------------------------------
insert into regulation_v2.document_relations
  (id, source_id, source_ref, target_id, target_ref, relation, agreement, evidence, ingested_at)
select
  uuid_generate_v5(uuid_ns_dns(), e.src || '|amendment|' || e.tgt),
  s.id, e.src, t.id, e.tgt, 'amendment', 'one_way', e.evidence, now()
from (values
  ('6000_reg_0002', '17486_reg_008',
   'migration 211 — مادة 65 من النظام الجديد: «يحل النظام محل نظام التنفيذ، الصادر بالمرسوم الملكي رقم (م/53)»'),
  ('6000_reg_0001', '17642_reg_026',
   'migration 211 — اللائحة الجديدة تحل محل القديمة (superseded_by، migration 199)')
) e(src, tgt, evidence)
join regulation_v2.regulations s on s.reg_ref = e.src
join regulation_v2.regulations t on t.reg_ref = e.tgt
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- §5  Self-test
-- ---------------------------------------------------------------------------
do $$
declare
  v_bad int;
begin
  select count(*) into v_bad
    from (values
      ('287fcb1a-304b-4ec3-94a3-14e86e1ac404', 'نظام-التنفيذ'),
      ('267cef23-cef6-4090-8f61-d31c646eebbb', 'اللائحة-التنفيذية-لنظام-التنفيذ'),
      ('a49cc765-8023-4d21-88dd-27b6e720efe8', 'نظام-التنفيذ-القديم'),
      ('98f66bdf-87ec-40c9-adf2-0b3ab0cc2bc8', 'اللائحة-التنفيذية-لنظام-التنفيذ-القديم')
    ) x(id, slug)
    left join public.seo_item_meta m
      on m.content_type = 'regulation' and m.content_id = x.id and m.slug = x.slug
   where m.content_id is null;
  if v_bad > 0 then
    raise exception '211 self-test: % slug(s) not where expected', v_bad;
  end if;

  if (select rank from public.seo_item_meta
       where content_type = 'regulation'
         and content_id = '287fcb1a-304b-4ec3-94a3-14e86e1ac404') is null then
    raise exception '211 self-test: new نظام التنفيذ has no rank';
  end if;
end $$;

commit;
