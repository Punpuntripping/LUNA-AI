-- ============================================================================
-- 144_compliance_search_corpus.sql — the 337 service guides join `search_index`
--                                     as a new `compliance` corpus (2026-08-22)
--
-- Plan: .claude/plans/compliance_entity_sections.md
--       §1 verified live state · §6.1 this file · §6.2 the image-hole strip ·
--       §6.3/§6.4 the backend + frontend halves that follow it · §7 rollout
-- Depends on: 111 (search_index, search_index_fill_trg, refresh_bm25_stats,
--             refresh_search_index, the guarded pg_cron block — the house style
--             for all four), 112 (the CURRENT refresh_search_index: entity_text
--             at weight B; this file is 112's body verbatim + one branch),
--             142 (public.library_compliance_v; seo key = content_type
--             'compliance', content_id = service_guides.id::text).
--
-- ── WHAT CHANGES, AND WHY ──────────────────────────────────────────────────
-- /compliance is the one wing with no row in the navigation index, so the
-- cross-wing SearchBar cannot reach a single guide and `ComplianceHubView`
-- carries a "NO SEARCH PANEL, deliberately" comment whose own text says to add
-- the panel back "with the corpus". This file is that corpus. Four statements,
-- nothing else:
--   1. `search_index_corpus_check` gains 'compliance' (six values preserved).
--   2. `refresh_search_index(p_corpus)` gains an `elsif 'compliance'` branch.
--   3. the nightly `bm25_refresh_nightly` cron job refreshes it too.
--   4. a backfill — expect 337 (plan §1: 337 guides, every one published).
--
-- ── WHY THE WHOLE `guide_md` IS SAFE AS THE `lead` ─────────────────────────
-- 111's header rule still governs: weight D may only ever hold ALWAYS-FREE
-- text, because the index is probe-reachable via ?q= even though it renders
-- nothing. The guides clear that bar completely rather than by a computed
-- floor: the wing is 100% published and ungated end to end — 'compliance' is
-- deliberately absent from the gate map (library_service.py:839) and
-- `get_compliance_guide` resolves no gate and charges nothing. So unlike
-- `circular`, whose lead is the guaranteed-free 400-char floor of a gated
-- column, there is no partial-content problem to solve here. The guides are
-- also OURS — our own authored rewrite of each entity's PDF — which is what
-- let the wing be ungated in the first place (142's header).
--
-- ⚠ THE IMAGE-HOLE TOKENS MUST BE STRIPPED BEFORE THEY ARE INDEXED (§6.2).
-- 324 of the 337 guides carry lines that are nothing but a bare `\d+_\d+`
-- token — the screenshot placeholders the RENDERER swaps for the matching
-- `service_guide_images` row by image_ref (142's header · the ingestion
-- REFERENCE.md §3–§4). Verified live 2026-08-22: the regexp below removes
-- 46,525 characters across those 324 guides. This is not cosmetic:
--   • the tokens lex as searchable numbers, so `12_3` becomes a term a query
--     can hit on a "document" in which the reader can never see that string;
--   • `search_index_fill_trg` sets `doc_len` to the TOTAL token count, so
--     69 unstripped holes make the most heavily-illustrated guide — the one
--     with the most screenshots and therefore the most help to give — look
--     long and thin to BM25's length normalization, and rank it DOWN for it.
--
-- NOT a snippet concern, despite how it looks: `SearchHit` carries no snippet
-- and no `lead` (bm25_navigation_search.md §5.3 deleted `ts_headline` outright)
-- — every card renders its own excerpt from its own always-free column, and
-- ComplianceHubItem.summary is what /compliance renders. `lead` is recall
-- weight and nothing else.
--
-- ── WHY `summary` IS NOT CONCATENATED INTO THE LEAD ────────────────────────
-- It would add zero vocabulary. Measured 2026-08-22: `summary` is a VERBATIM
-- substring of `guide_md` on 337 of 337 guides, so indexing it beside the body
-- would only double-count the terms the abstract happens to repeat and tilt
-- BM25 toward them. If a future ingest ever authors summaries independently of
-- the body, re-run that check and revisit this line — do not assume it holds.
-- ⚠ The `'gn'` flags are load-bearing. `n` is what turns `^`/`$` into LINE
--   anchors; without it the pattern anchors to the whole body and matches
--   nothing at all, silently. Same regex as the renderer's — one source of
--   truth, quoted in the branch comment. In-sentence «الصورة {n}» is NOT
--   touched and must not be.
--
-- ⚠ `refresh_search_index` IS RE-CREATED WHOLE, NOT PATCHED. `create or
--   replace function` replaces the entire body, so every branch below is 112
--   §4 reproduced verbatim — a branch dropped by accident here is a corpus
--   that silently stops refreshing and starts serving stale vectors. Diff this
--   function against 112 before applying; only the `compliance` branch differs.
--
-- ⚠ Do NOT convert `corpus` to an enum while adding a value to it. Same rule
--   142 §3.0 records for this wing's `content_type` columns: an enum makes
--   every future vocabulary addition a type-level migration with a rewrite,
--   and the array below is APPENDED to, never re-ordered or "tidied" — a value
--   silently removed becomes a nightly refresh that raises on a corpus nobody
--   is currently looking at. The six existing values are verbatim from the
--   live constraint definition (queried 2026-08-22).
--
-- ⚠ `search_doc` and `doc_len` are NOT inserted. `search_index_fill_trg` is a
--   BEFORE INSERT trigger and builds both (111 §4, 112 §2). Writing them here
--   would be overwritten at best and would drift at worst.
--
-- Idempotent and safe to re-run end to end: the CHECK is dropped-if-exists and
-- re-added rather than mutated, the function is `create or replace`, the cron
-- job is unscheduled-if-present then re-scheduled (111 §11's pattern), and the
-- backfill is a delete+insert of one corpus.
--
-- ⚠ NOT AUTO-APPLIED. Files in this directory are run by hand (Supabase SQL
-- editor / MCP `apply_migration`). APPLY THIS FILE AS ONE SCRIPT, and apply it
-- BEFORE the backend that puts 'compliance' in `PUBLIC_CORPORA` deploys (§6.3)
-- — migration precedes deploy, always.
-- ============================================================================

-- ── 1. The corpus vocabulary ────────────────────────────────────────────────
-- Six existing values preserved exactly, 'compliance' appended. Without this,
-- the branch in §2 raises 23514 on its first INSERT and the wing has no index.

alter table public.search_index
  drop constraint if exists search_index_corpus_check;

alter table public.search_index
  add constraint search_index_corpus_check
  check (corpus = any (array[
    'regulation'::text, 'judgment'::text, 'circular'::text,
    'service'::text, 'blog'::text, 'template'::text,
    'compliance'::text
  ]));

-- ── 2. refresh_search_index: 112 §4 verbatim + the `compliance` branch ──────
-- ⚠ Everything except the `elsif p_corpus = 'compliance'` block is an exact
--   copy of migration 112. Do not edit it here; edit it in a new migration
--   that likewise reproduces the whole function.
--
-- ⚠ `service` is NOT this corpus and is deliberately left exactly as it is.
--   It is 100 rows keyed by `services.id` carrying the RETIRED wing's Arabic
--   slugs — out of `PUBLIC_CORPORA`, with no URL prefix, unranked by any
--   navigation surface since 2026-08-03, kept alive only for
--   `manual_search.py`'s rung-③ exact-title pin. The government-services
--   NAVIGATION corpus is the new `compliance` one. Retiring the `service` rows
--   is a separate decision (plan §10).

create or replace function public.refresh_search_index(p_corpus text)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer := 0;
begin
  if p_corpus = 'regulation' then
    delete from public.search_index where corpus = 'regulation';
    insert into public.search_index (corpus, content_id, slug, title, entity_text, facets_text, lead, facets)
    select 'regulation', r.id::text, m.slug,
           coalesce(nullif(trim(r.clean_title), ''), r.title, ''),
           coalesce(r.entity_name, ''),
           concat_ws(' ', r.doc_type_bucket, r.status_class, r.reg_ref,
                     array_to_string(r.sectors, ' ')),
           coalesce(nullif(trim(r.llm_summary), ''), r.summary, ''),
           jsonb_strip_nulls(jsonb_build_object(
             'entity_name', r.entity_name, 'doc_type_bucket', r.doc_type_bucket,
             'status_class', r.status_class, 'reg_ref', r.reg_ref,
             'sectors', to_jsonb(coalesce(r.sectors, array[]::text[]))))
    from regulation_v2.regulations r
    join public.seo_item_meta m
      on m.content_type = 'regulation' and m.content_id = r.id::text
     and m.slug is not null;

  elsif p_corpus = 'judgment' then
    delete from public.search_index where corpus = 'judgment';
    insert into public.search_index (corpus, content_id, slug, title, entity_text, facets_text, lead, facets)
    select 'judgment', c.id::text, m.slug,
           public.luna_judgment_title(c.short_summary, c.summary, c.facts,
                                      c.ruling, c.court, c.date_hijri),
           -- A judgment's issuing body is its court. Court LEVEL stays in
           -- facets: «استئناف» is a stage, not an issuer.
           coalesce(c.court, ''),
           concat_ws(' ', c.court_level, c.city, c.case_number,
                     c.judgment_number, array_to_string(c.legal_domains, ' ')),
           coalesce(c.short_summary, ''),
           jsonb_strip_nulls(jsonb_build_object(
             'court', c.court, 'court_level', c.court_level, 'city', c.city,
             'case_number', c.case_number,
             'legal_domains', to_jsonb(coalesce(c.legal_domains, array[]::text[]))))
    from public.cases c
    join public.seo_item_meta m
      on m.content_type = 'judgment' and m.content_id = c.id::text
     and m.slug is not null;

  elsif p_corpus = 'circular' then
    delete from public.search_index where corpus = 'circular';
    insert into public.search_index (corpus, content_id, slug, title, entity_text, facets_text, lead, facets)
    select 'circular', ci.id::text, m.slug,
           coalesce(ci.title, ''),
           -- THE FIX. `circulars.entity_ref` is a numeric source token; the
           -- readable name is only in `entities`. 100/100 slugged circulars
           -- resolve. LEFT JOIN so an unmatched entity never drops the circular
           -- out of the index — it just loses its issuer field.
           coalesce(e.entity_name, ''),
           concat_ws(' ', ci.doc_type, ci.circ_ref,
                     array_to_string(ci.sectors, ' ')),
           -- Unchanged from 111: the guaranteed-free 400-char floor with the
           -- trailing partial word stripped. See 111's header — this is the one
           -- corpus whose lead is a gated column.
           regexp_replace(left(coalesce(ci.content, ''), 400), '\S*$', ''),
           -- entity_name added here too: 111 gave circular cards NO printable
           -- meta line, because every other circular facet is a ref token or a
           -- raw enum.
           jsonb_strip_nulls(jsonb_build_object(
             'entity_name', e.entity_name,
             'doc_type', ci.doc_type, 'circ_ref', ci.circ_ref,
             'sectors', to_jsonb(coalesce(ci.sectors, array[]::text[]))))
    from public.circulars ci
    join public.seo_item_meta m
      on m.content_type = 'circular' and m.content_id = ci.id::text
     and m.slug is not null
    left join public.entities e on e.id = ci.entity_id;

  elsif p_corpus = 'service' then
    delete from public.search_index where corpus = 'service';
    insert into public.search_index (corpus, content_id, slug, title, entity_text, facets_text, lead, facets)
    select 'service', s.id::text, m.slug,
           coalesce(s.service_name_ar, ''),
           -- Already denormalized on the row; it was simply pooled with sectors
           -- at an unboosted weight before.
           coalesce(s.provider_name, ''),
           array_to_string(s.sectors, ' '),
           concat_ws(' ', s.intro_title, s.intro_description,
                     array_to_string(s.requirements, ' '),
                     array_to_string(s.required_documents, ' '),
                     array_to_string(s.steps, ' ')),
           jsonb_strip_nulls(jsonb_build_object(
             'provider_name', s.provider_name,
             'sectors', to_jsonb(coalesce(s.sectors, array[]::text[]))))
    from public.services s
    join public.seo_item_meta m
      on m.content_type = 'service' and m.content_id = s.id::text
     and m.slug is not null;

  -- ── NEW IN 144 (plan §6.1 step 2) ────────────────────────────────────────
  elsif p_corpus = 'compliance' then
    delete from public.search_index where corpus = 'compliance';
    insert into public.search_index (corpus, content_id, slug, title,
                                     entity_text, facets_text, lead, facets)
    select 'compliance', g.id::text, m.slug,
           coalesce(g.title, ''),
           -- B: the issuing entity, the same slot `service`/`circular` give it.
           coalesce(g.provider_name, ''),
           concat_ws(' ', g.service_ref, array_to_string(g.sectors, ' ')),
           -- D: the guide, WHOLE. It is ours, it is published in full and
           -- ungated, so unlike `circular` there is no free-floor to compute.
           -- The regexp strips the image-hole token lines (§6.2).
           regexp_replace(coalesce(g.guide_md, ''), '^[ \t]*\d+_\d+[ \t]*$', '', 'gn'),
           jsonb_strip_nulls(jsonb_build_object(
             'provider_name', g.provider_name,
             'service_ref', g.service_ref,
             'sectors', to_jsonb(coalesce(g.sectors, array[]::text[]))))
    from public.library_compliance_v g
    join public.seo_item_meta m
      on m.content_type = 'compliance' and m.content_id = g.id::text
     and m.slug is not null;

  elsif p_corpus = 'blog' then
    delete from public.search_index where corpus = 'blog';
    insert into public.search_index (corpus, content_id, owner_user_id, slug, title, entity_text, facets_text, lead, facets)
    select 'blog', b.post_id::text, b.owner_user_id, b.token,
           coalesce(nullif(trim(b.title), ''), b.question_text, ''),
           '',                                  -- the reader owns it; no issuer
           concat_ws(' ', b.subtype, b.display_mode),
           coalesce(b.content_md, ''),
           jsonb_strip_nulls(jsonb_build_object(
             'subtype', b.subtype, 'display_mode', b.display_mode,
             'is_public', b.is_public, 'is_published', b.is_published))
    from public.blog_posts b
    where b.deleted_at is null;

  elsif p_corpus = 'template' then
    delete from public.search_index where corpus = 'template';
    insert into public.search_index (corpus, content_id, owner_user_id, slug, title, entity_text, facets_text, lead, facets)
    select 'template', t.template_id::text, t.user_id, t.template_id::text,
           coalesce(t.title, ''),
           '',
           coalesce(t.created_by::text, ''),
           coalesce(t.content_md, ''),
           jsonb_strip_nulls(jsonb_build_object('created_by', t.created_by::text))
    from public.user_templates t
    where t.deleted_at is null;

  else
    raise exception 'refresh_search_index: unknown corpus %', p_corpus;
  end if;

  get diagnostics v_count = row_count;
  perform public.refresh_bm25_stats(p_corpus);
  return v_count;
end $$;

-- Restated, not mutated: `create or replace` preserves the existing ACL, but
-- 112 states the final state here too and a replay must keep it true.
revoke all on function public.refresh_search_index(text) from public, anon, authenticated;

-- ── 3. Nightly refresh ──────────────────────────────────────────────────────
-- 111 §11's pattern exactly: guarded on pg_cron being installed, unscheduled if
-- present, then re-scheduled — so a replay is a no-op rather than a duplicate
-- job. The command list below is the LIVE one (queried 2026-08-22) with one
-- line added; `cron.schedule` REPLACES the whole command, so a line dropped
-- here is a corpus that silently stops refreshing.

do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    if exists (select 1 from cron.job where jobname = 'bm25_refresh_nightly') then
      perform cron.unschedule('bm25_refresh_nightly');
    end if;

    perform cron.schedule(
      'bm25_refresh_nightly', '20 2 * * *',
      $cron$
        select public.refresh_search_index('regulation');
        select public.refresh_search_index('judgment');
        select public.refresh_search_index('circular');
        select public.refresh_search_index('service');
        select public.refresh_search_index('compliance');
        select public.refresh_bm25_stats('blog');
        select public.refresh_bm25_stats('template');
      $cron$);
  end if;
end $$;

-- ── 4. Backfill ─────────────────────────────────────────────────────────────
-- Runs now rather than waiting for 02:20 — the backend half of this change
-- (§6.3: 'compliance' in PUBLIC_CORPORA) ships the same day, and a corpus in
-- PUBLIC_CORPORA with no rows is a search chip that returns nothing.
-- refresh_search_index calls refresh_bm25_stats itself, so IDF is populated
-- before the first query.
--
-- ⚠ EXPECT 337 — every guide in `library_compliance_v` is published (plan §1).
--   A lower number means unslugged rows in `seo_item_meta`, i.e. the join, not
--   this migration. Zero means §1's CHECK or the view is missing.
select public.refresh_search_index('compliance');

-- ============================================================================
-- ROLLBACK ───────────────────────────────────────────────────────────────────
-- Run the three blocks IN THIS ORDER. The rows must go before the CHECK comes
-- back, or the ADD CONSTRAINT fails on its own data.
--
--   -- 1. Drop the corpus and its IDF statistics.
--   DELETE FROM public.search_index    WHERE corpus = 'compliance';
--   DELETE FROM public.bm25_terms      WHERE corpus = 'compliance';
--   DELETE FROM public.bm25_corpus_stats WHERE corpus = 'compliance';
--
--   -- 2. Restore the six-value vocabulary.
--   ALTER TABLE public.search_index
--       DROP CONSTRAINT IF EXISTS search_index_corpus_check;
--   ALTER TABLE public.search_index
--       ADD CONSTRAINT search_index_corpus_check
--       CHECK (corpus = ANY (ARRAY[
--           'regulation'::text, 'judgment'::text, 'circular'::text,
--           'service'::text, 'blog'::text, 'template'::text
--       ]));
--
--   -- 3. Restore the nightly command (drop the compliance line).
--   DO $rb$
--   BEGIN
--     IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
--       IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'bm25_refresh_nightly') THEN
--         PERFORM cron.unschedule('bm25_refresh_nightly');
--       END IF;
--       PERFORM cron.schedule(
--         'bm25_refresh_nightly', '20 2 * * *',
--         $cron$
--           select public.refresh_search_index('regulation');
--           select public.refresh_search_index('judgment');
--           select public.refresh_search_index('circular');
--           select public.refresh_search_index('service');
--           select public.refresh_bm25_stats('blog');
--           select public.refresh_bm25_stats('template');
--         $cron$);
--     END IF;
--   END
--   $rb$;
--
-- The `compliance` BRANCH in refresh_search_index may stay: with the six-value
-- CHECK back, calling it raises 23514 loudly instead of writing anything, and
-- nothing calls it once step 3 has run. To remove it as well, re-apply
-- migration 112 §4 in full — never hand-edit the function, and never edit this
-- file or 112: correct forward, in a new migration.
--
-- ⚠ Roll the BACKEND back first (§6.3: 'compliance' out of PUBLIC_CORPORA and
--   CORPUS_SECTION), or the live search path queries a corpus that has just
--   lost its rows and its bm25_corpus_stats entry.
-- ============================================================================
