-- ============================================================================
-- 143_related_items.sql — «اقرأ تاليًا» related-items graph (Wave A, data layer)
--
-- Plan: .claude/plans/read_next_related_items.md
--       §2 ground truth · §3 scoring · §4 this file · §9 traps · §10 criteria
-- Depends on: 111 (house style for refresh_x(corpus) + the guarded pg_cron
--             block), 116 (public.regulations_v2 is a VIEW over the
--             pipeline-owned regulation_v2 schema), 142 (compliance corpus =
--             public.service_guides, seo key = service_guides.id::text).
--
-- WHAT THIS BUILDS
-- Two precomputed tables and two refresh functions. `related_axis_weights`
-- holds the scarcity weight of every sector / entity / court value per corpus;
-- `related_items` holds every scored same-type edge above the floor. The
-- backend reads `related_items` with the service role, joins `seo_item_meta`
-- for the publish filter AT READ TIME, and renders the top 7.
--
-- ── D5/D6, AND WHY THEY DICTATE THE SHAPE OF BOTH TABLES ───────────────────
-- The graph is computed over the FULL corpus and the publish filter is applied
-- at render. B can be A's best neighbour and simply not render until B is
-- slugged; publishing then lights B up in everyone's strip with NO recompute,
-- inside the 24h ISR window. Two consequences are load-bearing here:
--   • Nothing in this file reads `seo_item_meta`. Publish state must not leak
--     into the stored graph or D5 collapses.
--   • There is NO top-N per source and NO stored `rank`. A top-10 whose members
--     are all unpublished renders an empty strip while good candidates sit at
--     rank 11+. Rank changes with every publish, so it cannot be stored; order
--     by `score desc` at read time. Average degree on أنظمة is ~2, so storing
--     everything is cheap (أحكام are the exception — see §7.4).
--
-- ── EDGES ARE STORED IN BOTH DIRECTIONS ────────────────────────────────────
-- Every input relation is symmetric and so is the score, but a reader always
-- asks "what is related to X" with X in the SOURCE position. Storing (a,b) and
-- (b,a) makes that a single index-prefix scan on idx_related_items_lookup
-- instead of an OR over two columns. It doubles the row count; the row count is
-- small.
--
-- ⚠ TWO CORRECTIONS TO THE PLAN, both measured live on 2026-08-22 and both
--   authoritative over the plan text as written:
--
--   1. §3.1's `n_min` normalizer is WRONG and is not implemented. It defines
--      scarcity as "smallest value with n >= 2", which measures rank among
--      values, not share of the corpus. On تعاميم there are only 5 entity
--      values (296…619 docs); n_min = 296 would hand w = 1.0 — a near-maximum
--      bonus — to an entity holding 16% of the corpus. Same failure on أحكام
--      (4 values, 225 / 4,669 / 4,966 / 20,671). Replaced by a corpus-SHARE
--      normalizer with one knob, `related_target_share()`. See §0.
--
--   2. §4.5 step 4's `w(entity) < 0.0005` generation guard is not implemented
--      either — it is unsafe on أحكام. There, entity 4,966 scores w = 0.0018,
--      clears 0.0005, and would emit 24.6M pairs on its own. Replaced by a
--      threshold DERIVED from FLOOR that is provably lossless. See §0 and the
--      completeness argument on `related_gen_floor()`.
--
-- ⚠ Every column named below was read off `information_schema` on prod
--   2026-08-22, INCLUDING the three pipeline-owned `regulation_v2` relation
--   tables (§2). Names are written out literally and never resolved at run
--   time: a migration that discovers its own column names cannot be reviewed by
--   reading it, and it turns a pipeline rename from a loud failure into a
--   silent behaviour change. If the ingest renames a column, this file must
--   break on the next apply — that is the intended failure mode.
--
-- Idempotent and re-runnable end to end: create-if-not-exists tables, or-replace
-- functions and views, a delete-then-insert refresh, and the same guarded
-- cron block 111 uses.
-- ============================================================================


-- ── 0. THE CALIBRATION KNOBS ───────────────────────────────────────────────
-- Two constants, in one place, each behind a one-line function. Wave D (plan
-- §7) edits the two `select` lines and re-runs the refresh; nothing else in
-- this file, the backend or the frontend hardcodes either number.
--
-- FLOOR (0.15): the minimum score an edge must reach to be stored at all.
-- Rationale for the starting value (plan §3.4): a pair sharing one common
-- sector (المعاملات التجارية, w = 0.0012, mult(1) = 1) falls far below it,
-- while a pair sharing two mid-rare sectors (العقار + الإسكان, x mult(2) = 3)
-- clears. It is a GUESS. Wave D tunes it against real samples.
--
-- TARGET_SHARE (0.007): what "scarce" means as a fraction of the corpus. It
-- replaces the plan's broken n_min (see the header). Calibrated to reproduce
-- the design anchors on أنظمة sectors, N = 3,951:
--     المعاملات التجارية 783 -> 0.0012      الشؤون الخارجية 88 -> 0.0988
-- and it fixes the cases n_min got backwards:
--     تعاميم البنك المركزي 619/1,843 (34%)      -> 0.0009
--     أحكام وزارة العدل  20,671/30,531 (68%)    -> 0.0002
--     أحكام لجان التأمينية  225/30,531 (0.7%)   -> 1.0 (clamped) — correct
--
-- Inverse SQUARE, not inverse linear: the target spread was ~100x across an
-- 8.9x size difference. 1/n gives 8.9x; (target/share)^2 gives ~980x.
--
-- Functions rather than a settings table so the values inline into the planner
-- and so a change is one edit with no data migration. Both are IMMUTABLE; no
-- index depends on either, so redefining them is safe — but it DOES require a
-- full refresh of both tables (the weights are materialized, and the floor is
-- baked into which rows exist).

create or replace function public.related_floor()
returns real language sql immutable parallel safe as $$
  select 0.15::real;
$$;

comment on function public.related_floor() is
  'Minimum score for an edge to be stored in related_items. Wave D calibration '
  'knob (plan §3.4/§7). Changing it requires re-running refresh_related_items() '
  'for all four corpora — it decides which rows EXIST, not just how they rank.';

create or replace function public.related_target_share()
returns real language sql immutable parallel safe as $$
  select 0.007::real;
$$;

comment on function public.related_target_share() is
  'Scarcity normalizer: the corpus share at which an axis value is worth a full '
  '1.0. w(v) = least(1, (TARGET_SHARE * N / n_v)^2). Replaces the plan §3.1 '
  'n_min normalizer, which measured rank among values instead of corpus share '
  'and gave w = 1.0 to an entity holding 16% of the تعاميم corpus. Changing it '
  'requires refresh_related_axis_weights() THEN refresh_related_items() x4.';

