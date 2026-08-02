-- ============================================================================
-- 112_bm25_entity_weight.sql — issuing-entity name as a first-class ranked field
--
-- Follows 111. Plan: .claude/plans/bm25_navigation_search.md
--
-- ── TWO PROBLEMS, ONE FIX ──────────────────────────────────────────────────
--
-- 1. THE CIRCULAR ENTITY NAME WAS NEVER INDEXED AT ALL. 111 put `entity_ref`
--    into a circular's facet text, but that column is a NUMERIC SOURCE TOKEN
--    ("17900"), not a name — the readable name lives in `public.entities`,
--    reachable via `circulars.entity_id`. Verified: 100/100 slugged circulars
--    resolve to a real name («هيئة التأمين», «الهيئة العامة للغذاء والدواء»).
--    So «ابحث عن تعاميم هيئة التأمين» could not work, and the numeric token was
--    indexed as searchable Arabic text for no benefit.
--
-- 2. WEIGHT `B` WAS DECORATIVE. 111's tf was:
--        count(*) filter (where w = 'A') * p_title_boost
--      + count(*) filter (where w <> 'A')
--    Only `A` was ever boosted, so `B`, `C` and `D` all counted 1. A service's
--    `provider_name` WAS in the vector at `B` — and was worth exactly as much as
--    a word buried in the body. Setting a weight letter and never reading it is
--    worse than not setting one: it reads, at a glance, as though field boosting
--    is in effect.
--
-- ── THE FIELD LADDER ───────────────────────────────────────────────────────
--   A  title                                    × p_title_boost   (3.0)
--   B  issuing entity name                      × p_entity_boost  (2.0)  ← new
--   C  other facets — doc type, sectors, refs    × 1
--   D  the always-free lead                      × 1
--
-- `B` is now its own tier rather than a bucket shared with sectors and ref
-- tokens. A reader typing «وزارة التجارة» means the ISSUER, and that intent
-- should outrank an incidental mention of the ministry in someone else's
-- summary — but it should NOT outrank a document actually titled «وزارة
-- التجارة», which is why entity sits below title rather than beside it.
--
-- Per corpus, "the entity" is whoever issued the document:
--   regulation  regulations.entity_name       (moved out of facet text)
--   circular    entities.entity_name          (JOINED — the fix)
--   service     services.provider_name        (moved out of facet text)
--   judgment    cases.court                   (the court IS the issuing body)
--   blog/temp   none — the reader owns them, there is no issuer
--
-- ⚠ REINDEX REQUIRED, and it is at the bottom of this file. Changing
--   search_index_fill() changes what every vector means; rows written by 111's
--   trigger would keep the old shape and silently rank differently from rows
--   written after. Same class of trap as 111's #4.
--
-- ⚠ bm25_search is DROPPED and recreated, not CREATE OR REPLACE'd. Adding
--   p_entity_boost changes the signature, and Postgres would treat that as an
--   OVERLOAD — leaving 111's version in place and making every call ambiguous
--   ("function is not unique"). Drop by full argument list.
-- ============================================================================

-- ── 1. The new field ───────────────────────────────────────────────────────

alter table public.search_index
  add column if not exists entity_text text not null default '';

comment on column public.search_index.entity_text is
  'Issuing entity/court/provider NAME — weight B, boosted by bm25_search''s '
  'p_entity_boost. Never a ref token or an id: entity_ref/circ_ref/reg_ref are '
  'numeric source tokens and belong in facets_text (weight C) if anywhere.';

-- ── 2. Vector build: four tiers instead of three ───────────────────────────

create or replace function public.search_index_fill()
returns trigger
language plpgsql
as $$
begin
  new.search_doc :=
      public.luna_tsvector(new.title,       'A')
   || public.luna_tsvector(new.entity_text, 'B')
   || public.luna_tsvector(new.facets_text, 'C')
   || public.luna_tsvector(new.lead,        'D');

  -- Unchanged from 111 and still the token count, NOT length(search_doc):
  -- length() counts DISTINCT lexemes, which makes a repetitive document look
  -- short and inverts BM25's length normalization.
  -- Boosts belong in tf (§4.3), never in dl — a document does not become longer
  -- because we care more about one of its fields.
  new.doc_len := coalesce(
    (select sum(coalesce(array_length(positions, 1), 1))::int
     from unnest(new.search_doc)), 0);

  new.updated_at := now();
  return new;
