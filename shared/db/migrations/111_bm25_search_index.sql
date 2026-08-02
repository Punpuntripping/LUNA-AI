-- ============================================================================
-- 111_bm25_search_index.sql — Shared BM25 navigation search (Wave A)
--
-- Plan: .claude/plans/bm25_navigation_search.md
--
-- One index + one ranking function serving every navigation surface, replacing
-- the per-hub single-column `ILIKE '%q%'`. Real BM25 (IDF + tf saturation +
-- length normalization), Arabic-normalized, hand-rolled — `pg_search`/ParadeDB
-- is not in the Supabase catalog.
--
-- ── WHAT IS INDEXED (D3, option 2) ─────────────────────────────────────────
-- title (A) + facets (B) + the ALWAYS-FREE lead (D). NO gated body text ever
-- enters this index, which is why there is no snippet gate, no ts_headline and
-- no per-hit access-tier resolution anywhere in the search path. Hub cards keep
-- rendering the static free snippet they already render.
--
-- The per-corpus "what is free" audit (plan §4.5, A0, 2026-08-01):
--   regulation  llm_summary ?? summary   FULL  — shipped unconditionally as
--                                        `summary_md` (library_service.py:2205),
--                                        outside the gate entirely
--   judgment    short_summary            FULL  — `summary_md` (:4266)
--   circular    content, FIRST 400 CHARS ONLY — `content` is gated by
--                                        truncate_for_gate(..., free_chars=
--                                        GATE_FREE_CHARS_DEFAULT=400) at :3127
--   service     intro_description + requirements + required_documents + steps
--                                        FULL  — services never gate (:2425, :477)
--   blog/template  content_md            FULL  — owner-only corpora
--
-- ⚠ Adding any other column here is a DATA LEAK, not a ranking tweak. It is
--   reachable by probing ?q= even though nothing is ever displayed.
--   Deliberately excluded: cases.content (8,538 avg chars, gated after
--   JUDGMENT_FREE_CHARS), cases.facts/reasoning/ruling (internal summaries the
--   doc page NEVER publishes — no unlock path exists for them, see
--   _JUDGMENT_DOC_SELECT at library_service.py:3407), services.service_context
--   (in no anon payload), and every *_url / content_path / content_hash / bare
--   numeric id — that last set is exactly the junk polluting cases.fts.
--
-- ⚠ TRAP — luna_normalize_ar + luna_tsvector feed search_doc and the
--   search_index_title_norm index. Redefining either WITHOUT re-running
--   refresh_search_index() for every corpus silently corrupts both: old rows
--   keep vectors built by the old definition and quietly stop matching.
--
-- ── ARABIC PIPELINE — MEASURED, NOT ASSUMED (2026-08-01, PG 17.6) ──────────
-- Three corrections to the plan's draft, each verified against this database:
--
-- 1. The draft's harakat class `[ـً-ٰٟ]` spans U+064B–U+0670, which SWALLOWS
--    U+0660–U+0669 — the Arabic-Indic digits. Measured:
--      'الْإِيجَار ١٤٤٥هـ ٥٠٪' -> 'الايجار ه محمد '   (١٤٤٥ and ٥٠٪ destroyed)
--    Fixed class is U+0640 + U+064B–U+0655 + U+0670, which never touches digits.
--    Hijri years and judgment numbers are load-bearing here.
--
-- 2. `ة -> ه` and `ى -> ي` are DROPPED. The Arabic snowball stemmer already
--    conflates ة/ه on its own, and pre-converting BREAKS its suffix stripping:
--      مكتبة    -> مكتب   |  ة->ه first -> مكتب     (no gain)
--      اللائحة  -> لايح   |  ة->ه first -> لايحه    (WORSE)
--      الخامسة  -> خامس   |  ة->ه first -> خامسه    (WORSE)
--      الأمومة  -> اموم   |  ة->ه first -> امومه    (WORSE)
--      على      -> علي    |  ى->ي first -> عل       (WORSE)
--    So normalization is hamza-only: أ إ آ ٱ -> ا, plus tatweel + harakat.
--    That alone satisfies the plan's criterion — الايجار and الإيجار both
--    stem to 'ايجار'.
--
-- 3. Hamza SEATS (ؤ ئ) are left alone: the stemmer already maps ؤ->و and ئ->ي
--    internally, so the draft's translate was a no-op. Consequence: مسؤولية and
--    مسئولية do NOT conflate. Known recall gap, logged for Wave F — conflating
--    them needs ؤ AND ئ mapped to one char, which would wreck قائمة -> قايم.
--
-- ── KNOWN RECALL GAP: «ال» + final ه ───────────────────────────────────────
-- Measured boundary of the ة/ه behaviour. The stemmer strips ة always, and
-- strips a bare word's final ه, but NOT when the definite article and a final
-- ه appear together:
--     إجازة / اجازة / الإجازة / الاجازة / اجازه  -> 'اجاز'   (all conflate)
--     الاجازه                                    -> 'اجازه'  (MISSES)
--     محكمة / المحكمة -> 'محكم'   |  المحكمه -> 'محكمه'  (MISSES)
--     لائحة / اللائحة -> 'لايح'   |  اللائحه -> 'لايحه'  (MISSES)
-- So a reader who types «المحكمه» does not find «المحكمة». Accepted for now:
-- the obvious fixes are all worse. Global ة->ه degrades every stem (see 2
-- above); rewriting a trailing ه to ة on ال-prefixed words would corrupt real
-- legal vocabulary — الفقه -> الفقة, الله -> اللة. The right fix is variant/typo
-- tolerance via pg_trgm (already installed, earmarked in plan §1.2) as a
-- fallback when BM25 returns nothing. Wave F.
-- ⚠ Do NOT "fix" this by adding ة->ه to luna_normalize_ar. It reads like the
--   obvious one-character solution and it makes overall recall worse.
--
-- Stopwords: managed Supabase gives no $SHAREDIR access, so there is no
-- stopword FILE. ts_delete after vector construction is the only route. The
-- list below is POST-STEM and was generated BY RUNNING THE STEMMER — writing it
-- by intuition is how you get a list that removes nothing. Note التي -> 'الت'
-- and الذي -> 'الذ', which is exactly what hand-writing gets wrong.
-- Deliberately NOT stopworded despite being frequent: غير and ذات — «غير نظامي»
-- is semantically load-bearing in legal Arabic, and BM25's IDF already
-- down-weights frequent terms without discarding them.
-- ============================================================================