-- The candidate-GENERATION threshold, and the reason it is derived rather than
-- picked. It is what keeps this from being a cross join (plan §9: an unguarded
-- self-join is 15.6M pairs on أنظمة and 932M on أحكام).
--
-- COMPLETENESS ARGUMENT — why FLOOR/2 loses nothing.
-- A stored edge needs  base + least(2, entity_term + sector_term) >= FLOOR,
-- and both caps only ever REDUCE, so a necessary condition is
--     base + entity_term + sector_term >= FLOOR.
-- Every pair carrying base > 0 is generated by its own base generator (curated
-- relations on أنظمة; the Wave E topic pairs on تعاميم/خدمات), so the axis
-- generators only have to catch pairs with base = 0. For those,
--     entity_term + sector_term >= FLOOR
-- implies max(entity_term, sector_term) >= FLOOR/2. So generating from an axis
-- whenever that axis ALONE reaches FLOOR/2, then scoring with ALL axes, cannot
-- miss a storable pair. Generation is a filter on the GENERATOR; scoring still
-- uses full information, which is why a pair generated by a rare sector still
-- collects its entity bonus.
--
-- What that buys, concretely (أنظمة, TARGET_SHARE * N = 27.66):
--   entity generator fires only for n <= 100 docs, so the four biggest issuing
--   authorities (920 / 411 / 366 / 234) generate NOTHING — and their pairs are
--   not lost, because any such pair that could clear the floor must be carrying
--   sector_term >= 0.075 and is generated by the sector side instead.
--   On أحكام it fires only for n <= 780, which is what turns 932M into 2.2M.
--
-- ⚠ MEASURED AT APPLY TIME (2026-08-23), because an earlier draft of this line
--   said "~10^5" and that is wrong by more than an order of magnitude. The court
--   generator emits 2,212,308 pairs (26 of 29 courts pass the guard) and the
--   entity generator 50,400; 1,456,576 of them clear FLOOR and are STORED. The
--   guard is still doing its job — التجارية alone would have been 413M — but
--   size this branch for ~1.5M rows and ~650MB of table+indexes, not for 10^5.

create or replace function public.related_gen_floor()
returns real language sql immutable parallel safe as $$
  select (public.related_floor() / 2.0)::real;
$$;

comment on function public.related_gen_floor() is
  'Per-axis candidate-generation threshold = FLOOR/2. Provably lossless for '
  'base = 0 pairs: if both bonus terms are below FLOOR/2 their sum is below '
  'FLOOR. Replaces plan §4.5 step 4''s hardcoded 0.0005, which is unsafe on '
  'أحكام (entity n = 4,966 clears it and emits 24.6M pairs). Do not raise it '
  'above FLOOR/2 — that starts dropping real edges.';


-- ── 1. THE ENTITY SPLIT-BRAIN (plan §2 warning, §9) ────────────────────────
-- «هيئة الخبراء بمجلس الوزراء» exists in `entities` under THREE refs. Measured
-- on prod 2026-08-22, counts over the FULL regulations_v2 corpus (N = 3,951):
--
--   entity_ref  entities.entity_name                                    regs
--   5000        أنظمة عامة (هيئة الخبراء بمجلس الوزراء)   [national]      920
--   17573       هيئة الخبراء بمجلس الوزراء                [المجالس]        366
--   40002       هيئة الخبراء بمجلس الوزراء                [—]                0
--
-- Collapsed, that one body issues 1,286 of 3,951 أنظمة — 33% of the corpus, so
-- the honest weight is 0.00046: "same issuer" means nothing here.
-- Uncollapsed, the formula sees two mid-size entities and prices both above
-- that — 920 -> 0.0009 and 366 -> 0.0057, the second one 12x too generous. It
-- would add that to the bonus of every pair among 366 documents whose only
-- commonality is "the Council of Ministers' expert body drafted it", which is
-- true of a third of Saudi law. (Neither reaches FLOOR/2, so neither becomes a
-- candidate GENERATOR either way — the damage is to the score, not the volume.)
-- 40002 owns no regulations today; it is folded in anyway so that it can never
-- appear as a stray singleton with weight 1.0 if the ingest ever attaches a
-- document to it.
--
-- WHY A TABLE AND NOT A CASE EXPRESSION: the next merge is an INSERT by whoever
-- finds it, not a function edit + re-apply of a 700-line migration. The map is
-- read by BOTH the weight builder and every candidate generator — canonicalizing
-- on only one side would count the collapsed group correctly and then fail to
-- find its weight, silently zeroing the entity axis for 1,286 documents.
--
-- ⚠ The map is keyed on `entity_ref`, NEVER on `entity_name`:
--   regulations_v2.entity_name is NULL on 1,739 of 3,951 rows (44%). Within
--   regulations_v2 no entity_name maps to more than one entity_ref, so entity_ref
--   alone is a safe key once these three are collapsed.
-- ⚠ canonical_ref must itself be canonical. Do not build chains (a -> b -> c);
--   the resolver is a single lookup and will not follow one.

create table if not exists public.related_entity_aliases (
  entity_ref    text primary key,
  canonical_ref text not null,
  note          text,
  added_at      timestamptz not null default now()
);

comment on table public.related_entity_aliases is
  'entity_ref -> canonical entity_ref, for issuing authorities that exist under '
  'several records. Read by refresh_related_axis_weights (counting) AND by '
  'refresh_related_items (matching) — both sides, always. Migration 143 §1.';
comment on column public.related_entity_aliases.canonical_ref is
  'Must itself be canonical: the resolver does ONE lookup and does not follow '
  'chains. Merging c into b when b already maps to a means writing c -> a.';

insert into public.related_entity_aliases (entity_ref, canonical_ref, note) values
  ('5000',  '5000', 'هيئة الخبراء بمجلس الوزراء — canonical of the 3-way split '
                    '(5000 «أنظمة عامة (هيئة الخبراء بمجلس الوزراء)» 920 regs + '
                    '17573 366 + 40002 0 = 1,286 = 33% of the أنظمة corpus).'),
  ('17573', '5000', 'Duplicate record of 5000 under category «المجالس». 366 regs.'),
  ('40002', '5000', 'Third duplicate record of 5000. 0 regs today; folded in so '
                    'it can never become a singleton with weight 1.0.')
on conflict (entity_ref) do update
  set canonical_ref = excluded.canonical_ref,
      note          = excluded.note;

-- STABLE, not IMMUTABLE (it reads a table), and deliberately not carrying a
-- `set search_path` clause: that would block inlining, and it is only ever
-- called from SECURITY DEFINER functions that already pin the path. Every
-- reference inside is schema-qualified.
create or replace function public.related_canonical_entity(p_ref text)
returns text language sql stable parallel safe as $$
  select coalesce(
           (select a.canonical_ref
              from public.related_entity_aliases a
             where a.entity_ref = p_ref),
           p_ref);
$$;

comment on function public.related_canonical_entity(text) is
  'Folds duplicate entity records onto one key before counting or matching. '
  'Applied to all four corpora: regulation/circular/compliance pass an '
  'entity_ref, judgment passes cases.entity_id::text (a uuid — no alias exists '
  'for it today, and the text key space makes adding one possible).';