end $$;

drop trigger if exists search_index_fill_trg on public.search_index;
create trigger search_index_fill_trg
  before insert or update of title, entity_text, facets_text, lead
  on public.search_index
  for each row execute function public.search_index_fill();

-- ── 3. Ranking: give B a boost of its own ──────────────────────────────────

drop function if exists public.bm25_search(
  text[], text, uuid, jsonb, integer, integer, integer, numeric, numeric, numeric, numeric);

create or replace function public.bm25_search(
  p_corpora      text[],
  p_query        text,
  p_owner        uuid    default null,
  p_facets       jsonb   default '{}'::jsonb,
  p_limit        integer default 20,
  p_offset       integer default 0,
  p_candidates   integer default 500,
  p_k1           numeric default 1.2,
  p_b            numeric default 0.75,
  p_title_boost  numeric default 3.0,
  p_exact_bonus  numeric default 1000.0,
  p_entity_boost numeric default 2.0
) returns table (
  corpus      text,
  content_id  text,
  slug        text,
  title       text,
  facets      jsonb,
  score       numeric,
  total_count bigint
)
language sql
stable
security definer
set search_path = public
as $$
  with qv as (
    -- Same builder as the documents, so the query cannot carry a stopword the
    -- documents had ts_delete'd — that would AND against nothing and match zero.
    select public.luna_tsvector(p_query, 'D') as v
  ),
  qlex as (
    select distinct u.lexeme as lex from unnest((select v from qv)) u
  ),
  tsq as (
    -- AND semantics, and no surviving lexemes => NULL => zero rows.
    select case when count(*) = 0 then null::tsquery
           else string_agg(quote_literal(lex), ' & ')::tsquery end as q
    from qlex
  ),
  stats as (
    select s.corpus, s.doc_count, s.avg_doc_len
    from public.bm25_corpus_stats s
    where s.corpus = any(p_corpora)
  ),
  cand as (
    select si.corpus, si.content_id, si.slug, si.title, si.facets,
           si.search_doc, si.doc_len
    from public.search_index si, tsq
    where tsq.q is not null
      and si.corpus = any(p_corpora)
      and (case when p_owner is null then si.owner_user_id is null
                else si.owner_user_id = p_owner end)
      and (p_facets = '{}'::jsonb or si.facets @> p_facets)
      and si.search_doc @@ tsq.q
    order by ts_rank_cd(si.search_doc, tsq.q) desc
    limit greatest(p_candidates, p_limit + p_offset)
  ),
  scored as (
    select c.corpus, c.content_id, c.slug, c.title, c.facets,
           coalesce((
             select sum(
               ln(1 + (st.doc_count - coalesce(bt.doc_freq, 0) + 0.5)
                      / (coalesce(bt.doc_freq, 0) + 0.5))
               * (x.tf * (p_k1 + 1))
               / (x.tf + p_k1 * (1 - p_b
                                 + p_b * c.doc_len / nullif(st.avg_doc_len, 0)))
             )
             from (
               select v.lexeme,
                      -- The field ladder. `weights` is positionally parallel to
                      -- `positions`, which is what makes this exact rather than
                      -- estimated.
                      ( select count(*) filter (where wt.w = 'A') * p_title_boost
                             + count(*) filter (where wt.w = 'B') * p_entity_boost
                             + count(*) filter (where wt.w not in ('A','B'))
                        from unnest(v.weights) as wt(w) )::numeric as tf
               from unnest(c.search_doc) v
               where v.lexeme in (select lex from qlex)
             ) x
             left join public.bm25_terms bt
               on bt.corpus = c.corpus and bt.lexeme = x.lexeme
             join stats st on st.corpus = c.corpus
           ), 0)
           + case when public.luna_normalize_ar(p_query)
                     = public.luna_normalize_ar(c.title)
                  then p_exact_bonus else 0 end
           as score
    from cand c
  )
  select s.corpus, s.content_id, s.slug, s.title, s.facets, s.score,
         count(*) over () as total_count
  from scored s
  where s.score > 0
  order by s.score desc, s.content_id
  limit p_limit offset p_offset;
