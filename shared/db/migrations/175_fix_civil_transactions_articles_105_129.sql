-- 175: نظام المعاملات المدنية (17642_reg_070) — repair مواد 105–129.
--
-- The ingest splitter missed four «##### المادة» boundaries after section
-- sub-headings («ثانياً: خيار الشرط\n\n.» …), so the row Article_105 held the text
-- of 105–110 and every following row was numbered 5 too low (row 106 = text of
-- 111 … row 124 = text of 129). 125–129 had no row; 130 onward was correct.
-- Effect: the library showed 124 → 130, and every lookup by number in 106–124
-- (fetch_article, citations, 218 judgment cross_references, incl. 165 for م120)
-- landed on the مادة five numbers later.
--
-- Fix, keyed on article_ref (id = uuid5(DNS, article_ref), so cross_references
-- target_ids stay valid and now point at the right text):
--   1. regulation_v2.articles: rewrite content/chunk_parent_id of 105–124, insert 125–129.
--   2. regulation_v2.chunks.owns.MADDA for the three affected chunks.
--   3. regulation_v2.cross_references.target_chunk_ids for those targets.
--   4. public.seo_articles (derived index) for 105–129.
-- Guarded: a no-op once Article_105 holds a single مادة.

DO $$
DECLARE
  v_reg constant uuid := '1406637f-0f07-440a-b4aa-fb0637539677';
  v_prefix constant text := '17642_reg_070_Article_';
BEGIN
  IF (SELECT count(*) FROM regexp_matches(
        (SELECT content FROM regulation_v2.articles WHERE article_ref = v_prefix || '105'),
        '##### المادة', 'g')) <= 1 THEN
    RAISE NOTICE '175: already applied — skipping';
    RETURN;
  END IF;

  CREATE TEMP TABLE _fix (n int, body text, chunk_id uuid) ON COMMIT DROP;
  INSERT INTO _fix
  WITH split AS (
    SELECT 104 + s.ord::int AS n, rtrim(s.seg, E'\n ') AS body
    FROM regulation_v2.articles a,
         regexp_split_to_table(a.content, '(?=##### المادة)') WITH ORDINALITY s(seg, ord)
    WHERE a.article_ref = v_prefix || '105'
  ), shifted AS (
    SELECT a.article_number::int + 5 AS n, a.content AS body
    FROM regulation_v2.articles a
    WHERE a.regulation_id = v_reg AND a.article_number ~ '^\d+$'
      AND a.article_number::int BETWEEN 106 AND 124
  ), t AS (SELECT * FROM split UNION ALL SELECT * FROM shifted)
  SELECT t.n, t.body,
         (SELECT c.id FROM regulation_v2.chunks c
           WHERE c.regulation_id = v_reg
             AND strpos(c.content, split_part(t.body, E'\n', 1)) > 0
           ORDER BY c.position LIMIT 1) AS chunk_id
  FROM t;

  IF (SELECT count(*) FROM _fix) <> 25
     OR (SELECT count(DISTINCT n) FROM _fix) <> 25
     OR EXISTS (SELECT 1 FROM _fix WHERE chunk_id IS NULL OR n NOT BETWEEN 105 AND 129) THEN
    RAISE EXCEPTION '175: unexpected split shape — aborting';
  END IF;

  -- 1. articles
  UPDATE regulation_v2.articles a
     SET content = f.body, chunk_parent_id = f.chunk_id
    FROM _fix f
   WHERE a.article_ref = v_prefix || f.n AND f.n <= 124;

  INSERT INTO regulation_v2.articles (id, article_ref, chunk_parent_id, regulation_id, article_number, content, ingested_at)
  SELECT uuid_generate_v5(uuid_ns_dns(), v_prefix || f.n), v_prefix || f.n, f.chunk_id, v_reg, f.n::text, f.body, now()
    FROM _fix f WHERE f.n >= 125;

  -- 2. chunks.owns.MADDA: keep numbers outside 105–129, add the repaired ones.
  UPDATE regulation_v2.chunks c
     SET owns = jsonb_set(c.owns, '{MADDA}', (
           SELECT jsonb_agg(x ORDER BY x) FROM (
             SELECT (m)::int AS x FROM jsonb_array_elements_text(c.owns->'MADDA') m
              WHERE (m)::int NOT BETWEEN 105 AND 129
             UNION
             SELECT f.n FROM _fix f WHERE f.chunk_id = c.id) u))
   WHERE c.id IN (SELECT DISTINCT chunk_id FROM _fix)
      OR (c.regulation_id = v_reg AND EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(c.owns->'MADDA') m WHERE (m)::int BETWEEN 105 AND 129));

  -- 3. judgment / chunk citations of these مواد
  UPDATE regulation_v2.cross_references x
     SET target_chunk_ids = jsonb_build_array(f.chunk_id)
    FROM _fix f
   WHERE x.target_id = uuid_generate_v5(uuid_ns_dns(), v_prefix || f.n);

  -- 4. seo_articles (derived; same rules as scripts/build_seo_article_index.py)
  UPDATE public.seo_articles s
     SET article_text = f.body, chunk_id = f.chunk_id,
         extraction_status = 'extracted', updated_at = now()
    FROM _fix f
   WHERE s.regulation_id = v_reg AND s.article_no = f.n;

  INSERT INTO public.seo_articles (id, regulation_id, article_no, article_label, slug, chunk_id, article_text, extraction_status, created_at, updated_at)
  SELECT gen_random_uuid(), v_reg, f.n, 'المادة ' || f.n, 'المادة-' || f.n, f.chunk_id, f.body, 'extracted', now(), now()
    FROM _fix f
   WHERE NOT EXISTS (SELECT 1 FROM public.seo_articles s WHERE s.regulation_id = v_reg AND s.article_no = f.n);
END $$;