-- ── 1. Arabic normalization ────────────────────────────────────────────────

create or replace function public.luna_normalize_ar(t text)
returns text
language sql
immutable
parallel safe
as $$
  select regexp_replace(
           translate(
             -- U+0640 tatweel, U+064B-U+0655 harakat/tanween/hamza marks,
             -- U+0670 superscript alef. Stops short of U+0660 (digit ٠).
             regexp_replace(coalesce(t, ''), '[ـً-ٰٕ]', '', 'g'),
             'أإآٱ', 'اااا'),
           '\s+', ' ', 'g');
$$;

comment on function public.luna_normalize_ar(text) is
  'Arabic IR normalization: strip tatweel/harakat, fold hamza-carrying alef to ا. '
  'IMMUTABLE and used by search_index.search_doc + search_index_title_norm — '
  'redefining requires refresh_search_index() on every corpus AND a REINDEX. '
  'Deliberately does NOT fold ة/ه or ى/ي: the snowball stemmer already handles '
  'ة/ه, and pre-folding degrades its suffix stripping (اللائحة لايح -> لايحه).';

-- ── 2. Text search configuration ───────────────────────────────────────────
-- Owned copy of `arabic` so the config name is stable and schema-qualified;
-- to_tsvector's 2-arg form with a literal regconfig is what keeps callers
-- IMMUTABLE (a search_path-dependent config would not be).

