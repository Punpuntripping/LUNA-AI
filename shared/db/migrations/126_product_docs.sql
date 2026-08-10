-- ============================================================================
-- Migration 126 — product_docs: what the router is allowed to say about ريحان
-- Plan: .claude/plans/router_rayhan_docs.md
--
-- WHY A TABLE AND NOT FILES IN THE IMAGE.
-- The router's system prompt already promises it answers «questions about
-- Rayhan and its functions», but it has no grounded product knowledge, so it
-- answers pricing and data-protection questions from the model's own priors —
-- the one surface still doing exactly what the rest of the pipeline exists to
-- prevent. The content it should be reading lives in the FRONTEND
-- (`components/learn/*.tsx`, `components/landing/content.ts`), and
-- `backend/Dockerfile` copies only shared/ + agents/ + backend/, so the router
-- cannot read a single byte of it at runtime.
--
-- A table, rather than markdown baked into the image, because product copy
-- changes on marketing's clock, not on a deploy's: a price line or a
-- data-protection sentence must be fixable in minutes.
--
-- ⚠ THIS TABLE HAS NO SEEDER AND NO FILE-BASED MIRROR — that is the owner's
-- decision, and it means THE ROWS ARE THE ONLY COPY. There is nothing in the
-- repo to re-seed from: an accidental DELETE is a content loss, not a re-run.
-- The 15 launch rows were written straight into the database on 2026-08-10
-- (the legal three copied byte-for-byte out of frontend/content/legal/*.md so
-- the router quotes the same words /privacy and /terms serve). Edit rows in
-- the Supabase console; the router picks the change up within one cache TTL
-- (10 minutes, see rayhan_docs.py).
--
-- ⚠ `doc_key` IS A CONTRACT WITH PYTHON, NOT A FREE-TEXT SLUG.
-- `agents/tool_repository/rayhan_docs.py` declares the key set as a `Literal`
-- so it renders into the tool's JSON schema — that is what lets the model see
-- the whole catalog without a token of it entering the router's system prompt,
-- and what makes a hallucinated key impossible. Renaming a key here without
-- renaming it there silently removes a doc from the model's reach: the tool
-- keeps offering the old key and the lookup returns nothing. `catalog` is the
-- same contract one level up — it decides WHICH of the two tools serves the
-- row, and a row whose catalog does not match its tool is simply unreachable.
--
-- ⚠ NO PRICES IN THIS TABLE. Amounts live in `plans.price_sar` (authoritative,
-- what checkout charges) and `frontend/lib/pricing.ts` (display). Those two
-- have already drifted apart once and are pinned together by hand. A third
-- copy here would be the one nobody remembers to update — and the router
-- states it to a paying customer with total confidence. The pricing doc
-- explains the points MODEL and links to /pricing for the amounts.
-- ============================================================================

create table if not exists public.product_docs (
  doc_key         text primary key,

  -- Which of the two router tools serves this row. 'about' → open_rayhan_page
  -- (what Rayhan is, plans, the legal documents); 'guide' → open_rayhan_guide
  -- (agents, workspace, library, how to use it well). The split is the owner's
  -- framing of the two tools and is enforced here so a mis-catalogued row
  -- fails the seed rather than going quietly unreachable.
  catalog         text not null check (catalog in ('about', 'guide')),

  title           text not null,

  -- One line, shown to the model as the doc's header when it opens the doc.
  -- Not a summary of the body — a statement of what question the body answers.
  blurb           text not null,

  content_md      text not null,

  -- The public page this doc mirrors, e.g. '/learn/workspace'. The tool hands
  -- it to the router so it can point the user at the real page instead of
  -- paraphrasing it. NULL is meaningful and load-bearing: `guide`,
  -- `best_practices` and `examples` are nav entries with NO page behind them
  -- (`site-nav.ts` has them `enabled: false`), so the router must teach the
  -- material without offering a link into a 404.
  canonical_path  text,

  -- Unpublishing is how you retract a claim without a deploy: the tool filters
  -- on this, so flipping it false makes the router stop citing the doc within
  -- one cache TTL.
  is_published    boolean not null default true,

  sort_order      integer not null default 100,

  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

comment on table public.product_docs is
  'Grounding for the router''s answers about ريحان itself — the product, the '
  'plans, the legal documents, how to use it. Authored as markdown under '
  'agents/knowledge/rayhan/ and upserted by scripts/seed_product_docs.py. '
  'Read by agents/tool_repository/rayhan_docs.py. Carries NO prices.';

comment on column public.product_docs.doc_key is
  'Stable slug. MUST match a member of the Literal key set in '
  'agents/tool_repository/rayhan_docs.py — renaming here without renaming '
  'there makes the doc unreachable.';

comment on column public.product_docs.canonical_path is
  'Public page this doc mirrors. NULL = no page exists yet (the router then '
  'teaches the content without offering a link).';

-- The tools filter by catalog + is_published on every miss. Small table, but
-- the index keeps the seed-and-forget path from ever growing a seq scan.
create index if not exists idx_product_docs_catalog
  on public.product_docs (catalog, sort_order)
  where is_published;

-- --- updated_at ------------------------------------------------------------
-- Reuses the repo-wide trigger fn when it exists (every prior migration that
-- needed one defines it), else creates it. Idempotent either way.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists trg_product_docs_updated_at on public.product_docs;
create trigger trg_product_docs_updated_at
  before update on public.product_docs
  for each row execute function public.set_updated_at();

-- --- RLS -------------------------------------------------------------------
-- House rule: every table has RLS enabled. The router reads through the
-- SERVICE-ROLE client, which bypasses RLS entirely — so the policy below is
-- NOT what feeds the agent. It exists so that a future frontend can render
-- these same docs straight from the client without a backend route, and so
-- that the table's default-deny posture is explicit rather than incidental.
--
-- Read: anyone, published rows only. This is public marketing and legal copy —
-- the same words already served to anonymous visitors on /about_us and
-- /privacy. An unpublished row is a retracted or not-yet-live claim and stays
-- invisible to everyone but service_role.
--
-- Write: NO policy at all. Authoring is a human in the Supabase console.
-- Neither anon nor authenticated may write product claims — a table an end
-- user can INSERT into is a table that can put words in the router's mouth.
alter table public.product_docs enable row level security;

drop policy if exists product_docs_read_published on public.product_docs;
create policy product_docs_read_published
  on public.product_docs
  for select
  to anon, authenticated
  using (is_published);
