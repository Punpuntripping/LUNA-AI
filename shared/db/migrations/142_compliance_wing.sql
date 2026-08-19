-- ============================================================================
-- Migration 142 — library_compliance_v: the /compliance service-guides wing
-- Plan: .claude/plans/compliance_service_guides.md §3 (this file) · §0 (why the
--       wing's founding rule is superseded) · §4.1 (the lister this unlocks)
-- Consumer contract for the corpus itself:
--       C:\Programming\agentic_for_ministry\ingestion\service_guides\REFERENCE.md
--
-- WHAT THE WING NOW PUBLISHES, AND WHY THAT IS ALLOWED.
-- /compliance has been live, wired and deliberately EMPTY since 2026-08-04. It
-- was emptied because the thing it used to serve was the `services` corpus —
-- somebody else's procedure text, republished. A service GUIDE is different in
-- kind: it is Rayhan's own authored rewrite of the issuing entity's official PDF
-- user-guide, with our own screenshots pipeline behind it. That is why this wing
-- gets to be fully ungated and indexable while the old one could not be, and it
-- is why the corpus is only 169 rows — one per service we actually wrote a guide
-- for, not one per service that exists. The entity's own service page
-- (`services.service_url`) is the single outbound link; `source_pdf_url` is
-- NEVER surfaced, which is enforced structurally by the API payload rather than
-- here (see the note on that column below).
--
-- WHY A VIEW AT ALL — THE THREE COLUMNS THAT LIVE ON THE WRONG TABLE.
-- Everything a guide page and a guide card need is split across two tables:
--
--   `service_guides`  — the guide itself: title, summary, guide_md, image_count,
--                       most_used_rank.
--   `services`        — provider_name (the issuing entity's display name, i.e.
--                       the card's byline and the hub's `provider` filter),
--                       service_url (the ONE outbound link the wing is allowed
--                       to render), and sectors (text[] — THE sector axis every
--                       other wing is faceted on; `_SECTION_SOURCES` in
--                       backend/app/services/library_service.py declares this
--                       view's sector column as `sectors`, which is what makes
--                       /library/{sector} grow a compliance tab and
--                       `LibraryCounts.compliance` go real).
--
-- The alternative to a join is denormalising those three onto `service_guides`.
-- That is exactly what must not happen. ⚠ BOTH BASE TABLES ARE PIPELINE-OWNED:
-- they are built and REBUILT by the `agentic_for_ministry` ingestion writing
-- straight into this project. App-owned columns bolted onto them would be
-- silently dropped or clobbered by the next rebuild, and the app would be the
-- last to find out. Same rule this repo already holds for `regulations_v2`
-- (migration 116), `cases` (123/124) and `circulars`: never ALTER a corpus
-- table, never write to one — put the app's shape in a view, which the ingest
-- cannot see and cannot break. If the ingest ever DOES reshape a column named
-- below, `create or replace view` fails loudly on the next apply instead of
-- serving something subtly wrong.
--
-- WHY NO SIDECAR JOIN HERE (this view is NOT a `library_*_ranked` view).
-- 116 and 123 join `seo_item_meta` INTO the view because their wings paginate
-- over corpora of 3,951 and 30,531 rows: above `SAMPLE_MODE_MAX_IDS`
-- (library_service.py:172 — 1000) the lister falls back to paginating the corpus
-- and post-filtering out unpublished rows, which returns short pages. This
-- corpus is 169 rows, permanently — it is one row per guide we wrote, and it
-- grows by hand. 169 is six times under that ceiling, so the compliance lister
-- stays on the published-ids path (`_published_ids('compliance')` → filter in
-- Python, plan §4.1) forever, and "published" never needs to become a property
-- of the relation. Adding the join anyway would buy nothing and would couple a
-- pipeline-owned view to the sidecar's key-space conventions. If this corpus
-- ever passes 1000 guides, THAT is the moment to add `join seo_item_meta` and a
-- `where m.slug is not null` — not before.
--
-- ⚠ `guide_md` CARRIES UNRESOLVED IMAGE HOLES AND THAT IS BY DESIGN.
-- A guide body contains bare `\d+_\d+` token lines, each of which the RENDERER
-- swaps for the matching `service_guide_images` row (REFERENCE.md §3). This view
-- deliberately does not attempt that substitution: SQL is the wrong layer for it
-- (holes resolve by `image_ref`, never by position — 28% of guides place them out
-- of numeric order), and the images are a second relation the doc endpoint reads
-- separately. The one rule that survives into every consumer: an unresolved hole
-- emits NOTHING. A raw `223719_1` on a user-facing page is THE failure mode this
-- corpus's whole design exists to prevent.
--
-- ⚠ `source_pdf_url` IS NOT IN THE SELECT LIST, ON PURPOSE.
-- It exists on `service_guides` and it is a perfectly good internal provenance
-- column, but decision #4 of the plan is that the wing never shows it. Leaving
-- it out here is a second, structural line of defence behind the API model that
-- also omits it: a route that selects `*` from this view still cannot leak it.
-- Same reasoning for `source_pdf_md5` / `source_pages` / `guide_ref` /
-- `entity_ref` / `built_at` / `ingested_at` / `fts` — ingest bookkeeping with no
-- reader on the wing. This is an ENUMERATED select list, never `g.*`: `fts` is a
-- tsvector that would otherwise ride across the wire on every card query, and an
-- enumerated list turns a corpus reshape into a failed migration instead of a
-- silently wider view.
--
-- Idempotent. Safe to re-run: `create or replace view` + a grants block that
-- states the final state rather than mutating it + CHECK constraints that are
-- dropped-if-exists and re-added rather than mutated in place.
-- ============================================================================

-- ── 1. the content_type vocabulary ──────────────────────────────────────────
-- ⚠ WITHOUT THIS SECTION THE WING CANNOT PUBLISH A SINGLE PAGE.
-- Three tables spell their `content_type` vocabulary as a text column plus a
-- CHECK against a literal array, and none of those arrays contains
-- 'compliance'. The sidecar's is the fatal one:
-- `scripts/build_compliance_slugs.py --apply` inserts
-- `content_type='compliance'` rows into `seo_item_meta`, and a slug IS the
-- publish mechanism for this wing — so a rejected INSERT is not a degraded
-- wing, it is a wing with zero pages, no sitemap entries and a dead chat exit.
--
-- WHY A NEW content_type AND NOT THE EXISTING 'service'.
-- Because they are different key spaces over different corpora, and mixing them
-- ships 404s. `seo_item_meta` already holds 4,717 `content_type='service'` rows
-- (100 of them slugged, with ARABIC slugs) left behind by the RETIRED services
-- wing, and those rows are keyed by `services.id`. A compliance row is keyed by
-- `service_guides.id`. The SAME real-world service therefore has two different
-- ids in the two spaces, so a lookup that resolved a 'service' row and handed
-- its content_id to the guide reader would miss every time. On top of that the
-- sidecar's unique index is `(content_type, slug)` — per type — so reusing
-- 'service' would put Latin guide slugs in the same namespace as the retired
-- wing's Arabic ones and make collisions possible between two corpora that have
-- nothing to do with each other. A new value keeps the two spaces disjoint by
-- construction, and leaves the 4,717 stale rows untouched (plan §9).
--
-- The column stays TEXT + CHECK. Converting to an enum would be a strictly
-- worse trade here: it makes every future vocabulary addition a type-level
-- migration with a rewrite, and this repo has added a content_type four times
-- already.
--
-- Each constraint is dropped-if-exists then re-added, which makes a replay a
-- no-op instead of a `constraint already exists` failure. The arrays below
-- PRESERVE the existing values verbatim in their existing order and only APPEND
-- 'compliance' — verified against pg_constraint 2026-08-19. Do not "tidy" them;
-- a value silently removed here becomes a failing INSERT in a wing nobody is
-- currently looking at.

-- (a) THE BLOCKER. Without this the slug script writes nothing.
ALTER TABLE public.seo_item_meta
    DROP CONSTRAINT IF EXISTS seo_item_meta_content_type_check;
ALTER TABLE public.seo_item_meta
    ADD CONSTRAINT seo_item_meta_content_type_check
    CHECK (content_type = ANY (ARRAY[
        'regulation'::text, 'article'::text, 'judgment'::text,
        'circular'::text, 'service'::text, 'form'::text,
        'compliance'::text
    ]));

-- (b) The my-library shelf. The guide page's `LibraryUseBeacon` writes a
--     `library_items` row on read, and «حفظ في مكتبتي» writes one on demand.
--     This one fails QUIETLY in one direction and loudly in the other:
--     `record_use` swallows its exceptions, so the beacon would just never
--     record a guide and the shelf would look empty for no visible reason,
--     while the explicit save would 500.
ALTER TABLE public.library_items
    DROP CONSTRAINT IF EXISTS library_items_content_type_valid;
ALTER TABLE public.library_items
    ADD CONSTRAINT library_items_content_type_valid
    CHECK (content_type = ANY (ARRAY[
        'regulation'::text, 'article'::text, 'judgment'::text,
        'circular'::text, 'service'::text, 'form'::text,
        'calculator'::text, 'compliance'::text
    ]));

-- (c) DELIBERATE AND CURRENTLY UNEXERCISED — vocabulary parity only.
--     NOTHING writes a 'compliance' row here today and nothing is expected to:
--     the wing is ungated by decision (plan §0 #2), so no unlock is ever
--     purchased for a guide, and the hub's exposure budget meters string keys in
--     a window rather than rows in this table. It is relaxed anyway because
--     `library_items` and `library_unlocks` are maintained as a PAIR with
--     identical vocabularies — they are the "what did you look at" and "what did
--     you pay to see" halves of the same idea. Letting the two drift leaves a
--     landmine: the day someone decides one guide should be gated after all,
--     the gating path would 500 in production against a constraint nobody
--     remembered, in a table whose sibling had been fixed years earlier. The
--     cost of parity now is one array; the cost of drift later is an outage.
ALTER TABLE public.library_unlocks
    DROP CONSTRAINT IF EXISTS library_unlocks_content_type_valid;
ALTER TABLE public.library_unlocks
    ADD CONSTRAINT library_unlocks_content_type_valid
    CHECK (content_type = ANY (ARRAY[
        'regulation'::text, 'article'::text, 'judgment'::text,
        'circular'::text, 'service'::text, 'form'::text,
        'calculator'::text, 'compliance'::text
    ]));

-- ⚠ `topic_map_content_type_check` IS DELIBERATELY NOT TOUCHED. That vocabulary
-- ('regulation','article','judgment','circular','service','blog','calculator',
-- 'form') belongs to the unified-topics work and guides are not mapped to
-- topics in v1. Adding a value there would be this migration reaching into
-- another project's surface for no reader.

-- ── 2. the view ─────────────────────────────────────────────────────────────
-- `is_canonical` is the corpus's own dedupe flag (portal aliases point several
-- refs at one guide). All 169 rows are canonical today and there are no aliases,
-- but the predicate stays: the day the ingest adds one, the wing must not
-- publish two URLs for the same guide.
--
-- The join is on `services.id` (the PK) and cannot fan out. It is an INNER join
-- and that is deliberate — a guide whose service row vanished has no
-- provider_name, no service_url and no sectors, i.e. no card byline, no outbound
-- link and no sector facet. Such a row belongs out of the wing, not in it with
-- three nulls. Verified live 2026-08-19: 0 of 169 guides fail this join, and 0
-- have an empty `service_url`.
create or replace view public.library_compliance_v
with (security_invoker = true) as
select g.id,
       g.service_id,
       g.service_ref,
       g.title,
       g.summary,
       g.guide_md,
       g.image_count,
       g.most_used_rank,
       s.provider_name,
       s.service_url,
       s.sectors
from   public.service_guides g
join   public.services s on s.id = g.service_id
where  g.is_canonical;

comment on view public.library_compliance_v is
  'The /compliance wing''s read surface: service_guides ⋈ services, canonical '
  'guides only. Exists because provider_name / service_url / sectors live on '
  '`services` while the guide body lives on `service_guides`, and BOTH are '
  'pipeline-owned (agentic_for_ministry) — the app must never add its own '
  'columns to a table the ingest rebuilds. `source_pdf_url` is deliberately '
  'absent: the wing shows the entity''s service page and never the source PDF. '
  '`guide_md` still contains unresolved `\d+_\d+` image holes — resolve them by '
  'image_ref against service_guide_images, and emit NOTHING for a hole that has '
  'no row. See migration 142 and the ingestion REFERENCE.md before changing.';

-- ── 3. exposure ─────────────────────────────────────────────────────────────
-- ⚠ "PUBLIC WING" MEANS PUBLIC THROUGH THE BACKEND, NOT THROUGH POSTGREST.
-- Every anon reader of this wing arrives at FastAPI (`GET
-- /api/v1/public/library/compliance/...`), which reads with the SERVICE-ROLE
-- client (backend/app/deps.py) after the route has applied its own gating,
-- rate-limiting and payload shaping. Nothing in the browser ever talks to this
-- relation. So the grants match `library_regulations_ranked` (116) and
-- `library_judgments_ranked` (123) exactly: service_role only.
--
-- Granting `anon` here instead would put the RAW corpus on the public PostgREST
-- endpoint — full `guide_md` for all 169 guides, ignoring the sidecar entirely,
-- which would hand out the 164 guides the pilot deliberately leaves UNPUBLISHED
-- and make `--limit 5` meaningless. The wing's publish control is the sidecar
-- slug; a table grant would route around it.
--
-- `security_invoker = true` is the belt to that braces, and it is not
-- decoration. Without it the view executes with its OWNER's privileges
-- (postgres, which carries BYPASSRLS) — so anyone later granted SELECT on the
-- view would read both base tables with RLS bypassed. With it, the base tables'
-- own grants and policies are checked as the CALLER, and the caller fails
-- closed: verified live 2026-08-19, `anon` has no privilege at all on
-- `service_guides` (a direct read is `permission denied for table`), so a future
-- accidental `grant select ... to anon` on this view still yields nothing.
-- ⚠ Do not drop this option on a later edit. Migration 129 dropped it off
-- `user_subscriptions_live` while recreating that view and 129a had to put it
-- back; the guard in §4 is here so that failure mode is loud this time.
revoke all on public.library_compliance_v from PUBLIC;
revoke all on public.library_compliance_v from anon, authenticated;
grant select on public.library_compliance_v to service_role;

-- ── 4. self-verification ────────────────────────────────────────────────────
-- Three invariants the file above only ASSERTS in prose. All are cheap, and all
-- three have a way of silently regressing: 129 → 129a for the security_invoker
-- option, Supabase's dashboard grant editor for the privileges, and a partially
-- applied script for the constraints (this file is run BY HAND, so "someone
-- pasted from §2 down" is a real thing that happens — and the failure it
-- produces, a wing that publishes nothing, looks like a bug in the slug script
-- rather than a missing constraint).
do $$
declare
  v_opts text[];
  v_leak text;
  v_bad  text;
begin
  -- (a) The vocabulary. Checked by asking each CHECK's own definition whether
  --     it mentions 'compliance' — cheaper and more direct than a trial INSERT,
  --     and it cannot leave a row behind.
  select string_agg(c.relname || '.' || con.conname, ', ' order by c.relname)
    into v_bad
  from pg_constraint con
  join pg_class c on c.oid = con.conrelid
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public'
    and con.contype = 'c'
    and con.conname in (
      'seo_item_meta_content_type_check',
      'library_items_content_type_valid',
      'library_unlocks_content_type_valid'
    )
    and pg_get_constraintdef(con.oid) not like '%compliance%';

  if v_bad is not null then
    raise exception
      'content_type CHECK(s) still reject ''compliance'': %. The /compliance '
      'wing publishes by INSERTing seo_item_meta rows, so until this passes '
      'scripts/build_compliance_slugs.py --apply writes nothing and the wing '
      'has zero pages. Apply migration 142 §1 in full.',
      v_bad;
  end if;

  -- (b) The view's security posture.
  select c.reloptions into v_opts
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relname = 'library_compliance_v';

  if v_opts is null or not ('security_invoker=true' = any (v_opts)) then
    raise exception
      'library_compliance_v is missing security_invoker=true (reloptions: %). '
      'Without it the view runs as its owner (BYPASSRLS) and any future grant '
      'to anon would read service_guides unfiltered. Re-apply migration 142.',
      coalesce(v_opts::text, 'NULL');
  end if;

  -- (c) The view's exposure.
  select string_agg(distinct grantee, ', ' order by grantee) into v_leak
  from information_schema.role_table_grants
  where table_schema = 'public'
    and table_name = 'library_compliance_v'
    and grantee in ('anon', 'authenticated', 'PUBLIC');

  if v_leak is not null then
    raise exception
      'library_compliance_v is readable by %. The /compliance wing is public '
      'through the FastAPI routes (service-role client), not through PostgREST; '
      'a direct grant hands out the 164 guides the sidecar leaves unpublished. '
      'Revoke it.',
      v_leak;
  end if;
end
$$;

-- ============================================================================
-- ⚠ NOT AUTO-APPLIED. Files in this directory are run by hand (Supabase SQL
-- editor / MCP `apply_migration`); nothing in the repo executes them. APPLY THIS
-- FILE AS ONE SCRIPT — §1 and §2 are the two halves of one change and the guard
-- in §4 can only verify what ran with it. Apply it BEFORE the backend that reads
-- `library_compliance_v` deploys, or every /compliance request 500s on a missing
-- relation (the migration-before-deploy trap this repo has now hit twice), and
-- before `scripts/build_compliance_slugs.py --apply` runs, or every INSERT it
-- attempts is rejected by the §1(a) CHECK.
--
-- This migration publishes NOTHING on its own. The wing's visible set is the
-- `seo_item_meta` sidecar: `content_type='compliance'`, `content_id =
-- service_guides.id::text`, and a row is servable only once it has a `slug`.
-- `scripts/build_compliance_slugs.py` writes those, and the pilot ships with
-- `--limit 5 --apply` — five guides live, 164 unslugged and therefore invisible
-- to every endpoint and to the sitemap.
--
-- ⚠ The sidecar already holds 4,717 `content_type='service'` rows (100 of them
-- slugged, with ARABIC slugs) left over from the RETIRED wing. Those are keyed
-- by `services.id` — a different key space entirely. Nothing in this wing reads,
-- writes or collides with them.
-- ============================================================================