do $$
begin
  if not exists (
    select 1 from pg_ts_config c
    join pg_namespace n on n.oid = c.cfgnamespace
    where c.cfgname = 'arabic_luna' and n.nspname = 'public'
  ) then
    create text search configuration public.arabic_luna ( copy = arabic );
  end if;
end $$;

-- ── 3. Weighted, stopworded vector builder ─────────────────────────────────

create or replace function public.luna_tsvector(t text, w "char")
returns tsvector
language sql
immutable
parallel safe
as $$
  select setweight(
           ts_delete(
             to_tsvector('public.arabic_luna', public.luna_normalize_ar(t)),
             -- POST-STEM lexemes. Regenerate via the stemmer, never by hand.
             array['الت','التي','الذ','الي','ان','انه','او','بعد','به','بها',
                   'بين','ثم','حيث','ذلك','علي','عن','عند','في','فيه','قبل',
                   'قد','كان','كل','كما','لا','له','لها','ما','مع','من',
                   'هذا','هذه','وه','وهو']
           ), w);
$$;

comment on function public.luna_tsvector(text, "char") is
  'Normalized + stemmed + stopworded + weighted tsvector. Same IMMUTABLE '
  'reindex caveat as luna_normalize_ar. The query side MUST be built with this '
  'same function, or stopwords surviving in the query would AND against doc '
  'vectors that no longer contain them and match nothing.';

-- ── 4. The unified index table ─────────────────────────────────────────────

create table if not exists public.search_index (
  id            uuid primary key default gen_random_uuid(),
  corpus        text not null
                check (corpus in ('regulation','judgment','circular',
                                  'service','blog','template')),
  content_id    text not null,
  -- NULL for the four public corpora; set for blog/template (owner-scoped).
  owner_user_id uuid references public.users(user_id) on delete cascade,
  slug          text,
  title         text not null default '',
  -- Facets rendered to text for weight B. The jsonb `facets` below is the
  -- filterable copy; this is the searchable one («وزارة التجارة» as a query
  -- means the issuer, and issuers live in facets rather than titles).
  facets_text   text not null default '',
  -- ⚠ ALWAYS-FREE text only. See the audit table in the header.
  lead          text not null default '',
  facets        jsonb not null default '{}'::jsonb,
  search_doc    tsvector not null default ''::tsvector,
  doc_len       integer not null default 0,
  updated_at    timestamptz not null default now()
);

-- PG 15+ NULLS NOT DISTINCT gives one row per (corpus, content_id) for the
-- public corpora without inventing a sentinel uuid for owner_user_id.
-- ⚠ This single line is what would break a self-hosted/older-PG target — it
--   matters for the KSA/Alibaba residency migration.
create unique index if not exists search_index_identity
  on public.search_index (corpus, content_id, owner_user_id) nulls not distinct;

create index if not exists search_index_doc_gin
  on public.search_index using gin (search_doc);
create index if not exists search_index_corpus
  on public.search_index (corpus) where owner_user_id is null;
create index if not exists search_index_owner
  on public.search_index (owner_user_id, corpus) where owner_user_id is not null;
create index if not exists search_index_facets
  on public.search_index using gin (facets jsonb_path_ops);
-- Backs the exact-title bonus (§4.3). Depends on luna_normalize_ar — same
-- reindex caveat.
create index if not exists search_index_title_norm
  on public.search_index (public.luna_normalize_ar(title));

-- Keep search_doc and doc_len in lockstep with the text they describe. A
-- BEFORE trigger rather than a GENERATED column because doc_len needs a
-- subquery over the vector, which generated columns disallow — and splitting
-- them across two mechanisms is how they drift.
create or replace function public.search_index_fill()
returns trigger
language plpgsql
as $$
begin
  new.search_doc :=
      public.luna_tsvector(new.title,       'A')
   || public.luna_tsvector(new.facets_text, 'B')
   || public.luna_tsvector(new.lead,        'D');

  -- ⚠ doc_len is the TOKEN count, not length(search_doc). length() counts
  --   DISTINCT lexemes, which makes a repetitive document look short and
  --   inverts the length normalization BM25 exists to provide — strictly
  --   worse than having no normalization at all.
  --   Unboosted on purpose: title_boost belongs in tf (§4.3), not in dl.
  new.doc_len := coalesce(
    (select sum(coalesce(array_length(positions, 1), 1))::int
     from unnest(new.search_doc)), 0);

  new.updated_at := now();
  return new;