-- ── 2. THE أنظمة RELATION SOURCES ──────────────────────────────────────────
-- Three pipeline-owned tables in the `regulation_v2` schema carry the ENTIRE
-- base axis for أنظمة. Columns read off information_schema on prod 2026-08-22
-- (only the ones this file uses are listed; each table also has `id uuid` and
-- `ingested_at timestamptz`):
--
--   core_subjects           1,821 memberships / 524 distinct core_subject_id
--     core_subject_id text  the cluster key — a PARTITION, every covered نظام
--                           is in exactly one cluster
--     regulation_id   uuid  -> regulation_v2.regulations.id
--     (also representative_core, core_subject_size, threshold — see below)
--
--   core_subject_relations  2,191 unordered pairs, score 0.6–1.0, median 0.862
--     doc_a_id / doc_b_id uuid   -> regulation_v2.regulations.id
--     score               real
--
--   document_relations        746 unordered pairs
--     source_id / target_id uuid -> regulation_v2.regulations.id
--     relation  text  sibling_under_same_law 414 · executive_regulation 330 ·
--                     amendment 2
--     agreement text  both 161 · one_way 585
--
-- ⚠ KEYING IS THE uuid, NOT THE *_ref TEXT. Every one of these tables carries a
--   parallel `*_ref` text column (`reg_ref`, `doc_a_ref`/`doc_b_ref`,
--   `source_ref`/`target_ref`) — the pipeline's own external key
--   («17642_reg_037»). None of them is read here. The uuid is what
--   `seo_item_meta.content_id` is built from, so `::text` on it IS the
--   related_items key with no lookup in between.
--
-- ⚠ BOTH PAIR TABLES STORE ONE ROW PER UNORDERED PAIR (plan §9). Reading only
--   (a,b) silently halves the graph, so both edge views emit (a,b) AND (b,a).
--   If the ingest ever starts writing both directions the duplicate is harmless:
--   every consumer aggregates with max() over the pair.
--
-- ⚠ `representative_core` / `core_subject_size` / `threshold` are deliberately
--   NOT read. The label never renders (D11 — no representative_core in the
--   heading), `threshold` is uniformly 0.6, and `core_subject_size` disagrees
--   with the real membership count on 51 of 524 clusters (plan §9): it is a
--   build-time figure. Count rows, never trust the column.
--
-- ⚠ regulations_v2.parent_law_id (317 rows) is NEVER read. It is fully
--   contained in document_relations (317 ⊂ 746); reading both double-counts and
--   inflates the base.
--
-- The three views below are thin, static projections: rename direction, cast
-- the uuid, drop nothing else. They exist so the scoring query in §7 reads one
-- vocabulary (a, b, …) instead of three, and so a pipeline rename breaks HERE,
-- in one obvious place, rather than eight lines deep inside a CTE.
--
-- An id in a relation table that no longer exists in the corpus (a re-ingest
-- orphan) needs no filtering: §7 inner-joins every candidate to `docs`, so
-- orphans drop out there.
--
-- NOTE ON WHICH REGULATIONS RELATION THIS FILE READS. The relation tables key
-- on `regulation_v2.regulations.id`; §6 and §7 read `public.regulations_v2`,
-- which is a VIEW over that same base table (116 header, 109 T6) and the
-- relation the whole library layer reads. Same 3,951 rows, same ids — reading
-- the view is what keeps this graph and the hub listers describing one corpus.
-- Nothing here ALTERs or indexes either: the regulation_v2 schema is
-- pipeline-owned.

-- 2.1 Preflight. Existence only — the column names below are literal, so a
-- rename surfaces as a plain "column ... does not exist" on the very next
-- statement, which is the loud failure we want. What a bare CREATE VIEW would
-- NOT tell you are the two things this block checks: that you are pointed at a
-- database that has these relations at all, and that the three relation tables
-- are non-empty. An empty core_subjects is not an error to Postgres; it is an
-- أنظمة wing that silently loses its entire base axis and quietly degrades to
-- bonus-only, which is exactly the regression nobody would notice.
do $$
declare
  v_doc integer; v_sub integer; v_mem integer;
begin
  if to_regclass('public.regulations_v2') is null
     or to_regclass('public.circulars')      is null
     or to_regclass('public.service_guides') is null
     or to_regclass('public.services')       is null
     or to_regclass('public.cases')          is null then
    raise exception
      '143: a corpus relation is missing (need public.regulations_v2, '
      'circulars, service_guides, services, cases). Wrong database?';
  end if;

  if to_regclass('regulation_v2.document_relations')      is null
     or to_regclass('regulation_v2.core_subject_relations') is null
     or to_regclass('regulation_v2.core_subjects')          is null then
    raise exception
      '143 §2: regulation_v2.{document_relations, core_subject_relations, '
      'core_subjects} not all present. These carry the ENTIRE base axis for '
      'أنظمة (plan §3.3); without them the wing degrades to bonus-only.';
  end if;

  select count(*) into v_doc from regulation_v2.document_relations;
  select count(*) into v_sub from regulation_v2.core_subject_relations;
  select count(*) into v_mem from regulation_v2.core_subjects;

  if v_doc = 0 or v_sub = 0 or v_mem = 0 then
    raise exception
      '143 §2: a relation table is EMPTY (document_relations %, '
      'core_subject_relations %, core_subjects %). Expected 746 / 2,191 / '
      '1,821 as of 2026-08-22. An empty one is not an error to Postgres — it '
      'is an أنظمة wing with no base axis at all.', v_doc, v_sub, v_mem;
  end if;

  -- Drift is reported, not enforced: these are pipeline-ingested and SHOULD
  -- grow. A hard equality check would turn every re-ingest into a failed
  -- migration. Read the notice — a large swing means the graph changed shape.
  raise notice '143 §2 inputs: document_relations % (2026-08-22: 746) · '
               'core_subject_relations % (2,191) · core_subjects % (1,821)',
               v_doc, v_sub, v_mem;
end $$;

-- 2.2 The three projections.

create or replace view public.related_reg_document_edges as
  select d.source_id::text as a, d.target_id::text as b,
         d.relation, d.agreement
    from regulation_v2.document_relations d
   where d.source_id is not null and d.target_id is not null
     and d.source_id <> d.target_id
  union all
  select d.target_id::text, d.source_id::text,
         d.relation, d.agreement
    from regulation_v2.document_relations d
   where d.source_id is not null and d.target_id is not null
     and d.source_id <> d.target_id;

comment on view public.related_reg_document_edges is
  'regulation_v2.document_relations projected BOTH directions, keyed on '
  'regulations_v2.id::text. `agreement` (both|one_way) drives the 5.0 vs 3.5 '
  'base split — that split is how plan D10''s skepticism enters the arithmetic, '
  'since 585 of 746 rows are single-method guesses. `relation` is carried for '
  'audit and is never rendered and never scored.';

create or replace view public.related_reg_subject_edges as
  select s.doc_a_id::text as a, s.doc_b_id::text as b, s.score
    from regulation_v2.core_subject_relations s
   where s.doc_a_id is not null and s.doc_b_id is not null
     and s.doc_a_id <> s.doc_b_id
  union all
  select s.doc_b_id::text, s.doc_a_id::text, s.score
    from regulation_v2.core_subject_relations s
   where s.doc_a_id is not null and s.doc_b_id is not null
     and s.doc_a_id <> s.doc_b_id;

comment on view public.related_reg_subject_edges is
  'regulation_v2.core_subject_relations projected BOTH directions, keyed on '
  'regulations_v2.id::text. score 0.6–1.0 (median 0.862) maps to base '
  '[1.5, 3.0]. Its own core_subject_id column is not projected: cluster '
  'membership is read from core_subjects, one source per fact.';

-- distinct because a duplicated membership row would fan the cluster
-- self-join out; count(*) over this view is also the only trustworthy cluster
-- size (core_subject_size is wrong on 51 of 524).
create or replace view public.related_reg_subject_members as
  select distinct m.regulation_id::text as reg_id, m.core_subject_id
    from regulation_v2.core_subjects m
   where m.regulation_id is not null
     and m.core_subject_id is not null;

comment on view public.related_reg_subject_members is
  'regulation_v2.core_subjects cluster membership, keyed on '
  'regulations_v2.id::text. A partition: every covered نظام is in exactly one '
  'cluster. Co-membership with no relation row = base 1.2.';


-- ── 3. related_axis_weights — the scarcity table (plan §4.1) ───────────────
-- Materialized so the weights are auditable and so the refresh function is not
-- recomputing n_v per pair. It is also what makes the candidate guards in §7
-- possible at all: they are a filter ON a weight, so the weight has to exist
-- before candidate generation starts.

create table if not exists public.related_axis_weights (
  corpus   text    not null check (corpus in ('regulation','compliance','circular','judgment')),
  axis     text    not null check (axis in ('sector','entity','court')),
  value    text    not null,
  n        integer not null,
  weight   real    not null,
  built_at timestamptz not null default now(),
  primary key (corpus, axis, value)
);

comment on table public.related_axis_weights is
  'Scarcity weight per (corpus, axis, value): w = least(1, (TARGET_SHARE*N/n)^2). '
  'Counted over the FULL corpus, never the slugged subset, so publishing never '
  'shifts a weight (plan D5/§3.1). Rebuilt whole by '
  'refresh_related_axis_weights().';
