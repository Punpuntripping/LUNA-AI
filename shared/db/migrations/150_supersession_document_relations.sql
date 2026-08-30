-- 150_supersession_document_relations.sql
--
-- Reconnect the executive regulations of the six re-ingested laws to the copy
-- that is actually in force.
--
-- WHY
-- ---
-- `regulation_v2.document_relations` is the typed law<->لائحة graph. It is a
-- CORPUS-INGEST output, and the six laws re-ingested on 2026-08-29 arrived with
-- ZERO rows in it -- so every one of them is an orphan in the document graph:
--
--   * `public.related_reg_document_edges` (the view `refresh_related_items()`
--     reads for its BASE score, 143 §3.3) is built straight off this table, so a
--     law with no rows here can only earn "bonus_only" edges from the entity /
--     sector generators. نظام العمل clears NEITHER generator -- entity 17900 has
--     150 أنظمة (low scarcity weight) and its sectors العمل والتوظيف /
--     القضاء والمحاكم are common -- so after publishing it had 0 «اقرأ تاليًا»
--     rows in both directions while the other five picked up 3-28 each.
--   * اللائحة التنفيذية لنظام المنافسة (17687_reg_507) and its parent نظام
--     المنافسة (17687_reg_506) were ingested in the SAME batch and still had no
--     edge joining them to each other.
--
-- Separately, eight edges from real, in-force لوائح still name the REPEALED
-- copies as their parent law (the pipeline wrote them before the re-ingest):
--   -> 17573_reg_093 (نظام الكهرباء 1426هـ):  17591_reg_001 · 5000_regulation_0212
--                                            5000_regulation_0928 · 5000_regulation_3989
--   -> 17573_reg_264 (الرهن التجاري 1424هـ):  5000_regulation_2419
--   -> 17573_reg_262 (مكافحة التستر 1425هـ):  5000_regulation_3243 · 5000_regulation_3257
--   -> 17573_reg_263 (المنافسة 1425هـ):       17645_reg_التشريعات_001   <-- SEE BELOW
--
-- WHAT THIS DOES: ADDS, NEVER MOVES
-- ---------------------------------
-- The historical edges are LEFT IN PLACE and a parallel edge to the current law
-- is inserted alongside. Both statements are true at once -- the لائحة WAS
-- issued under the repealed law, and it IS the operative لائحة of the law now in
-- force (a Saudi executive regulation survives the replacement of its parent
-- statute until it is itself replaced). Rewriting the old edge would erase the
-- first fact to assert the second; `refresh_related_items()` takes `max()` over
-- duplicate edges (143 §3.3), so carrying both costs nothing and the repealed
-- copies are unpublished, so their edges surface to no reader.
--
-- ⚠ ONE EDGE IS DELIBERATELY NOT MIRRORED
--   `17645_reg_التشريعات_001` -- «الائحة التنفيذية لنظام المنافسات» -- is the
--   executive regulation of نظام المنافسات والمشتريات الحكومية (government
--   PROCUREMENT). It is not a لائحة of نظام المنافسة (COMPETITION) at all; the
--   existing edge onto 17573_reg_263 is a pre-existing pipeline mismatch on the
--   المنافسة/المنافسات near-homograph. Mirroring it onto 17687_reg_506 would
--   propagate that error onto a published page, so it is left alone and
--   reported instead. Do not "fix" this by adding the mirror.
--
-- ⚠ ALSO NOT INCLUDED: `17900_reg_521` («اللائحة التنفيذية لنظام العمل بشأن
--   تقسيم رخص وتأشيرات العمل») is `status_class='consultation_ended'` -- a draft
--   that never took effect. Only in-force لوائح are wired here.
--
-- `agreement` is carried from the historical edge where one exists ('both' for
-- the two whose titles name their law verbatim, 'one_way' otherwise) and is
-- 'both' for the two new pairs, whose titles are verbatim
-- («اللائحة التنفيذية لنظام المنافسة» -> «نظام المنافسة»).
--
-- `id` follows the table's convention, verified against three live rows:
--     uuid_generate_v5(uuid_ns_dns(), source_ref || '|' || relation || '|' || target_ref)
--
-- Idempotent: ON CONFLICT (id) DO NOTHING; the ids are content-derived.
--
-- AFTER THIS MIGRATION: re-run the read-next graph, which reads this table --
--     select public.refresh_related_axis_weights();
--     select public.refresh_related_items('regulation');

begin;

insert into regulation_v2.document_relations
  (id, source_id, source_ref, target_id, target_ref, relation, agreement, evidence, ingested_at)
select
  uuid_generate_v5(uuid_ns_dns(), v.source_ref || '|executive_regulation|' || v.target_ref),
  s.id, v.source_ref,
  t.id, v.target_ref,
  'executive_regulation',
  v.agreement,
  'migration 150 — reconnected to the in-force copy after the 2026-08-29 '
    || 'supersession re-ingest; historical edge onto the repealed law retained',
  now()
from (values
  -- لوائح of نظام الكهرباء  (repealed parent 17573_reg_093 -> 17591_reg_003)
  ('17591_reg_001',        '17591_reg_003', 'both'),
  ('5000_regulation_0212', '17591_reg_003', 'one_way'),
  ('5000_regulation_0928', '17591_reg_003', 'one_way'),
  ('5000_regulation_3989', '17591_reg_003', 'one_way'),
  -- لائحة of نظام الرهن التجاري  (17573_reg_264 -> 17606_reg_531)
  ('5000_regulation_2419', '17606_reg_531', 'one_way'),
  -- لوائح of نظام مكافحة التستر  (17573_reg_262 -> 17606_reg_532)
  ('5000_regulation_3243', '17606_reg_532', 'one_way'),
  ('5000_regulation_3257', '17606_reg_532', 'both'),
  -- NEW pairs: never had an edge at all
  ('17687_reg_507',        '17687_reg_506', 'both'),
  ('17900_reg_128_p2',     '17900_reg_549', 'both')
) as v(source_ref, target_ref, agreement)
join regulation_v2.regulations s on s.reg_ref = v.source_ref
join regulation_v2.regulations t on t.reg_ref = v.target_ref
on conflict (id) do nothing;

commit;