end $$;

drop trigger if exists search_index_fill_trg on public.search_index;
create trigger search_index_fill_trg
  before insert or update of title, facets_text, lead
  on public.search_index
  for each row execute function public.search_index_fill();

-- ── 5. RLS ─────────────────────────────────────────────────────────────────
-- Writes are service-role only (no insert/update/delete policy exists). The
-- backend ALSO scopes every private query by user_id in the service layer —
-- this is the backstop, not the only guard.

alter table public.search_index enable row level security;

drop policy if exists search_index_public_read on public.search_index;
create policy search_index_public_read on public.search_index
  for select using (owner_user_id is null);

drop policy if exists search_index_owner_read on public.search_index;
create policy search_index_owner_read on public.search_index
  for select using (
    owner_user_id = (select user_id from public.users where auth_id = auth.uid())
  );

-- ── 6. BM25 statistics ─────────────────────────────────────────────────────

create table if not exists public.bm25_terms (
  corpus   text    not null,
  lexeme   text    not null,
  doc_freq integer not null,
  primary key (corpus, lexeme)
);

create table if not exists public.bm25_corpus_stats (
  corpus      text primary key,
  doc_count   integer not null,
  avg_doc_len numeric not null,
  computed_at timestamptz not null default now()
);

alter table public.bm25_terms       enable row level security;
alter table public.bm25_corpus_stats enable row level security;

-- IDF statistics are aggregate counts over already-public text; bm25_search is
-- SECURITY DEFINER and reads them on the caller's behalf, but direct reads are
-- harmless and keep the tables inspectable.
drop policy if exists bm25_terms_read on public.bm25_terms;
create policy bm25_terms_read on public.bm25_terms for select using (true);
drop policy if exists bm25_corpus_stats_read on public.bm25_corpus_stats;
create policy bm25_corpus_stats_read on public.bm25_corpus_stats for select using (true);

