-- ============================================================================
-- Migration 116 — usage-driven ordering for the /regulations wing
-- Plan: .claude/plans/ranking_criteria.md §5 (two segments) · §6 (storage) · §7
--
-- WHAT ORDERS THE WING TODAY, AND WHY IT HAS TO CHANGE.
-- `list_regulations_hub` orders "in-force first, then clean_title" — a contract
-- no single column expresses, so it is implemented THREE ways: BM25 relevance
-- when `q` is present, a Python sort in sample mode, and a two-partition DB
-- range-slice in full corpus. The result on the live wing is a page 1 of one
-- titleless row followed by EIGHT «النظام الأساس لشركة … للتأمين التعاوني»
-- incorporation charters, because `clean_title` is NULL on 43% of the corpus and
-- Arabic titles begin with their document type (اللائحة 549 · لائحة 426 ·
-- دليل 370 · نظام 323), so alphabetical order sorts by type-word, not by name.
--
-- This migration gives the wing the ONE sortable integer it never had. Ordering
-- policy itself lives in `scripts/build_usage_rank.py`; nothing here decides a
-- rank, it only stores one and makes it joinable.
--
-- ⚠ THE VIEW IS NOT A CONVENIENCE — IT IS WHAT MAKES 462 PUBLISHED ROWS WORK.
-- `rank` lives on the `seo_item_meta` SIDECAR; the facet filters (`entity`,
-- `doc_type`, `sector`) live on the `regulations_v2` CORPUS. One page needs to
-- filter, order and slice in a single query, so the two have to meet somewhere.
-- They meet here.
--
-- Without it, publishing past `SAMPLE_MODE_MAX_IDS` (300) is actively broken:
-- above that ceiling `_published_ids()` returns None and the listers fall back to
-- paginating the CORPUS, dropping every row that has no slug — 462 published of
-- 3,952 corpus rows yields roughly ONE card per nine-card page. The view makes
-- published-set pagination the only path, at any corpus size, and retires sample
-- mode for this wing.
--
-- ⚠ `regulations_v2` AND `chunks_v2` ARE VIEWS over the pipeline-owned
-- `regulation_v2` schema (see 109 T6). They cannot be altered or indexed from
-- here. Everything below either sits beside them (`seo_item_meta`) or reads
-- through them (this view, these functions). Do not attempt DDL on the corpus.
--
-- ⚠ WHY SECURITY DEFINER + REVOKE, same reasoning as 109/110.
-- The corpora and `workspace_items` are NOT readable by `anon`, and
-- `workspace_items` is per-user data behind RLS. The public library reads
-- everything through the SERVICE-ROLE client (`backend/app/deps.py`
-- `get_supabase()`), which — together with the operator running the rank script
-- — is the only caller these functions have. DEFINER is about the function
-- reading pipeline + user tables; the REVOKE is about nobody else being able to
-- trigger those aggregations from the Supabase hostname, outside the origin
-- lock, the rate limiter and the backend's memos.
--
-- ⚠ NO RAW USER IDENTIFIERS LEAVE THE DATABASE. `library_reg_usage_refs()`
-- needs to group by user and by conversation so that one account cannot define
-- the public ordering (plan §4.2), but it does NOT need to know WHICH account.
-- It returns `md5()` grouping keys. Grouping is identical; identity stays in the
-- database. Do not "simplify" these back to raw uuids.
-- ============================================================================

-- --- 1. Rank storage on the sidecar -----------------------------------------
-- `seo_item_meta` already carries the per-item editorial levers (`slug`,
-- `seo_tier`, `gate_override`) on PK (content_type, content_id) for 3,319 of the
-- 3,952 regulations. Rank belongs beside them: it is an editorial decision about
-- a published item, not a property of the pipeline's corpus row.
alter table public.seo_item_meta
  add column if not exists rank integer;

alter table public.seo_item_meta
  add column if not exists usage_score numeric;

comment on column public.seo_item_meta.rank is
  'Dense 1..N display order within a content_type, written by '
  'scripts/build_usage_rank.py. THE hub ordering key. NULL = not yet ranked '
  '(sorts last).';

comment on column public.seo_item_meta.usage_score is
  'Audit only — the §4 usage score behind `rank`. NEVER order on this directly: '
  'it is a float, and float ties reintroduce the arbitrary neighbour order the '
  'entity interleave exists to remove. Order on `rank`.';

