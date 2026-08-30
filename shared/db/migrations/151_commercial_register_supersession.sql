-- 151_commercial_register_supersession.sql
--
-- Retire the repealed نظام السجل التجاري (م/١، ١٤١٦هـ) and promote the current
-- one (م/٨٣، ١٤٤٦هـ) onto its URL.
--
-- THE TWO ROWS
-- ------------
--   KEEP  `17606_reg_001_p3`  d1da8216-…  «قرار مجلس الوزراء والمرسوم الملكي - نظام السجل التجاري»
--         authority_basis: قرار مجلس الوزراء (٢٣٧) ١٤/٣/١٤٤٦هـ + المرسوم الملكي (م/٨٣) ١٩/٣/١٤٤٦هـ
--         29 مادة · 6 chunks (الفصول الأولى→السادسة) · rank 23 · authority_score 10
--   DROP  `17606_reg_006`     783bdb68-…  «نظام السجل التجاري»
--         authority_basis: «نظام صادر بمرسوم ملكي» (no decree cited)
--         20 مادة · rank 30 · seo_tier='open' · slug 'نظام-السجل-التجاري'
--
-- The supersession is stated IN THE CORPUS, by the keeper's own closing article:
--   المادة التاسعة والعشرون: نفاذ النظام — «يحل النظام محل نظام السجل التجاري،
--   الصادر بالمرسوم الملكي رقم (م/١) وتاريخ ١٤١٦/٢/٢١هـ»
-- and corroborated by the in-force لائحة (`17606_reg_001`), whose own
-- authority_basis names «نظام السجل التجاري الصادر بالمرسوم الملكي رقم (م/٨٣)»
-- — i.e. the keeper, not the row it is currently edged to.
--
-- ⚠ BOTH ROWS WERE `status_class='in_force'`. The repealed 1416هـ law was being
-- served to readers AND to the agents as current law. That is the same defect
-- class as migration 149, reached by a different route: there, a retirement was
-- performed and the library was not told; here, no retirement was ever
-- performed at all.
--
-- WHAT THIS DOES
-- --------------
-- §2 RETIRES the old law rather than deleting it: `status_class='cancelled'`
--    (so `shared/library/reg_status.py` flags it «ملغي — لم يعد سارياً» to the
--    aggregator and the compliance reranker) and clears its slug (so the page
--    404s and it leaves the hub, the sitemap and BM25). The corpus row STAYS —
--    15 workspace_item_references cite its chunks, and migration 149 established
--    that deleting the rows behind a citation only turns it into a dead stub.
--    A reader asking about the old register still gets a correctly-flagged
--    answer instead of silence.
--
-- §3 PROMOTES the keeper onto the freed slug and renames it.
--    `clean_title` is the display column — `library_service` renders
--    `coalesce(clean_title, title)` at :2668/:3804/:4661 — and is NULL on 43% of
--    the corpus, so setting it is the intended, minimal lever for a rename. The
--    raw `title` is left untouched as the provenance record of the source
--    document.
--    `seo_tier='open'` transfers with the slug: the repealed law's مواد read
--    free, and its replacement should not read LESS freely than the text it
--    supersedes.
--
-- ⚠ THE KEEPER'S OLD URL WILL 404. `build_seo_slugs.py` states the invariant
--    ("Slugs are PERMANENT — URLs must not change once published") and there is
--    no slug-alias or redirect layer in this repo (next.config `redirects()`
--    only canonicalises www→apex). `/regulations/قرار-مجلس-الوزراء-والمرسوم-الملكي-نظام-السجل-التجاري`
--    is live, indexed and ranked 23. Renaming was explicitly requested; the
--    301 that would preserve its equity needs a next.config entry + a frontend
--    deploy and is NOT in this migration.
--
-- §4 REPAIRS a content bleed on the article being promoted. `articles.content`
--    for مادة 29 is 12,729 chars of which 12,527 (98%) is the whole of
--    «نظام الأسماء التجارية», swept in from the next law in the source file. The
--    CHUNKS ARE CLEAN (chunk 6 is 659 chars and ends correctly), so this is an
--    articles-only extraction defect. Left alone it would render 12.5k chars of
--    a DIFFERENT LAW under «المادة 29» on a page about to become the flagship
--    السجل التجاري URL, at `seo_tier='open'` (i.e. free and indexable).
--    The genuine article is chars 1..198; the bleed starts at char 200 with
--    «# نظام الأسماء التجارية». Trimmed content is archived first.
--    (A sibling case exists — `17606_reg_003` مادة 255, 13,707 chars — NOT
--    touched here; see the report §6.)
--
-- §5 EDGES the in-force لائحة to the law it is actually issued under. Today
--    `17606_reg_001 --executive_regulation--> 17606_reg_006` points at the
--    REPEALED law. The historical edge is kept (migration 150's rule: add,
--    never move) and the correct one added alongside.
--
-- Idempotent: archive guarded on emptiness, updates are value-assignments,
-- the insert is ON CONFLICT (id) DO NOTHING on a content-derived uuid.
--
-- AFTER: refresh_search_index + refresh_bm25_stats ('regulation'),
--        refresh_related_axis_weights() -> refresh_related_items('regulation'),
--        build_seo_article_index.py --apply --reg d1da8216-…  (picks up §4),
--        generate_sharh.py --apply --reg d1da8216-…            (new open tier),
--        ISR purge of both slugs + /regulations + the two sitemaps.

begin;

-- ---------------------------------------------------------------------------
-- §1  Archive everything this migration rewrites
-- ---------------------------------------------------------------------------