comment on column public.related_axis_weights.value is
  'sector: the Arabic sector tag (one clean 38-value vocabulary across all four '
  'corpora — verified 2026-08-22, circulars 37 / services 38 / cases '
  'legal_domains 36 are strict subsets, so set intersection is safe with no '
  'normalization). entity: entity_ref for regulation/circular/compliance, '
  'cases.entity_id::text (a uuid) for judgment — the key spaces differ and that '
  'is fine, matching only ever happens within one corpus (D2). court: '
  'cases.court, judgment only.';
comment on column public.related_axis_weights.n is
  'Documents in the FULL corpus carrying this value. Audit column — the weight '
  'is already computed. Judgment counts here are ~3x the plan §2 table, which '
  'was measured over the 10,000 slugged rows.';


-- ── 4. related_items — the edge store (plan §4.2) ──────────────────────────

create table if not exists public.related_items (
  source_type text not null check (source_type in ('regulation','compliance','circular','judgment')),
  source_id   text not null,          -- matches seo_item_meta.content_id (uuid::text)
  target_type text not null check (target_type in ('regulation','compliance','circular','judgment')),
  target_id   text not null,
  score       real not null,
  base        real not null default 0,
  bonus       real not null default 0,
  reason      text not null,          -- audit only, never rendered (D10)
  built_at    timestamptz not null default now(),
  primary key (source_type, source_id, target_type, target_id),
  constraint related_items_no_self check (source_id <> target_id or source_type <> target_type)
);

create index if not exists idx_related_items_lookup
  on public.related_items (source_type, source_id, score desc);

comment on table public.related_items is
  'Precomputed «اقرأ تاليًا» graph. Same-type only (D2). Stored in BOTH '
  'directions. NO publish state and NO stored rank: rank changes with every '
  'publish, so the reader joins seo_item_meta and orders by score desc (D5/D6). '
  'Rebuilt per corpus by refresh_related_items(); weekly on pg_cron.';
comment on column public.related_items.source_id is
  'seo_item_meta.content_id key space: regulations_v2.id / circulars.id / '
  'service_guides.id / cases.id, all ::text. NOT reg_ref, NOT a slug.';
comment on column public.related_items.base is
  'Relation/topic evidence (plan §3.3). 0 for a bonus-only edge — the backend''s '
  '"at most 2 of the 7 may have base = 0" guard reads THIS column, and that '
  'guard is off for judgment where every edge is bonus-only by construction.';
comment on column public.related_items.reason is
  'document_relation_both | document_relation_one_way | core_subject_relation | '
  'core_subject_member | topic_bm25 | bonus_only. Audit and Wave D sampling '
  'only — D10 forbids rendering a relation-type chip, because 585 of 746 '
  'document_relations rows are single-method guesses.';


-- ── 5. RLS and grants (plan §4.3) ──────────────────────────────────────────
-- RLS on, NO policies, grants revoked. The backend reads with the service role;
-- the frontend never talks to Supabase for library data.
--
-- ⚠ This is not boilerplate. There is an OPEN finding that the anon key can
--   read corpus tables directly through PostgREST
--   (project_anon_postgrest_corpus_exposure). A permissive policy added here
--   "so the frontend can read it" would put a walkable id-to-id map of the
--   entire corpus — published AND unpublished, since D5 keeps publish state out
--   of this table — on the public PostgREST endpoint, routing around both the
--   publish filter and the enumeration meter.

alter table public.related_items        enable row level security;
alter table public.related_axis_weights enable row level security;
alter table public.related_entity_aliases enable row level security;

revoke all on public.related_items, public.related_axis_weights,
              public.related_entity_aliases
  from public, anon, authenticated;

revoke all on public.related_reg_document_edges,
              public.related_reg_subject_edges,
              public.related_reg_subject_members
  from public, anon, authenticated;

-- Reads only. Every write goes through the SECURITY DEFINER refresh functions.
grant select on public.related_items, public.related_axis_weights,
                public.related_entity_aliases
  to service_role;


-- ── 6. refresh_related_axis_weights() (plan §4.4) ──────────────────────────
-- Rebuilds all three axes for all four corpora from FULL-corpus counts.
--
-- delete-then-insert rather than the plan's `on conflict do update`: an upsert
-- leaves a stale row behind forever when a value disappears from a corpus, and
-- a stale weight is worse than a missing one — the candidate generators read
-- this table, so a phantom rare value would keep generating pairs for documents
-- that no longer carry it. The table is ~200 rows.
--
-- Axis coverage is deliberate and not uniform:
--   sector  regulation, circular, compliance      (خدمات inherit from services)
--   entity  all four
--   court   judgment only — أحكام have no sectors column, and per plan §3.2
--           court SUBSTITUTES for the sector axis there. cases.legal_domains
--           exists and is deliberately unused: court is the axis that rescues
--           the judgment tail, legal_domains would just add noise on top of a
--           corpus where 68% of rows share one entity.

create or replace function public.refresh_related_axis_weights()
returns integer
language plpgsql
security definer
set search_path = public
set statement_timeout = '300s'
as $$
declare
  v_count integer := 0;
begin
  delete from public.related_axis_weights;

  insert into public.related_axis_weights (corpus, axis, value, n, weight, built_at)
  with
  -- N per corpus, joined in at the end rather than carried through every
  -- branch. is_canonical is the compliance corpus's own dedupe flag (portal
  -- aliases point several refs at one guide, migration 142 §2);
  -- coalesce(...,true) rather than a bare predicate, so a NULL on a freshly
  -- ingested row cannot silently shrink N and inflate every خدمات weight.
  sizes as (
      select 'regulation'::text as corpus, count(*)::double precision as n
        from public.regulations_v2
    union all
      select 'circular', count(*)::double precision from public.circulars
    union all
      select 'compliance', count(*)::double precision
        from public.service_guides g where coalesce(g.is_canonical, true)
    union all
      select 'judgment', count(*)::double precision from public.cases
  ),
  raw as (
    -- regulation · sector
      select 'regulation'::text as corpus, 'sector'::text as axis, s.v as value,
             count(*)::int as n
        from public.regulations_v2 r, unnest(coalesce(r.sectors, '{}'::text[])) s(v)
       where nullif(btrim(s.v), '') is not null
       group by s.v
    union all
    -- regulation · entity (canonicalized — §1)
      select 'regulation', 'entity',
             public.related_canonical_entity(nullif(btrim(r.entity_ref::text), '')),
             count(*)::int
        from public.regulations_v2 r
       where nullif(btrim(r.entity_ref::text), '') is not null
       group by 3
    union all
    -- circular · sector
      select 'circular', 'sector', s.v, count(*)::int
        from public.circulars ci, unnest(coalesce(ci.sectors, '{}'::text[])) s(v)
       where nullif(btrim(s.v), '') is not null
       group by s.v
    union all
    -- circular · entity
      select 'circular', 'entity',
             public.related_canonical_entity(nullif(btrim(ci.entity_ref::text), '')),
             count(*)::int
        from public.circulars ci
       where nullif(btrim(ci.entity_ref::text), '') is not null
       group by 3
    union all
    -- compliance · sector — خدمات carry no sectors of their own; they inherit
    -- from the service the guide documents (service_guides.service_id ->
    -- services.sectors), which is exactly what library_compliance_v does.
      select 'compliance', 'sector', s.v, count(*)::int
        from public.service_guides g
        join public.services sv on sv.id = g.service_id,
             unnest(coalesce(sv.sectors, '{}'::text[])) s(v)
       where coalesce(g.is_canonical, true)
         and nullif(btrim(s.v), '') is not null
       group by s.v
    union all
    -- compliance · entity (lives on the guide, not on the service)
      select 'compliance', 'entity',
             public.related_canonical_entity(nullif(btrim(g.entity_ref::text), '')),
             count(*)::int
        from public.service_guides g
       where coalesce(g.is_canonical, true)
         and nullif(btrim(g.entity_ref::text), '') is not null
       group by 3
    union all
    -- judgment · court
      select 'judgment', 'court', btrim(c.court), count(*)::int
        from public.cases c
       where nullif(btrim(c.court), '') is not null
       group by 3
    union all
    -- judgment · entity — a uuid FK to entities.id, NOT an entity_ref text key.
    -- Kept as the raw uuid text: it is stable, it needs no join, and Wave D can
    -- resolve names by joining `entities` when it dumps samples.
      select 'judgment', 'entity',
             public.related_canonical_entity(c.entity_id::text),
             count(*)::int
        from public.cases c
       where c.entity_id is not null
       group by 3
  )
  select r.corpus, r.axis, r.value, r.n,
         -- w = least(1, (TARGET_SHARE * N / n)^2)   — §0
         least(1.0,
               power(public.related_target_share()::double precision * z.n
                     / nullif(r.n, 0)::double precision, 2))::real,
         now()
    from raw r
    join sizes z on z.corpus = r.corpus
   where r.value is not null;

  get diagnostics v_count = row_count;
  return v_count;
