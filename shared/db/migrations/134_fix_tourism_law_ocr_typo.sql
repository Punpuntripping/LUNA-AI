-- 134_fix_tourism_law_ocr_typo.sql — data repair, NOT a schema change.
--
-- Regulation 17399_reg_018 is the Ministry of Tourism's نظام السياحة (source PDF:
-- .../tourism-regulations/Saudi-Tourism-Regulation-Ar-V014.pdf). Its cover-page OCR
-- misread ي as ب and produced "نظام السباحة" (swimming) in every field derived from
-- that page. The BODY of the law was ingested correctly — `scope`, `summary`,
-- `llm_summary`, all chunks/articles/search_topics say السياحة — so this repair is
-- confined to the cover-page-derived metadata. No re-embedding is required:
-- `search_topics` and `regulation_v2.chunk_titles` never carried the typo.
--
-- Scope of the corruption, as measured before the fix:
--   regulations.title             "نظام السباحة"
--   regulations.core_subject      "السباحة"
--   regulations.intro             "# نظام السباحة" + "وزارة السباحة" (cover page only;
--                                 the decree text lower in the same field already
--                                 read "الموافقة على نظام السياحة" correctly)
--   cross_references.target_reg_title   2 rows
--   seo_item_meta.slug            "نظام-السباحة"  ← the public URL key
--   search_index.title/.slug      derived — rebuilt by refresh_search_index below
--
-- A corpus-wide scan for the same signature (a single-word `core_subject` that never
-- appears in the document's own summary/scope) returned 4 rows out of 3,951, and this
-- was the only PUBLISHED one; the other three are benign word-form variations
-- (الصهاريخ/صهاريج, الانارة, servicesguideline). That scan only catches title-noun
-- mismatches, so it is not a complete OCR audit — it is evidence this instance is
-- isolated among slugged items, not proof the corpus is clean.
--
-- ⚠ SLUG CHANGE. This moves the public URL:
--     /regulations/نظام-السباحة  →  /regulations/نظام-السياحة
-- There is no redirect layer, so the old URL 404s by design (decision taken
-- 2026-08-12; the typo'd URL could never rank for «نظام السياحة» anyway).
--
-- ⚠ ISR PURGE IS PART OF THIS REPAIR AND MUST USE PERCENT-ENCODED PATHS.
-- The frontend Full Route Cache keys on the ENCODED pathname. POSTing the raw
-- Arabic path to /api/revalidate returns {"revalidated": true} and purges NOTHING —
-- the old page kept serving byte-identical stale HTML until the encoded form was
-- sent. After applying this migration, purge with:
--     python - <<'PY'
--     import urllib.parse, requests
--     q = urllib.parse.quote
--     for p in (f"/regulations/{q('نظام-السباحة')}", f"/regulations/{q('نظام-السياحة')}",
--               "/regulations", "/sitemaps/regulations", "/sitemaps/articles"):
--         requests.post(f"{WEB}/api/revalidate", json={"path": p},
--                       headers={"x-revalidate-secret": SECRET})
--     PY
--
-- Idempotent: every statement is guarded by a LIKE on the typo, so re-running is a
-- no-op once the repair has landed.

begin;

update regulation_v2.regulations
set title        = replace(title, 'السباحة', 'السياحة'),
    core_subject = replace(core_subject, 'السباحة', 'السياحة'),
    intro        = replace(intro, 'السباحة', 'السياحة')
where reg_ref = '17399_reg_018'
  and (title like '%السباحة%' or core_subject like '%السباحة%' or intro like '%السباحة%');

update regulation_v2.cross_references cr
set target_reg_title = replace(target_reg_title, 'السباحة', 'السياحة')
from regulation_v2.regulations r
where r.reg_ref = '17399_reg_018'
  and cr.target_regulation_id = r.id
  and cr.target_reg_title like '%السباحة%';

update public.seo_item_meta m
set slug = 'نظام-السياحة', updated_at = now()
from regulation_v2.regulations r
where r.reg_ref = '17399_reg_018'
  and m.content_type = 'regulation'
  and m.content_id = r.id::text
  and m.slug = 'نظام-السباحة';

commit;

-- Rebuild the derived BM25 layer from the corrected source. This DELETEs and
-- reinserts every regulation row in search_index (1,188 rows) and then calls
-- refresh_bm25_stats('regulation') internally — it is the only supported way to
-- propagate a title or slug edit into search. Verified post-fix: bm25_search()
-- ranks نظام السياحة first at 1007.78 (exact-title bonus), which the typo'd row
-- could never earn for this query.
select public.refresh_search_index('regulation');
