-- ============================================================================
-- Migration 123 — library_judgments_ranked: the published-only /judgments view
-- Plan: .claude/plans/library_court_sections_publish_ramp.md §0 (the blocker) ·
--       §1.1 (this view) · §1.2 (the lister rewire it unlocks) · §2.3 (courts)
--
-- Direct analogue of migration 116 (`library_regulations_ranked`). Read 116's
-- header first: everything it says about corpus ⋈ sidecar, about the view NOT
-- being a convenience, and about the SECURITY posture applies here verbatim.
--
-- WHAT BREAKS TODAY, AND WHY THIS IS NOT OPTIONAL.
-- The /judgments wing has no published-only surface. Below `SAMPLE_MODE_MAX_IDS`
-- (1000 — backend/app/services/library_service.py:154) the hub paginates the id
-- list `_published_ids()` returns. Above it that function returns NULL and the
-- wing falls back to paginating the CORPUS — all 30,531 `cases` rows — then
-- silently discards every row that has no slug. The lister's own docstring names
-- the assumption that path rests on: "every judgment is slugged then." At the
-- ~10,000-of-30,531 publish the plan calls for, that assumption is false: a
-- nine-card page would render about THREE cards, and `total_pages` would be
-- derived from the corpus count — roughly 3,393 pages, mostly holes, every one
-- of them prerendered as a static page.
--
-- This is not hypothetical. It already happened on the other wing: on 2026-08-06
-- publishing 503 regulations crossed the then-300 ceiling and prod /regulations
-- returned `items: 0`. Migration 116 fixed it there by making "published" a
-- property of the RELATION instead of a post-filter applied after pagination.
-- Judgments never got the same treatment. This migration is that treatment.
--
-- The consequence worth stating plainly: a page cannot come back short, at any
-- corpus size and any publish size, because an unpublished row cannot be in the
-- relation. Counts over this view are counts of what is SERVABLE, which is what
-- every page number on this wing has to mean.
--
-- ⚠ `public.cases` IS PIPELINE-OWNED. Never ALTER it, never write to it, never
-- index it from here — same rule as `regulations_v2`. All SEO state for a
-- judgment lives in the `seo_item_meta` SIDECAR keyed (content_type, content_id)
-- where `content_id` is TEXT. This view is the place the two meet, and it is the
-- only thing this migration creates: no DDL touches the corpus.
--
-- ⚠ WHY THE COLUMNS ARE ENUMERATED AND `c.*` IS FORBIDDEN.
-- `cases` carries `embedding` (pgvector — the legacy `hybrid_search_cases` path
-- still needs it on the corpus) and `fts` (tsvector, ~30.5k rows). `c.*` would
-- make every `select *` this view ever serves drag a full embedding array and a
-- lexeme vector per row across the wire to the API layer, for payloads that
-- print a card. The 1200-char judgment body is already the expensive column; the
-- vectors are not negotiable at all. Explicit lists also mean a corpus reshape
-- shows up as a failed migration instead of a silently wider view.
--
-- ⚠ NO COLUMN COLLISION — AND THE FILE ASSERTS IT RATHER THAN ASSUMING IT.
-- `slug` / `rank` / `usage_score` / `seo_tier` / `gate_override` are sidecar
-- names. `cases` has never carried them: the judgments wing was built with NO
-- migration precisely because the plan's assumed `cases.slug` / `pdpl_status` /
-- `seo_title` columns were never created, and nothing in backend/, agents/,
-- scripts/ or shared/ reads a `cases.slug`. A collision would not be silent
-- either — `select c.slug, m.slug` in a view definition is a hard Postgres error
-- (column «slug» specified more than once). But because this file selects
-- only the corpus columns it names, a NEW `cases.slug` added later by the
-- pipeline would be shadowed by the sidecar value with no error at all, so the
-- guard block at the bottom checks `information_schema` for exactly that and
-- refuses to apply.
--
-- ⚠ NO NEW INDEX, DELIBERATELY. The sidecar side is already covered by
-- `seo_item_meta_rank_idx` (migration 116) — `(content_type, rank) WHERE slug is
-- not null` — which is content-type agnostic, so judgments inherit it for free.
-- The corpus side cannot be indexed from here (see above), and `m.content_id =
-- c.id::text` could not use an index on `cases.id` anyway: the cast is on the
-- corpus side and the sidecar column is text. It plans as a hash join over
-- 30,531 rows, exactly as 116 does over 3,951. That is the accepted cost of a
-- text-keyed sidecar and it is not worth a functional index on a table we do not
-- own.
--
-- WHAT THE VIEW HAS TO CARRY, AND WHY THESE COLUMNS:
--   * `_JUDGMENT_HUB_SELECT` (library_service.py:4048) — id, case_ref, court,
--     court_level, city, case_number, judgment_number, date_hijri,
--     date_gregorian, legal_domains, short_summary, summary, facts, ruling.
--     All four title-source columns are in that list on purpose (the card title
--     and the doc-page H1 must be byte-identical), so all four are here.
--   * `_JUDGMENT_DOC_SELECT` (library_service.py:4058) — adds appeal_result,
--     details_url, referenced_regulations, content.
--   * `date_gregorian` + `id` — §1.2 keeps today's ordering
--     (`date_gregorian` DESC NULLS LAST, then `id`) and orders it over THIS
--     relation.
--   * `court` — §2.3's court sections filter `in.(variants)` over this view.
--     Without it every court route would have to fall back to the corpus and
--     re-inherit the bug above.
--   * `slug` — carried from the sidecar, which is what retires the `_slug_map`
--     round-trip the lister does today.
--   * `rank` / `usage_score` — not yet written for `judgment` (build_usage_rank
--     ranks regulations only). Exposed now so the ordering contract can become
--     one sortable integer here too without another migration; `rank` NULL sorts
--     last under PostgREST's ascending order, so unranked rows queue at the back
--     rather than breaking.
--   * `seo_tier` / `gate_override` — 116 exposes both; parity, so a future
--     gate-aware hub read needs no schema change. Costs nothing today.
-- Every other non-vector `cases` column rides along too (the narrative columns,
-- the appeal block, `cited_laws_text`): the API layer selects explicit column
-- lists from this view, and a column that is absent from the relation is a
-- PostgREST 400 at read time, not a compile error.
--
-- ⚠ APPLY THIS FILE AS ONE SCRIPT. The guard at the bottom is what makes the
-- migration self-verifying, and it can only roll the view back if it runs in the
-- same implicit transaction as the CREATE.
-- ============================================================================