end $$;

comment on function public.refresh_related_axis_weights() is
  'Rebuilds public.related_axis_weights from FULL-corpus counts. Must run '
  'BEFORE refresh_related_items() — the candidate generators filter on these '
  'weights, so an empty weights table produces an empty graph (silently, not '
  'with an error).';


-- ── 7. refresh_related_items(p_corpus) (plan §4.5) ─────────────────────────
-- SECURITY DEFINER, returns rows written, one branch per corpus, delete then
-- re-insert that corpus, raise on anything else. Same shape as
-- refresh_search_index() in 111.
--
-- 7.1 THE SCORE (plan §3), identical in every branch:
--     mult(k)     = k*(k+1)/2                     -- 1, 3, 6, 10 …
--     sector_term = least(1.0, mult(k) * Σ w(s))  -- over the k SHARED sectors
--     entity_term = w(entity) when it matches, else 0
--     bonus       = least(2.0, entity_term + sector_term)
--     score       = base + bonus                  -- stored iff >= FLOOR
--   mult(k) is super-linear on purpose: sharing two sectors is far rarer than
--   sharing either one. The least(1.0, …) on sector_term is REQUIRED — without
--   it three rare shared sectors give 6 * 2.5 = 15 and the weakest axis
--   outranks the strongest.
--   The bands overlap by design (plan §3.5): 1.2 + 2.0 > 3.0. Intended. The
--   one-line reversal, if Wave D wants it, is to lower the bonus cap to 1.0.
--
-- 7.2 CANDIDATE GENERATION — the part that must not become a cross join.
--   Generators per corpus:
--     regulation  document_relations · core_subject_relations · cluster
--                 co-membership · entity · sector
--     circular    entity · sector   (+ topic, Wave E seam)
--     compliance  entity · sector   (+ topic, Wave E seam)
--     judgment    court · entity
--   The entity/sector/court generators fire only where that axis ALONE reaches
--   related_gen_floor() = FLOOR/2, which is lossless (see §0). The sector
--   generator additionally goes through an exploded (doc, sector) inverted
--   index rather than a corpus self-join: its cost is Σ_s n_s², ~1.5M row pairs
--   on أنظمة against 15.6M for the naive join, and the threshold on the
--   aggregated sector_term drops all but a fraction of that before scoring.
--
-- 7.3 WAVE E is not in scope. تعاميم and خدمات run BONUS-ONLY. Each of those two
--   branches carries an explicitly marked, currently-empty `topic_base` CTE
--   with its contract written out; wiring the topic-BM25 base is an edit to
--   that one CTE and nothing else.
--
-- 7.4 EXPECTED SHAPE (plan §3.6): أنظمة good · تعاميم/خدمات thin until Wave E ·
--   أحكام no strip on ~75% of pages, because 7,483 of 10,000 slugged judgments
--   sit in التجارية where the court is worth ~0.0001. That is the intended
--   outcome. The tail courts DO form near-cliques (every pair inside a small
--   court clears the floor), so the judgment branch is the one place where
--   degree is high and the row count reaches ~10^5. It is bounded by the court
--   sizes, not by the corpus size.

create or replace function public.refresh_related_items(p_corpus text)
returns integer
language plpgsql
security definer
set search_path = public
-- The judgment branch aggregates millions of candidate pairs. If it ever runs
-- long, `work_mem` is the knob (raise to 128MB) — not this timeout.
--
-- ⚠ THIS CLAUSE DOES NOT PROTECT THIS FUNCTION'S OWN BODY, learned the hard way
--   on 2026-08-23. statement_timeout is armed when the OUTER statement starts,
--   from the value in force at that moment; a function-local SET changes the
--   setting for later statements, never for the one already running. So
--   `select public.refresh_related_items('judgment')` is bounded by the
--   CALLER's timeout — 2 minutes under Supabase's pooler, which killed the
--   first two attempts at 120.0s with this 900s clause sitting right here.
--   The judgment backfill takes ~13 MINUTES (12:56 measured, 1,456,576 rows),
--   so the caller must raise it. What worked, as a pg_cron one-shot — server
--   side, so no client can time the connection out from under it:
--
--     select cron.schedule('related_items_judgment_oneshot', '<MM> <HH> * * *',
--       $c$ set statement_timeout = '900s';
--           set work_mem = '256MB';
--           select public.refresh_related_items('judgment'); $c$);
--     -- then cron.unschedule() it once cron.job_run_details says succeeded.
--
--   Plain `SET`, never `SET LOCAL` (outside a transaction block that is a
--   no-op with a warning). The clause below is kept because it is the right
--   ceiling for the weekly cron job, whose session inherits no shorter one.
set statement_timeout = '900s'
as $$
declare
  v_count integer := 0;