create or replace function public.refresh_bm25_stats(p_corpus text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_sql text;
begin
  -- ⚠ ts_stat takes a SQL STRING, not a query. quote_literal on the corpus name
  --   is the one injection seam in this migration.
  v_sql := 'select search_doc from public.search_index where corpus = '
           || quote_literal(p_corpus);

  delete from public.bm25_terms where corpus = p_corpus;

  execute format(
    'insert into public.bm25_terms (corpus, lexeme, doc_freq)
     select %L, word, ndoc from ts_stat(%L)
     on conflict (corpus, lexeme) do update set doc_freq = excluded.doc_freq',
    p_corpus, v_sql);

  insert into public.bm25_corpus_stats (corpus, doc_count, avg_doc_len, computed_at)
  select p_corpus,
         count(*)::int,
         coalesce(avg(nullif(doc_len, 0)), 1)::numeric,
         now()
  from public.search_index
  where corpus = p_corpus
  on conflict (corpus) do update
    set doc_count   = excluded.doc_count,
        avg_doc_len = excluded.avg_doc_len,
        computed_at = excluded.computed_at;
end $$;

-- ── 7. Judgment title (ranking only) ───────────────────────────────────────
-- shared/seo/judgment_naming.py:204 is the SSoT for the DISPLAYED judgment
-- title (card, H1, <title>) and stays so — hubs render their own cards, and
-- bm25_search's `title` is consumed for ranking and the exact-title bonus, not
-- for display. This is a deliberate SQL approximation of that chain so the
-- nightly pg_cron refresh stays pure SQL; exact byte-parity with the Python is
-- NOT required and NOT claimed. The exact-title bonus is inert for judgments
-- anyway — nobody types a 100-char dispute subject verbatim.

create or replace function public.luna_judgment_title(
  p_short_summary text, p_summary text, p_facts text, p_ruling text,
  p_court text, p_date_hijri text
) returns text
language sql
immutable
parallel safe
as $$
  with subject as (
    select coalesce(
      -- First non-blank, non-heading line of the first populated column, the
      -- same walk judgment_subject() does.
      (select l from unnest(
         regexp_split_to_array(
           coalesce(nullif(trim(p_short_summary), ''),
                    nullif(trim(p_summary), ''),
                    nullif(trim(p_facts), ''),
                    nullif(trim(p_ruling), ''), ''), E'\n')) l
        where trim(l) <> '' and trim(l) !~ '^#{1,6}\s' limit 1),
      '') as line
  )
  select nullif(trim(
    -- Drop the markdown bullet marker, cut to ~110 chars on a word boundary,
    -- then append the court + Hijri year that make similar subjects
    -- distinguishable.
    regexp_replace(
      regexp_replace(left(regexp_replace((select line from subject),
                                         '^[ \t]*[-*•·—–]+[ \t]*', ''), 110),
                     '\S*$', '')
      || case when coalesce(trim(p_court), '') <> ''
              then ' — ' || trim(p_court) else '' end
      || case when substring(coalesce(p_date_hijri, '') from '(\d{4})') is not null
              then ' ' || substring(p_date_hijri from '(\d{4})') || 'هـ' else '' end,
      '\s+', ' ', 'g')
  ), '');
$$;

-- ── 8. Corpus backfill ─────────────────────────────────────────────────────
-- Public corpora are inserted ONLY where a slug exists, matching _slug_map()'s
-- rule that "a hub lists only slugged (published) items". As of 2026-08-01 the
-- slug backfill has written exactly 100 rows per public wing, so public search
-- ranks over ~100 docs per wing until it completes. That is the plan's §2
-- ceiling, NOT a bug in this migration — this function picks up new slugs on
-- its next run with no code change.

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
    insert into public.search_index (corpus, content_id, slug, title, facets_text, lead, facets)
    select 'regulation', r.id::text, m.slug,
           coalesce(nullif(trim(r.clean_title), ''), r.title, ''),
           concat_ws(' ', r.entity_name, r.doc_type_bucket, r.status_class,
                     r.reg_ref, array_to_string(r.sectors, ' ')),
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
    insert into public.search_index (corpus, content_id, slug, title, facets_text, lead, facets)
    select 'judgment', c.id::text, m.slug,
           public.luna_judgment_title(c.short_summary, c.summary, c.facts,
                                      c.ruling, c.court, c.date_hijri),
           concat_ws(' ', c.court, c.court_level, c.city, c.case_number,
                     c.judgment_number, array_to_string(c.legal_domains, ' ')),
           -- FULL short_summary: published verbatim as summary_md (:4266).
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
    insert into public.search_index (corpus, content_id, slug, title, facets_text, lead, facets)
    select 'circular', ci.id::text, m.slug,
           coalesce(ci.title, ''),
           concat_ws(' ', ci.entity_ref, ci.doc_type, ci.circ_ref,
                     array_to_string(ci.sectors, ' ')),
           -- ⚠ THE ONLY PARTIAL LEAD. content is gated by truncate_for_gate at
           --   free_chars=400 (:3127); bodies <= CIRCULAR_FREE_LENGTH (800)
           --   downgrade to open and ship whole. Rather than replicate
           --   resolve_gate per row, index the safe floor that holds for EVERY
           --   circular: the first 400 chars with the trailing partial word
           --   stripped. That is byte-identical to truncate_for_gate's
           --   whitespace cut for a gated row and a strict subset for an open
           --   one. Plain left(content,400) would drag the first characters of
           --   the next — gated — word in as a matchable fragment.
           regexp_replace(left(coalesce(ci.content, ''), 400), '\S*$', ''),
           jsonb_strip_nulls(jsonb_build_object(
             'entity_ref', ci.entity_ref, 'doc_type', ci.doc_type,
             'circ_ref', ci.circ_ref,
             'sectors', to_jsonb(coalesce(ci.sectors, array[]::text[]))))
    from public.circulars ci
    join public.seo_item_meta m
      on m.content_type = 'circular' and m.content_id = ci.id::text
     and m.slug is not null;

  elsif p_corpus = 'service' then
    delete from public.search_index where corpus = 'service';
    insert into public.search_index (corpus, content_id, slug, title, facets_text, lead, facets)
    select 'service', s.id::text, m.slug,
           coalesce(s.service_name_ar, ''),
           concat_ws(' ', s.provider_name, array_to_string(s.sectors, ' ')),
           -- Services never gate (:2425 "always resolve to 'open'", :477 fails
           -- open), so the whole published set is free. service_context is NOT
           -- here: it is in no anon payload.
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
    insert into public.search_index (corpus, content_id, owner_user_id, slug, title, facets_text, lead, facets)
    select 'blog', b.post_id::text, b.owner_user_id, b.token,
           coalesce(nullif(trim(b.title), ''), b.question_text, ''),
           concat_ws(' ', b.subtype, b.display_mode),
           coalesce(b.content_md, ''),
           jsonb_strip_nulls(jsonb_build_object(
             'subtype', b.subtype, 'display_mode', b.display_mode,
             'is_public', b.is_public, 'is_published', b.is_published))
    from public.blog_posts b
    where b.deleted_at is null;

  elsif p_corpus = 'template' then
    delete from public.search_index where corpus = 'template';
    insert into public.search_index (corpus, content_id, owner_user_id, slug, title, facets_text, lead, facets)
    select 'template', t.template_id::text, t.user_id, t.template_id::text,
           coalesce(t.title, ''),
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

-- ── 9. Live sync for the private corpora ───────────────────────────────────
-- 105 live rows across blog + template, so per-row trigger cost is noise.
-- ⚠ A soft delete must DELETE the index row, not upsert it — otherwise deleted
--   templates keep surfacing in search forever.

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

  insert into public.search_index (corpus, content_id, owner_user_id, slug, title, facets_text, lead, facets)
  values ('blog', new.post_id::text, new.owner_user_id, new.token,
          coalesce(nullif(trim(new.title), ''), new.question_text, ''),
          concat_ws(' ', new.subtype, new.display_mode),
          coalesce(new.content_md, ''),
          jsonb_strip_nulls(jsonb_build_object(
            'subtype', new.subtype, 'display_mode', new.display_mode,
            'is_public', new.is_public, 'is_published', new.is_published)))
  on conflict (corpus, content_id, owner_user_id) do update
    set slug = excluded.slug, title = excluded.title,
        facets_text = excluded.facets_text, lead = excluded.lead,
        facets = excluded.facets;
  return new;
end $$;

drop trigger if exists search_index_blog_trg on public.blog_posts;
create trigger search_index_blog_trg
  after insert or update or delete on public.blog_posts
  for each row execute function public.search_index_sync_blog();

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

  insert into public.search_index (corpus, content_id, owner_user_id, slug, title, facets_text, lead, facets)
  values ('template', new.template_id::text, new.user_id, new.template_id::text,
          coalesce(new.title, ''), coalesce(new.created_by::text, ''),
          coalesce(new.content_md, ''),
          jsonb_strip_nulls(jsonb_build_object('created_by', new.created_by::text)))
  on conflict (corpus, content_id, owner_user_id) do update
    set title = excluded.title, facets_text = excluded.facets_text,
        lead = excluded.lead, facets = excluded.facets;
  return new;
end $$;

drop trigger if exists search_index_template_trg on public.user_templates;
create trigger search_index_template_trg
  after insert or update or delete on public.user_templates
  for each row execute function public.search_index_sync_template();

-- ── 10. The one ranking function ───────────────────────────────────────────

create or replace function public.bm25_search(
  p_corpora     text[],
  p_query       text,
  p_owner       uuid    default null,
  p_facets      jsonb   default '{}'::jsonb,
  p_limit       integer default 20,
  p_offset      integer default 0,
  p_candidates  integer default 500,
  p_k1          numeric default 1.2,
  p_b           numeric default 0.75,
  p_title_boost numeric default 3.0,
  p_exact_bonus numeric default 1000.0
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
    -- Query vector built with the SAME function as the documents. This is not
    -- stylistic: websearch_to_tsquery would leave stopwords in the query, which
    -- would then AND against doc vectors that had them ts_delete'd and match
    -- nothing at all.
    select public.luna_tsvector(p_query, 'D') as v
  ),
  qlex as (
    -- unnest in FROM, not in the target list: the composite-accessor form
    -- ((unnest(v)).lexeme) re-evaluates the SRF per referenced field.
    select distinct u.lexeme as lex from unnest((select v from qv)) u
  ),
  tsq as (
    -- AND semantics. «نظام العمل» should mean both terms, not either — OR would
    -- pull in every document containing عمل and hand the reader exactly the
    -- noise this design exists to avoid. Wave F may revisit (OR + min-match).
    -- No surviving lexemes (query was all stopwords) => NULL => zero rows,
    -- which is the «من alone returns nothing» criterion.
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
    -- Stage 1: GIN-backed candidate cut. A common Arabic term can match 20k+
    -- judgments; exact BM25 over all of them is not affordable.
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
    -- Stage 2: exact BM25 over the candidates.
    --   idf = ln(1 + (N - df + 0.5) / (df + 0.5))
    --   tf  = A-weighted occurrences * title_boost + non-A occurrences
    -- unnest(tsvector) yields weights[] positionally parallel to positions[],
    -- which is what makes the weighted tf exact rather than estimated.
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
                      ( select count(*) filter (where wt.w = 'A') * p_title_boost
                             + count(*) filter (where wt.w <> 'A')
                        from unnest(v.weights) as wt(w) )::numeric as tf
               from unnest(c.search_doc) v
               where v.lexeme in (select lex from qlex)
             ) x
             left join public.bm25_terms bt
               on bt.corpus = c.corpus and bt.lexeme = x.lexeme
             join stats st on st.corpus = c.corpus
           ), 0)
           -- Exact-title bonus: known-item lookup must not be probabilistic.
           -- «نظام العمل» pins نظام العمل at rank 1 regardless of how many
           -- لوائح mention it in their lead. Set far above any achievable BM25
           -- score so it is an absolute pin, not a thumb on the scale.
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
  'THE ranking function for every navigation surface. Returns no snippet by '
  'design — nothing gated is indexed, so hub cards keep rendering their own '
  'always-free excerpt (plan §5.3). total_count is the count over the CANDIDATE '
  'set, so it is exact only when fewer than p_candidates documents matched.';

revoke all on function public.bm25_search from public;
grant execute on function public.bm25_search to anon, authenticated, service_role;

revoke all on function public.refresh_search_index(text) from public, anon, authenticated;
revoke all on function public.refresh_bm25_stats(text)   from public, anon, authenticated;

-- ── 11. Scheduled refresh ──────────────────────────────────────────────────
-- The four static corpora rebuild nightly: this is also what makes new slugs
-- appear in public search with no code change as the §2 backfill lands.
-- blog/template stay trigger-synced, but their IDF drifts as rows accumulate,
-- so their stats get the same nightly pass.

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
        select public.refresh_bm25_stats('blog');
        select public.refresh_bm25_stats('template');
      $cron$);
  end if;
end $$;

-- ── 12. Backfill ───────────────────────────────────────────────────────────
-- Run once at migration time. refresh_search_index() calls refresh_bm25_stats()
-- itself, so IDF is populated before the first query.
select public.refresh_search_index('regulation');
select public.refresh_search_index('judgment');
select public.refresh_search_index('circular');
select public.refresh_search_index('service');
select public.refresh_search_index('blog');
select public.refresh_search_index('template');