$$;

comment on function public.bm25_search is
  'THE ranking function for every navigation surface. Field ladder: title (A) '
  'x p_title_boost, issuing entity (B) x p_entity_boost, other facets (C) and '
  'the free lead (D) x1. Returns no snippet by design — nothing gated is '
  'indexed, so cards render their own always-free excerpt (plan §5.3). '
  'total_count counts the CANDIDATE set, so it is exact only below p_candidates.';

revoke all on function public.bm25_search from public;
grant execute on function public.bm25_search to anon, authenticated, service_role;

-- ── 4. Populate entity_text, and give circulars a printable entity facet ───

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

revoke all on function public.refresh_search_index(text) from public, anon, authenticated;

-- ── 5. Keep the live-sync triggers writing the new column ──────────────────
-- Both private corpora have no issuer, so entity_text is '' — but the INSERT
-- column lists must still name it, or a future NOT NULL default change breaks
-- them silently.

create or replace function public.search_index_sync_blog()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if (tg_op = 'DELETE') or (new.deleted_at is not null) then
    delete from public.search_index
     where corpus = 'blog' and content_id = coalesce(old.post_id, new.post_id)::text;
    return coalesce(new, old);
  end if;

  insert into public.search_index (corpus, content_id, owner_user_id, slug, title, entity_text, facets_text, lead, facets)
  values ('blog', new.post_id::text, new.owner_user_id, new.token,
          coalesce(nullif(trim(new.title), ''), new.question_text, ''),
          '',
          concat_ws(' ', new.subtype, new.display_mode),
          coalesce(new.content_md, ''),
          jsonb_strip_nulls(jsonb_build_object(
            'subtype', new.subtype, 'display_mode', new.display_mode,
            'is_public', new.is_public, 'is_published', new.is_published)))
  on conflict (corpus, content_id, owner_user_id) do update
    set slug = excluded.slug, title = excluded.title,
        entity_text = excluded.entity_text,
        facets_text = excluded.facets_text, lead = excluded.lead,
        facets = excluded.facets;
  return new;
end $$;

create or replace function public.search_index_sync_template()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if (tg_op = 'DELETE') or (new.deleted_at is not null) then
    delete from public.search_index
     where corpus = 'template'
       and content_id = coalesce(old.template_id, new.template_id)::text;
    return coalesce(new, old);
  end if;

  insert into public.search_index (corpus, content_id, owner_user_id, slug, title, entity_text, facets_text, lead, facets)
  values ('template', new.template_id::text, new.user_id, new.template_id::text,
          coalesce(new.title, ''), '', coalesce(new.created_by::text, ''),
          coalesce(new.content_md, ''),
          jsonb_strip_nulls(jsonb_build_object('created_by', new.created_by::text)))
  on conflict (corpus, content_id, owner_user_id) do update
    set title = excluded.title, entity_text = excluded.entity_text,
        facets_text = excluded.facets_text, lead = excluded.lead,
        facets = excluded.facets;
  return new;
end $$;

-- ── 6. Mandatory reindex ───────────────────────────────────────────────────
-- Not optional and not deferrable to the nightly job: until this runs, every
-- row still carries a 111-shaped vector with no B tier, so entity matches score
-- as though they were body text and the two shapes rank against each other.

select public.refresh_search_index('regulation');
select public.refresh_search_index('judgment');
select public.refresh_search_index('circular');
select public.refresh_search_index('service');
select public.refresh_search_index('blog');
select public.refresh_search_index('template');