begin

  -- ══ أنظمة ════════════════════════════════════════════════════════════════
  if p_corpus = 'regulation' then
    delete from public.related_items where source_type = 'regulation';

    insert into public.related_items
      (source_type, source_id, target_type, target_id, score, base, bonus, reason)
    with
    docs as (
      select r.id::text as id,
             public.related_canonical_entity(nullif(btrim(r.entity_ref::text), '')) as entity,
             coalesce(r.sectors, '{}'::text[]) as sectors
        from public.regulations_v2 r
    ),
    ds as (   -- the (doc, sector) inverted index; see 7.2
      select d.id, s.v as sector
        from docs d, unnest(d.sectors) s(v)
       where nullif(btrim(s.v), '') is not null
    ),
    sw as (select w.value, w.weight from public.related_axis_weights w
            where w.corpus = 'regulation' and w.axis = 'sector'),
    ew as (select w.value, w.weight from public.related_axis_weights w
            where w.corpus = 'regulation' and w.axis = 'entity'),

    -- ── base sources (plan §3.3), max() over any duplicate edge row ────────
    b_doc as (
      select e.a, e.b,
             max(case when e.agreement = 'both' then 5.0 else 3.5 end)::real as base
        from public.related_reg_document_edges e
       group by e.a, e.b
    ),
    b_sub as (
      -- 1.5 + 1.5*(score-0.6)/0.4 -> [1.5, 3.0]. Clamped both ends: the ingest
      -- threshold is 0.6 today, and a future re-run at a lower threshold must
      -- not push the base under the 1.2 cluster tier.
      select e.a, e.b,
             least(3.0, greatest(1.5,
               1.5 + 1.5 * (max(e.score) - 0.6) / 0.4))::real as base
        from public.related_reg_subject_edges e
       group by e.a, e.b
    ),
    b_clu as (
      select distinct m1.reg_id as a, m2.reg_id as b, 1.2::real as base
        from public.related_reg_subject_members m1
        join public.related_reg_subject_members m2
          on m2.core_subject_id = m1.core_subject_id
         and m2.reg_id <> m1.reg_id
    ),

    -- ── candidates ─────────────────────────────────────────────────────────
    cand as (
      select u.a, u.b from (
          select a, b from b_doc
        union
          select a, b from b_sub
        union
          select a, b from b_clu
        union
          -- entity generator, guarded (§0). On أنظمة this fires only for
          -- entities with n <= 100 documents, so the four biggest issuing
          -- authorities generate nothing at all.
          select d1.id as a, d2.id as b
            from docs d1
            join ew on ew.value = d1.entity
                   and ew.weight >= public.related_gen_floor()
            join docs d2 on d2.entity = d1.entity and d2.id <> d1.id
        union
          -- sector generator, guarded by the FULL sector_term (the HAVING is
          -- the guard; a pair only survives if its shared sectors alone carry
          -- half the floor).
          select p.a, p.b from (
            select x.id as a, y.id as b,
                   least(1.0, (count(*) * (count(*) + 1) / 2.0) * sum(sw.weight))
                     as sector_term
              from ds x
              join ds y  on y.sector = x.sector and y.id <> x.id
              join sw    on sw.value = x.sector
             group by x.id, y.id
          ) p
          where p.sector_term >= public.related_gen_floor()
      ) u
      where u.a is not null and u.b is not null and u.a <> u.b
    ),

    -- Shared sectors for every candidate pair, whatever generated it. Note
    -- this uses ALL shared sectors, including ones far below the generation
    -- threshold: generation filters the GENERATOR, scoring uses everything.
    pair_sec as (
      select c.a, c.b, count(*)::int as k,
             sum(sw.weight)::double precision as w_sum
        from cand c
        join ds x on x.id = c.a
        join ds y on y.id = c.b and y.sector = x.sector
        join sw   on sw.value = x.sector
       group by c.a, c.b
    ),

    scored as (
      select c.a, c.b,
             bd.base as base_doc, bs.base as base_sub, bc.base as base_clu,
             least(2.0,
                   (case when da.entity is not null and da.entity = db.entity
                         then coalesce(ew.weight, 0) else 0 end)
                 + coalesce(least(1.0, (ps.k * (ps.k + 1) / 2.0) * ps.w_sum), 0)
             )::real as bonus
        from cand c
        join docs da on da.id = c.a
        join docs db on db.id = c.b
        left join b_doc    bd on bd.a = c.a and bd.b = c.b
        left join b_sub    bs on bs.a = c.a and bs.b = c.b
        left join b_clu    bc on bc.a = c.a and bc.b = c.b
        left join pair_sec ps on ps.a = c.a and ps.b = c.b
        left join ew          on ew.value = da.entity and da.entity = db.entity
    ),
    final as (
      -- greatest() ignores NULLs, so this is "the max of whichever apply".
      select s.a, s.b,
             coalesce(greatest(s.base_doc, s.base_sub, s.base_clu), 0)::real as base,
             s.bonus,
             case
               when coalesce(greatest(s.base_doc, s.base_sub, s.base_clu), 0) = 0
                 then 'bonus_only'
               when s.base_doc is not null
                    and s.base_doc >= coalesce(s.base_sub, 0)
                    and s.base_doc >= coalesce(s.base_clu, 0)
                 then case when s.base_doc >= 5.0 then 'document_relation_both'
                           else 'document_relation_one_way' end
               when s.base_sub is not null and s.base_sub >= coalesce(s.base_clu, 0)
                 then 'core_subject_relation'
               else 'core_subject_member'
             end as reason
        from scored s
    )
    select 'regulation', f.a, 'regulation', f.b,
           (f.base + f.bonus)::real, f.base, f.bonus, f.reason
      from final f
     where (f.base + f.bonus) >= public.related_floor();

  -- ══ تعاميم ═══════════════════════════════════════════════════════════════
  elsif p_corpus = 'circular' then
    delete from public.related_items where source_type = 'circular';

    insert into public.related_items
      (source_type, source_id, target_type, target_id, score, base, bonus, reason)
    with
    docs as (
      -- circulars.entity_ref is this corpus's own key space (5 distinct values,
      -- 296–619 docs each — every one of them is worth ~0 after §0's fix, which
      -- is the correct reading of "both issued by the same ministry").
      select ci.id::text as id,
             public.related_canonical_entity(nullif(btrim(ci.entity_ref::text), '')) as entity,
             coalesce(ci.sectors, '{}'::text[]) as sectors
        from public.circulars ci
    ),
    ds as (
      select d.id, s.v as sector
        from docs d, unnest(d.sectors) s(v)
       where nullif(btrim(s.v), '') is not null
    ),
    sw as (select w.value, w.weight from public.related_axis_weights w
            where w.corpus = 'circular' and w.axis = 'sector'),
    ew as (select w.value, w.weight from public.related_axis_weights w
            where w.corpus = 'circular' and w.axis = 'entity'),

    -- ══════════════════ WAVE E SEAM — topic-BM25 base ══════════════════════
    -- تعاميم have no citations, no clusters and 5 entity values; one topic
    -- sentence per تعميم (search_topics source_type='circular', 2,119 rows,
    -- 1.15/doc) is the only content signal this corpus will ever have. Until
    -- Wave E ships it, this corpus is BONUS-ONLY and this CTE is empty.
    --
    -- CONTRACT for the replacement: (a text, b text, base real), one row per
    -- ORDERED pair, BOTH directions, base = 3.0 * bm25 / max_bm25_for_this_
    -- source, i.e. (0, 3.0] normalized per source (plan §3.3 — relative
    -- because BM25 is unbounded and its scale varies with corpus statistics).
    -- Nothing else in this branch changes: `cand` already unions it, `final`
    -- already left-joins it, and `reason` already flips to 'topic_bm25'.
    -- ⚠ Wave E must ALSO emit its own candidates — the axis generators are only
    -- lossless for base = 0 pairs (see §0), so a topic pair whose base carries
    -- it over the floor has to be generated by the topic side.
    topic_base as (
      select null::text as a, null::text as b, null::real as base where false
    ),
    -- ═══════════════════════════════════════════════════════════════════════

    cand as (
      select u.a, u.b from (
          select a, b from topic_base
        union
          select d1.id as a, d2.id as b
            from docs d1
            join ew on ew.value = d1.entity
                   and ew.weight >= public.related_gen_floor()
            join docs d2 on d2.entity = d1.entity and d2.id <> d1.id
        union
          select p.a, p.b from (
            select x.id as a, y.id as b,
                   least(1.0, (count(*) * (count(*) + 1) / 2.0) * sum(sw.weight))
                     as sector_term
              from ds x
              join ds y  on y.sector = x.sector and y.id <> x.id
              join sw    on sw.value = x.sector
             group by x.id, y.id
          ) p
          where p.sector_term >= public.related_gen_floor()
      ) u
      where u.a is not null and u.b is not null and u.a <> u.b
    ),
    pair_sec as (
      select c.a, c.b, count(*)::int as k,
             sum(sw.weight)::double precision as w_sum
        from cand c
        join ds x on x.id = c.a
        join ds y on y.id = c.b and y.sector = x.sector
        join sw   on sw.value = x.sector
       group by c.a, c.b
    ),
    final as (
      select c.a, c.b,
             coalesce(tb.base, 0)::real as base,
             least(2.0,
                   (case when da.entity is not null and da.entity = db.entity
                         then coalesce(ew.weight, 0) else 0 end)
                 + coalesce(least(1.0, (ps.k * (ps.k + 1) / 2.0) * ps.w_sum), 0)
             )::real as bonus,
             case when coalesce(tb.base, 0) > 0 then 'topic_bm25'
                  else 'bonus_only' end as reason
        from cand c
        join docs da on da.id = c.a
        join docs db on db.id = c.b
        left join topic_base tb on tb.a = c.a and tb.b = c.b
        left join pair_sec   ps on ps.a = c.a and ps.b = c.b
        left join ew            on ew.value = da.entity and da.entity = db.entity
    )
    select 'circular', f.a, 'circular', f.b,
           (f.base + f.bonus)::real, f.base, f.bonus, f.reason
      from final f
     where (f.base + f.bonus) >= public.related_floor();

  -- ══ خدمات ════════════════════════════════════════════════════════════════
  elsif p_corpus = 'compliance' then
    delete from public.related_items where source_type = 'compliance';

    insert into public.related_items
      (source_type, source_id, target_type, target_id, score, base, bonus, reason)
    with
    docs as (
      -- LEFT join to services on purpose: the entity axis and the corpus
      -- membership live on the guide, only the sectors are inherited. A guide
      -- whose service row vanished loses its sectors, not its existence — the
      -- opposite of library_compliance_v's inner join, which is about what is
      -- RENDERABLE. Key space is service_guides.id::text (migration 142), NOT
      -- services.id: the retired 'service' wing left 4,717 rows in the sidecar
      -- under that other key and they are a different corpus entirely.
      select g.id::text as id,
             public.related_canonical_entity(nullif(btrim(g.entity_ref::text), '')) as entity,
             coalesce(sv.sectors, '{}'::text[]) as sectors
        from public.service_guides g
        left join public.services sv on sv.id = g.service_id
       where coalesce(g.is_canonical, true)
    ),
    ds as (
      select d.id, s.v as sector
        from docs d, unnest(d.sectors) s(v)
       where nullif(btrim(s.v), '') is not null
    ),
    sw as (select w.value, w.weight from public.related_axis_weights w
            where w.corpus = 'compliance' and w.axis = 'sector'),
    ew as (select w.value, w.weight from public.related_axis_weights w
            where w.corpus = 'compliance' and w.axis = 'entity'),

    -- ══════════════════ WAVE E SEAM — topic-BM25 base ══════════════════════
    -- Same contract as the تعاميم branch above: (a, b, base) in (0, 3.0],
    -- both directions, normalized per source. Source table for خدمات is
    -- search_topics source_type='service' (6,712 rows, ~1.4/service) joined
    -- through service_guides.service_id. BONUS-ONLY until then.
    topic_base as (
      select null::text as a, null::text as b, null::real as base where false
    ),
    -- ═══════════════════════════════════════════════════════════════════════

    cand as (
      select u.a, u.b from (
          select a, b from topic_base
        union
          select d1.id as a, d2.id as b
            from docs d1
            join ew on ew.value = d1.entity
                   and ew.weight >= public.related_gen_floor()
            join docs d2 on d2.entity = d1.entity and d2.id <> d1.id
        union
          select p.a, p.b from (
            select x.id as a, y.id as b,
                   least(1.0, (count(*) * (count(*) + 1) / 2.0) * sum(sw.weight))
                     as sector_term
              from ds x
              join ds y  on y.sector = x.sector and y.id <> x.id
              join sw    on sw.value = x.sector
             group by x.id, y.id
          ) p
          where p.sector_term >= public.related_gen_floor()
      ) u
      where u.a is not null and u.b is not null and u.a <> u.b
    ),
    pair_sec as (
      select c.a, c.b, count(*)::int as k,
             sum(sw.weight)::double precision as w_sum
        from cand c
        join ds x on x.id = c.a
        join ds y on y.id = c.b and y.sector = x.sector
        join sw   on sw.value = x.sector
       group by c.a, c.b
    ),
    final as (
      select c.a, c.b,
             coalesce(tb.base, 0)::real as base,
             least(2.0,
                   (case when da.entity is not null and da.entity = db.entity
                         then coalesce(ew.weight, 0) else 0 end)
                 + coalesce(least(1.0, (ps.k * (ps.k + 1) / 2.0) * ps.w_sum), 0)
             )::real as bonus,
             case when coalesce(tb.base, 0) > 0 then 'topic_bm25'
                  else 'bonus_only' end as reason
        from cand c
        join docs da on da.id = c.a
        join docs db on db.id = c.b
        left join topic_base tb on tb.a = c.a and tb.b = c.b
        left join pair_sec   ps on ps.a = c.a and ps.b = c.b
        left join ew            on ew.value = da.entity and da.entity = db.entity
    )
    select 'compliance', f.a, 'compliance', f.b,
           (f.base + f.bonus)::real, f.base, f.bonus, f.reason
      from final f
     where (f.base + f.bonus) >= public.related_floor();

  -- ══ أحكام ════════════════════════════════════════════════════════════════
  -- No base axis at all (plan §3.3): base = 0.0 always, court carries the
  -- signal through the bonus. bonus = least(2.0, entity_term + w(court)).
  --
  -- ⚠ THIS IS THE BRANCH THE GUARDS EXIST FOR. An unguarded court self-join is
  --   932M pairs. The generation threshold admits only courts and entities with
  --   w >= FLOOR/2, i.e. n <= ~780 of 30,531 — so التجارية (~22k rows, w ~1e-4)
  --   and وزارة العدل (20,671, w 0.0002) generate NOTHING, which is also why
  --   ~75% of judgment pages will correctly show no strip.
  elsif p_corpus = 'judgment' then
    delete from public.related_items where source_type = 'judgment';

    insert into public.related_items
      (source_type, source_id, target_type, target_id, score, base, bonus, reason)
    with
    docs as (
      select c.id::text as id,
             public.related_canonical_entity(c.entity_id::text) as entity,
             nullif(btrim(c.court), '') as court
        from public.cases c
    ),
    cw as (select w.value, w.weight from public.related_axis_weights w
            where w.corpus = 'judgment' and w.axis = 'court'),
    ew as (select w.value, w.weight from public.related_axis_weights w
            where w.corpus = 'judgment' and w.axis = 'entity'),
    cand as (
      select u.a, u.b from (
          -- court generator. w(court) >= FLOOR/2 means n <= ~780 of 30,531, so
          -- التجارية and وزارة العدل never enter — which is the whole reason
          -- this is 10^5 rows and not 932M.
          select d1.id as a, d2.id as b
            from docs d1
            join cw on cw.value = d1.court
                   and cw.weight >= public.related_gen_floor()
            join docs d2 on d2.court = d1.court and d2.id <> d1.id
        union
          select d1.id as a, d2.id as b
            from docs d1
            join ew on ew.value = d1.entity
                   and ew.weight >= public.related_gen_floor()
            join docs d2 on d2.entity = d1.entity and d2.id <> d1.id
      ) u
      where u.a is not null and u.b is not null and u.a <> u.b
    ),
    final as (
      select c.a, c.b,
             0::real as base,
             least(2.0,
                   (case when da.entity is not null and da.entity = db.entity
                         then coalesce(ew.weight, 0) else 0 end)
                 + (case when da.court is not null and da.court = db.court
                         then coalesce(cw.weight, 0) else 0 end)
             )::real as bonus,
             'bonus_only'::text as reason
        from cand c
        join docs da on da.id = c.a
        join docs db on db.id = c.b
        left join ew on ew.value = da.entity and da.entity = db.entity
        left join cw on cw.value = da.court  and da.court  = db.court
    )
    select 'judgment', f.a, 'judgment', f.b,
           (f.base + f.bonus)::real, f.base, f.bonus, f.reason
      from final f
     where (f.base + f.bonus) >= public.related_floor();

  else
    raise exception 'refresh_related_items: unknown corpus %', p_corpus;
  end if;

  get diagnostics v_count = row_count;
  return v_count;