-- Partial: only published rows are ever ordered.
create index if not exists seo_item_meta_rank_idx
  on public.seo_item_meta (content_type, rank)
  where slug is not null;

-- --- 2. The ranked, published-only regulations view -------------------------
-- Corpus ⋈ sidecar, filtered to published rows (`slug is not null`). The hub
-- selects, filters, orders and ranges over THIS — one query, no slug sidecar
-- round-trip, no partition straddle, no sample mode.
--
-- `summary_embedding` is deliberately NOT exposed: it is a pgvector column that
-- no public surface reads, and carrying it would make every `select *` on this
-- view drag a 1536-dim array per row across the wire.
create or replace view public.library_regulations_ranked as
select
  r.id,
  r.reg_ref,
  r.entity_id,
  r.entity_ref,
  r.entity_name,
  r.title,
  r.clean_title,
  r.core_subject,
  r.doc_type_raw,
  r.doc_type_bucket,
  r.sectors,
  r.parent_law_id,
  r.doc_relation,
  r.status_class,
  r.status_raw,
  r.legal_authority,
  r.start_date,
  r.end_date,
  r.landing_url,
  r.fallback_url,
  r.pdf_url,
  r.intro,
  r.scope,
  r.obligations,
  r.summary,
  r.llm_summary,
  r.definitions,
  r.ingested_at,
  m.slug,
  m.rank,
  m.usage_score,
  m.seo_tier,
  m.gate_override
from public.regulations_v2 r
join public.seo_item_meta m
  on m.content_type = 'regulation'
 and m.content_id = r.id::text
where m.slug is not null;

comment on view public.library_regulations_ranked is
  'Published regulations (slug present) with their sidecar rank. THE surface '
  '/regulations paginates. See migration 116 header before changing.';

revoke all on public.library_regulations_ranked from anon, authenticated;
grant select on public.library_regulations_ranked to service_role;

-- --- 3. Rank inputs: usage -------------------------------------------------
-- One row per (regulation, user, conversation, used, relevance) with a ref
-- count. The caller applies the quality weights and the conversation/user caps
-- (plan §4) — this function holds NO policy, so re-tuning the weights never
-- needs a migration.
--
-- ⚠ `workspace_item_references.item_id` is a `chunks_v2.id` — a مادة chunk, NOT
-- a `regulations_v2.id`. Joining it straight to the regulations corpus returns
-- ZERO rows, silently, with no error. The roll-up through
-- `chunks_v2.regulation_id` below is the whole reason this function exists.
-- Chunk ids orphaned by a corpus re-ingest (~8% of refs) drop out on that join.
create or replace function public.library_reg_usage_refs()
returns table (
  regulation_id uuid,
  user_key      text,
  conv_key      text,
  used          boolean,
  relevance     text,
  n_refs        bigint
)
language sql
stable
security definer
set search_path = public
as $$
  select
    c.regulation_id,
    md5(i.user_id::text)         as user_key,
    md5(i.conversation_id::text) as conv_key,
    w.used,
    w.relevance,
    count(*)                     as n_refs
  from workspace_item_references w
  join chunks_v2       c on c.id = w.item_id
  join workspace_items i on i.item_id = w.wi_id
  where w.domain = 'regulations'
    and c.regulation_id is not null
  group by 1, 2, 3, 4, 5;
$$;

revoke all on function public.library_reg_usage_refs() from public, anon, authenticated;
grant execute on function public.library_reg_usage_refs() to service_role;

-- --- 4. Rank inputs: corpus depth ------------------------------------------
-- Chunk count per regulation — the §4.3 depth tiebreaker. PostgREST cannot
-- express `GROUP BY regulation_id` over the chunks view, and the script must not
-- pull every chunk row to count them client-side.
create or replace function public.library_reg_chunk_counts()
returns table (
  regulation_id uuid,
  chunk_count   bigint
)
language sql
stable
security definer
set search_path = public
as $$
  select regulation_id, count(*)
  from chunks_v2
  where regulation_id is not null
  group by 1;
$$;

revoke all on function public.library_reg_chunk_counts() from public, anon, authenticated;
grant execute on function public.library_reg_chunk_counts() to service_role;