insert into regulation_v2.library_surface_archive_20260829 (reg_ref, reg_uuid, source_table, row_data)
select v.reg_ref, v.reg_uuid, v.src, v.row_data
from (
  select '17606_reg_006' as reg_ref, '783bdb68-6f7a-4e97-bc35-8139b6a9d800' as reg_uuid,
         'seo_item_meta' as src, to_jsonb(m) as row_data
    from public.seo_item_meta m
   where m.content_type = 'regulation' and m.content_id = '783bdb68-6f7a-4e97-bc35-8139b6a9d800'
  union all
  select '17606_reg_006', '783bdb68-6f7a-4e97-bc35-8139b6a9d800', 'regulations', to_jsonb(r) - 'summary_embedding'
    from regulation_v2.regulations r where r.reg_ref = '17606_reg_006'
  union all
  select '17606_reg_006', '783bdb68-6f7a-4e97-bc35-8139b6a9d800', 'search_index', to_jsonb(si)
    from public.search_index si where si.content_id = '783bdb68-6f7a-4e97-bc35-8139b6a9d800'
  union all
  select '17606_reg_006', '783bdb68-6f7a-4e97-bc35-8139b6a9d800', 'library_items', to_jsonb(li)
    from public.library_items li where li.content_id = '783bdb68-6f7a-4e97-bc35-8139b6a9d800'
  union all
  select '17606_reg_001_p3', 'd1da8216-a1b6-496d-9180-ea9c6b7fa433', 'seo_item_meta', to_jsonb(m)
    from public.seo_item_meta m
   where m.content_type = 'regulation' and m.content_id = 'd1da8216-a1b6-496d-9180-ea9c6b7fa433'
  union all
  select '17606_reg_001_p3', 'd1da8216-a1b6-496d-9180-ea9c6b7fa433', 'articles_bleed', to_jsonb(a)
    from regulation_v2.articles a
    join regulation_v2.regulations r on r.id = a.regulation_id
   where r.reg_ref = '17606_reg_001_p3' and a.article_number::text = '29'
) v
where not exists (
  select 1 from regulation_v2.library_surface_archive_20260829
   where reg_uuid = '783bdb68-6f7a-4e97-bc35-8139b6a9d800'
);

-- ---------------------------------------------------------------------------
-- §2  Retire the repealed 1416هـ law
-- ---------------------------------------------------------------------------

-- Agent-facing: is_repealed() switches on exactly this value.
update regulation_v2.regulations
   set status_class = 'cancelled',
       status_raw   = coalesce(status_raw, 'ملغي')
 where reg_ref = '17606_reg_006';

-- Reader-facing: clearing the slug unpublishes it (hub, sitemap, BM25, page).
-- The sidecar row and its seo_tier survive, exactly as build_seo_slugs.py
-- --unpublish would leave them, so a re-publish restores gating.
update public.seo_item_meta
   set slug = null, updated_at = now()
 where content_type = 'regulation'
   and content_id = '783bdb68-6f7a-4e97-bc35-8139b6a9d800';

delete from public.search_index
 where content_id = '783bdb68-6f7a-4e97-bc35-8139b6a9d800';

-- Two readers had it on their مكتبتي shelf; move them to the law in force.
update public.library_items li
   set content_id = 'd1da8216-a1b6-496d-9180-ea9c6b7fa433'
 where li.content_id = '783bdb68-6f7a-4e97-bc35-8139b6a9d800'
   and not exists (
     select 1 from public.library_items x
      where x.user_id = li.user_id
        and x.content_type = li.content_type
        and x.content_id = 'd1da8216-a1b6-496d-9180-ea9c6b7fa433'
   );

-- ---------------------------------------------------------------------------
-- §3  Promote + rename the current law
-- ---------------------------------------------------------------------------

update regulation_v2.regulations
   set clean_title = 'نظام السجل التجاري'
 where reg_ref = '17606_reg_001_p3';

update public.seo_item_meta
   set slug = 'نظام-السجل-التجاري',
       seo_tier = 'open',
       updated_at = now()
 where content_type = 'regulation'
   and content_id = 'd1da8216-a1b6-496d-9180-ea9c6b7fa433';

-- ---------------------------------------------------------------------------
-- §4  Repair the مادة 29 content bleed (articles only; chunks are clean)
-- ---------------------------------------------------------------------------

update regulation_v2.articles a
   set content = btrim(substring(a.content from 1 for 198))
  from regulation_v2.regulations r
 where r.id = a.regulation_id
   and r.reg_ref = '17606_reg_001_p3'
   and a.article_number::text = '29'
   and position('# نظام الأسماء التجارية' in a.content) > 0;

-- ---------------------------------------------------------------------------
-- §5  Edge the لائحة to the law it is issued under
-- ---------------------------------------------------------------------------

insert into regulation_v2.document_relations
  (id, source_id, source_ref, target_id, target_ref, relation, agreement, evidence, ingested_at)
select
  uuid_generate_v5(uuid_ns_dns(), '17606_reg_001|executive_regulation|17606_reg_001_p3'),
  s.id, '17606_reg_001', t.id, '17606_reg_001_p3',
  'executive_regulation', 'both',
  'migration 151 — the لائحة''s own authority_basis names المرسوم الملكي (م/٨٣) ١٤٤٦هـ, '
    || 'i.e. 17606_reg_001_p3; its existing edge onto the repealed 17606_reg_006 is retained',
  now()
from regulation_v2.regulations s, regulation_v2.regulations t
where s.reg_ref = '17606_reg_001' and t.reg_ref = '17606_reg_001_p3'
on conflict (id) do nothing;

commit;