end $$;

comment on function public.refresh_related_items(text) is
  'Rebuilds public.related_items for one corpus (regulation|circular|compliance'
  '|judgment) and returns the row count. Reads related_axis_weights, so '
  'refresh_related_axis_weights() must have run at least once. Same-type edges '
  'only (D2), both directions, everything above related_floor() — no top-N, no '
  'publish filter, no rank (D5/D6).';

revoke all on function public.refresh_related_items(text)      from public, anon, authenticated;
revoke all on function public.refresh_related_axis_weights()   from public, anon, authenticated;
revoke all on function public.related_canonical_entity(text)   from public, anon, authenticated;
-- The knobs stay executable: they are three constants, and Wave D reads them
-- while sampling.
grant execute on function public.related_floor()         to service_role;
grant execute on function public.related_target_share()  to service_role;
grant execute on function public.related_gen_floor()     to service_role;


-- ── 8. Scheduled refresh (plan §4.6) ───────────────────────────────────────
-- Weekly, Sunday 03:40, offset from 111's nightly BM25 job at 02:20. Weekly is
-- safe because every input (core_subjects, document_relations, sectors,
-- entities, courts) is pipeline-ingested and changes rarely — and because
-- publish state is NOT in the table, so a newly slugged item appears in
-- everyone's strip within the 24h ISR window without waiting for Sunday (D5).
--
-- Guarded exactly like 111:695 — check pg_extension, unschedule any existing
-- job of the same name, then schedule. The unschedule is what makes a re-apply
-- idempotent; cron.schedule() on an existing jobname would otherwise stack.