-- --- 1. The ranked, published-only judgments view ---------------------------
-- Corpus ⋈ sidecar, filtered to published rows (`slug is not null`).
-- `seo_item_meta`'s PK is (content_type, content_id), so this inner join matches
-- AT MOST ONE sidecar row per judgment and cannot fan out — an inner join that
-- fanned out would inflate every count taken over this view, silently.
create or replace view public.library_judgments_ranked as
select
  -- identity + card metadata
  c.id,
  c.case_ref,
  c.entity_id,
  c.case_variant,
  c.court,
  c.court_level,
  c.city,
  c.case_number,
  c.judgment_number,
  c.date_hijri,
  c.date_gregorian,
  c.details_url,
  c.legal_domains,
  c.referenced_regulations,
  c.reference_count,
  c.cited_laws_text,
  c.source,
  c.ingested_at,
  -- appeal block
  c.appeal_court,
  c.appeal_city,
  c.appeal_judgment_number,
  c.appeal_date_hijri,
  c.appeal_date_gregorian,
  c.appeal_result,
  -- the document itself (THE body — the per-stage columns below are pipeline
  -- SUMMARIES of it, never a substitute for it)
  c.content,
  c.summary,
  c.short_summary,
  c.verdict_procedural,
  c.verdict_substantive,
  -- the 11 narrative columns
  c.facts,
  c.claims,
  c.plaintiff_grounds,
  c.defendant_response,
  c.defendant_grounds,
  c.reasoning,
  c.ruling,
  c.objection_grounds,
  c.appellee_response,
  c.appeal_reasoning,
  c.appeal_ruling,
  -- sidecar
  m.slug,
  m.rank,
  m.usage_score,
  m.seo_tier,
  m.gate_override
from public.cases c
join public.seo_item_meta m
  on m.content_type = 'judgment'
 and m.content_id = c.id::text
where m.slug is not null;