do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    if exists (select 1 from cron.job where jobname = 'related_items_refresh_weekly') then
      perform cron.unschedule('related_items_refresh_weekly');
    end if;

    perform cron.schedule(
      'related_items_refresh_weekly', '40 3 * * 0',
      $cron$
        select public.refresh_related_axis_weights();
        select public.refresh_related_items('regulation');
        select public.refresh_related_items('circular');
        select public.refresh_related_items('compliance');
        select public.refresh_related_items('judgment');
      $cron$);
  end if;
end $$;


-- ── 9. Backfill ────────────────────────────────────────────────────────────
-- Run once at apply time, same as 111 does. Weights FIRST — the candidate
-- generators filter on them, so running these out of order produces an empty
-- graph with no error at all.
--
-- Wave D (plan §7) starts from here: score histogram per corpus, count of
-- sources with 0 / 1–2 / 3–6 / 7+ candidates AFTER the publish filter, then 20
-- random sources per corpus read by a human. Expected going in: أنظمة ~50% with
-- >= 1 candidate, تعاميم/خدمات thin (bonus-only), أحكام ~25%.

select public.refresh_related_axis_weights();
select public.refresh_related_items('regulation');
select public.refresh_related_items('circular');
select public.refresh_related_items('compliance');
select public.refresh_related_items('judgment');


-- ── 10. Self-verification ──────────────────────────────────────────────────
-- Four invariants the file above only asserts in prose. All four have a way of
-- regressing SILENTLY — none of them would raise on its own, they would just
-- produce a quietly wrong strip:
--   (a) the entity collapse (§1) not taking effect — the map read as empty
--       returns a no-op resolver and nobody notices;
--   (b) an empty weights table, which makes every generator's guard reject
--       everything and yields a graph of zero rows with no error;
--   (c) a stored edge that violates the floor / self / same-type invariants;
--   (d) the cron job not registered, so the graph freezes at today's corpus.
-- Prints the row counts either way — those are the plan §10 numbers to record.

do $$
declare
  v_n5000  integer;
  v_stray  integer;
  v_w      integer;
  v_bad    integer;
  v_stats  text;
begin
  -- (a) The entity split-brain. 5000 + 17573 + 40002 = 1,286 of 3,951 أنظمة
  --     (33%) as of 2026-08-22. If the collapse did not happen, '5000' reads
  --     ~920 AND a separate '17573' row exists.
  select n into v_n5000 from public.related_axis_weights
   where corpus = 'regulation' and axis = 'entity' and value = '5000';
  select count(*) into v_stray from public.related_axis_weights
   where corpus = 'regulation' and axis = 'entity' and value in ('17573','40002');

  if v_stray > 0 or coalesce(v_n5000, 0) < 1000 then
    raise exception
      '143 §1: the entity split-brain was NOT collapsed (entity_ref 5000 n = %, '
      'stray 17573/40002 rows = %). Expected n >= 1,286 and 0 strays. Three '
      'things do this: (1) the §1 INSERT did not land; (2) related_entity_'
      'aliases is unreadable — with RLS on, that happens when the refresh '
      'functions are owned by a role that is not the table owner; (3) '
      'regulations_v2.entity_ref is not the numeric-code key space this map '
      'assumes, in which case re-key §1 against whatever it actually holds '
      '— the split is real either way. Fix, then re-run '
      'refresh_related_axis_weights() and all four refresh_related_items().',
      coalesce(v_n5000::text, '<no row>'), v_stray;
  end if;

  -- (b) Weights present for every axis that should have one.
  select count(*) into v_w from public.related_axis_weights;
  if v_w = 0 then
    raise exception '143: related_axis_weights is empty — every candidate '
                    'generator filters on it, so related_items will be empty '
                    'too, silently. Run refresh_related_axis_weights() first.';
  end if;

  -- (c) The stored-edge invariants (plan §10).
  select count(*) into v_bad from public.related_items
   where source_type <> target_type          -- D2: same-type only
      or source_id = target_id               -- no self-edges
      or score < public.related_floor();     -- nothing below the floor
  if v_bad > 0 then
    raise exception '143: % related_items rows violate the same-type / '
                    'no-self / floor invariants.', v_bad;
  end if;

  -- (d) The weekly job.
  if exists (select 1 from pg_extension where extname = 'pg_cron')
     and not exists (select 1 from cron.job
                      where jobname = 'related_items_refresh_weekly') then
    raise exception '143 §8: pg_cron is installed but '
                    'related_items_refresh_weekly is not scheduled.';
  end if;

  select string_agg(x.line, ' · ' order by x.source_type) into v_stats
    from (select source_type,
                 source_type || ' ' || count(*)::text || ' edges/' ||
                 count(distinct source_id)::text || ' sources' as line
            from public.related_items group by source_type) x;

  raise notice '143 built: % · axis weights: % rows',
               coalesce(v_stats, '<no edges>'), v_w;
end $$;

-- ============================================================================
-- ⚠ NOT AUTO-APPLIED. Files in this directory are run by hand (Supabase SQL
-- editor / MCP apply_migration); nothing in the repo executes them. APPLY THIS
-- FILE AS ONE SCRIPT, and apply it BEFORE the Wave B backend deploys — code
-- that reads related_items 500s if 143 has not run (plan §9, the
-- migration-before-deploy trap this repo has hit twice).
--
-- After applying, the §10 data checks are:
--   select source_type, count(*), count(distinct source_id)
--     from public.related_items group by 1;
--   select count(*) from public.related_items
--    where source_type <> target_type
--       or source_id = target_id
--       or score < public.related_floor();            -- must be 0
--   select corpus, axis, count(*) from public.related_axis_weights group by 1,2;
--   select jobname, schedule from cron.job
--    where jobname = 'related_items_refresh_weekly';
-- and \timing on refresh_related_items('judgment') — if it exceeds a few
-- minutes the generation guards are not biting and §0 is the place to look.
-- ============================================================================