comment on view public.library_judgments_ranked is
  'Published judgments (slug present) with their sidecar rank. THE surface '
  '/judgments and /judgments/courts/{slug} paginate. Published is a property of '
  'the relation, not a post-filter, so no page can come back short at any '
  'publish size. `embedding` and `fts` are deliberately absent. See migration '
  '123 before changing.';

revoke all on public.library_judgments_ranked from anon, authenticated;
grant select on public.library_judgments_ranked to service_role;

-- --- 2. Self-verification ---------------------------------------------------
-- The corpus is pipeline-owned and it DRIFTS: migration 021 still declares
-- `legal_domains jsonb` in `hybrid_search_cases`, while 109/111/112 `unnest()`
-- and `array_to_string()` it — i.e. the column is `text[]` and has been reshaped
-- since. A hand-written column list against a drifting table has exactly two
-- failure modes, and only one of them is loud on its own:
--
--   * a column named here that no longer exists  -> CREATE VIEW fails. Loud.
--   * a column that exists but is NOT named here -> the view is quietly narrower
--     than the corpus, and the gap only surfaces months later as a PostgREST 400
--     the first time some caller selects it.
--
-- This block converts the second into the first. It is not defensive padding: it
-- is the reason a reviewer can trust the list above without re-deriving it.
--
-- Excluded BY TYPE, not by name, so a future pipeline-added vector column does
-- not trip the guard and does not need this file edited.
do $$
declare
  v_shadowed text;
  v_missing  text;
begin
  -- (a) Sidecar names must not exist on the corpus. If one ever does, the view
  --     above shadows the corpus value with the sidecar's, with no error.
  select string_agg(c.column_name, ', ' order by c.column_name)
    into v_shadowed
  from information_schema.columns c
  where c.table_schema = 'public'
    and c.table_name   = 'cases'
    and c.column_name in ('slug', 'rank', 'usage_score', 'seo_tier', 'gate_override');

  if v_shadowed is not null then
    raise exception
      'public.cases now carries sidecar-named column(s): %. '
      'library_judgments_ranked would shadow them with the seo_item_meta value. '
      'Decide which one the wing means and alias the loser before applying '
      'migration 123.',
      v_shadowed;
  end if;

  -- (b) Completeness. Every `cases` column that is not a vector/tsvector must be
  --     reachable through the view.
  select string_agg(c.column_name, ', ' order by c.ordinal_position)
    into v_missing
  from information_schema.columns c
  where c.table_schema = 'public'
    and c.table_name   = 'cases'
    -- `embedding` (vector) and `fts` (tsvector) — see the header.
    and c.udt_name not in ('vector', 'tsvector')
    -- Named opt-outs. Add a column here ONLY to record "the view deliberately
    -- does not carry this, for a reason other than its type".
    and c.column_name <> all (array[]::text[])
    and not exists (
      select 1
      from information_schema.columns v
      where v.table_schema = 'public'
        and v.table_name   = 'library_judgments_ranked'
        and v.column_name  = c.column_name
    );

  if v_missing is not null then
    raise exception
      'library_judgments_ranked does not expose public.cases column(s): %. '
      'Append them to the select list in migration 123 (CREATE OR REPLACE VIEW '
      'can only add columns at the END of the list — inserting one mid-list '
      'fails), or add them to the opt-out array in this guard if the omission '
      'is intended.',
      v_missing;
  end if;
end
$$;

-- ============================================================================
-- ⚠ NOT AUTO-APPLIED. Migration files in this directory are run by hand in the
-- Supabase SQL Editor; nothing in the repo executes them. This file is inert
-- until someone does, and the backend rewire (plan §1.2) must not deploy first —
-- it would read a view that does not exist.
--
-- THIS IS THE FILE THAT RETIRES `SAMPLE_MODE_MAX_IDS`
-- (backend/app/services/library_service.py:154) FOR THE JUDGMENTS LISTER, the
-- same way migration 116 retired it for the regulations lister. Once §1.2 points
-- the lister at `library_judgments_ranked`, the 1000-row ceiling, the sample-mode
-- branch, the `_published_ids()` call and the `_slug_map` round-trip all stop
-- applying to this wing. The constant stays for circulars and services (100
-- published each, untouched). Migration 124 removes the other half of its job —
-- the sector counts.
-- ============================================================================
