-- 146 — the /compliance guide TITLE gains its delivery channel.
--
-- Depends on: 142 (public.library_compliance_v, public.service_guides,
--             public.services), 144 (refresh_search_index's `compliance` branch,
--             which reads `title` FROM the view and therefore inherits this
--             change with no edit of its own).
--
-- WHAT THIS DOES
--   1. creates `public.service_guide_channels` — an APP-OWNED sidecar holding,
--      per guide, the delivery channel and the composed public title.
--   2. re-creates `library_compliance_v` so its `title` column serves that
--      composed title, falling back to the corpus title.
--
-- WHY THE TITLE CHANGES AT ALL
-- ----------------------------
-- The corpus title reads «الدليل الشامل: {الخدمة} في السعودية». 445 of 533
-- titles carry that locale tail, so as a distinguishing keyword it is worth
-- nothing — every sibling has it. What a reader searches for is the CHANNEL:
-- «… في بوابة ناجز», «… في منصة بلدي». Measured 2026-08-25: 365 of 533 guides
-- have a branded channel; the other 168 take their issuing entity, which is
-- still a real distinguisher and always true.
--
-- ⚠ WHY A SIDECAR AND NOT A COLUMN ON `service_guides`
-- -----------------------------------------------------
-- `service_guides` is PIPELINE-OWNED (agentic_for_ministry rebuilds it on every
-- ingest). The app must never add its own columns to a table another system
-- recreates — the third ingest would drop them. Same reasoning that put the
-- slugs in `seo_item_meta` rather than on the corpus.
--
-- ⚠ AND WHY THERE IS NO FOREIGN KEY TO `service_guides(id)`
-- ----------------------------------------------------------
-- The obvious `references public.service_guides(id) on delete cascade` is a
-- TRAP here. If a future ingest ever rebuilds by delete-then-insert rather than
-- upsert, the cascade silently wipes every title this migration exists to hold
-- — and it would happen during someone else's routine data run, with no error.
-- Guide ids are `uuid5(service_ref)` and therefore stable across re-ingests
-- (ingestion REFERENCE.md), so the join is reliable WITHOUT the constraint. A
-- dangling sidecar row costs nothing: the LEFT JOIN below simply does not match
-- it, and the guide shows its corpus title.
--
-- ⚠ THIS MIGRATION CANNOT CHANGE A URL. Slugs live in `seo_item_meta` and are
-- permanent; nothing here touches them. A title is COPY and is re-derivable —
-- which is why `scripts/build_guide_channels.py` upserts the whole row on every
-- run, and why clearing this table is a complete, safe rollback (§4).

begin;

-- ── 1. the sidecar ──────────────────────────────────────────────────────────
create table if not exists public.service_guide_channels (
    guide_id      uuid primary key,
    -- The branded channel, already normalised and canonicalised
    -- (`shared/library/guide_titles.py`): «بوابة ناجز», «منصة بلدي». NULL when
    -- the guide has no branded channel — a first-class answer, not a failure.
    channel       text,
    -- The composed public title. STORED rather than computed in SQL so the
    -- composition rules (locale-tail strip, anti-stutter, the «الدليل الشامل:»
    -- prefix left untouched for the client's «بالصور» rewrite) live in ONE
    -- place, in Python, with tests — instead of being reimplemented in plpgsql
    -- where the two could drift.
    display_title text not null,
    -- 'llm' when a channel was found, 'entity_fallback' when the issuing body
    -- was used instead. Lets a reviewer slice the wing by how a title was got.
    source        text not null default 'llm',
    -- Why there is no channel, for the fallback rows: 'no branded channel', a
    -- shape-gate message, or 'NOT GROUNDED …' when the model named a portal our
    -- own guide body never mentions. Diagnostic only; nothing reads it.
    reason        text,
    built_at      timestamptz not null default now()
);

comment on table public.service_guide_channels is
  'App-owned sidecar for the /compliance wing: the delivery channel per service '
  'guide and the composed public title built from it. Written ONLY by '
  'scripts/build_guide_channels.py. Deliberately has NO foreign key to '
  'service_guides — that table is pipeline-owned and a cascade would wipe these '
  'titles on a re-ingest; guide ids are uuid5(service_ref) and stable, so the '
  'join holds without one. Clearing this table is a complete rollback: every '
  'guide falls back to its corpus title.';

create index if not exists service_guide_channels_channel_idx
    on public.service_guide_channels (channel)
    where channel is not null;

-- ── 2. exposure — service_role ONLY ─────────────────────────────────────────
-- Same rule as `library_compliance_v` itself (142 §3): this wing is public
-- THROUGH FASTAPI, never through PostgREST. A grant to anon here would not leak
-- the corpus (the titles are public anyway), but it would put an app-owned
-- table on the public API surface for no reader, and this project already has
-- one open finding of exactly that shape. RLS on with NO policies = deny-all
-- for anon/authenticated even if a grant is ever added by accident.
alter table public.service_guide_channels enable row level security;
revoke all on public.service_guide_channels from public, anon, authenticated;
grant select, insert, update, delete on public.service_guide_channels to service_role;

-- ── 3. the view ─────────────────────────────────────────────────────────────
-- ⚠ `create or replace view` KEEPS THE EXISTING COLUMN ORDER AND NAMES. `title`
-- stays column 4 with the same type; `channel` is APPENDED, which is the only
-- structural change Postgres permits here. Do not reorder.
--
-- ⚠ `security_invoker = true` IS RESTATED ON PURPOSE. `create or replace view`
-- does not inherit the option — migration 129 dropped it off
-- `user_subscriptions_live` exactly this way and 129a had to put it back. 142's
-- §4 guard exists for this failure mode; §5 below re-asserts it.
--
-- The join to the sidecar is a LEFT JOIN and the coalesce is belt-and-braces:
-- a guide with no sidecar row, or one whose `display_title` is somehow blank,
-- serves its corpus title rather than an empty <h1>. The wing degrades to
-- exactly its pre-146 behaviour if this table is empty.
create or replace view public.library_compliance_v
with (security_invoker = true) as
select g.id,
       g.service_id,
       g.service_ref,
       coalesce(nullif(btrim(c.display_title), ''), g.title) as title,
       g.summary,
       g.guide_md,
       g.image_count,
       g.most_used_rank,
       s.provider_name,
       s.service_url,
       s.sectors,
       c.channel
from   public.service_guides g
join   public.services s on s.id = g.service_id
left join public.service_guide_channels c on c.guide_id = g.id
where  g.is_canonical;

comment on view public.library_compliance_v is
  'The /compliance wing''s read surface: service_guides ⋈ services, canonical '
  'guides only, LEFT JOINed to the app-owned service_guide_channels sidecar. '
  '`title` is the COMPOSED public title («… في بوابة ناجز»), falling back to the '
  'corpus title when the sidecar has no row — so emptying that table reverts the '
  'wing. `source_pdf_url` is deliberately absent: the wing shows the entity''s '
  'service page and never the source PDF. `guide_md` still contains unresolved '
  '`\d+_\d+` image holes — resolve them by image_ref against service_guide_images, '
  'and emit NOTHING for a hole that has no row. See migrations 142 and 146.';

-- Grants do not survive `create or replace view` in every Postgres path; restate
-- them, matching 142 §3 exactly.
revoke all on public.library_compliance_v from public, anon, authenticated;
grant select on public.library_compliance_v to service_role;

-- ── 4. guards ───────────────────────────────────────────────────────────────
do $$
begin
  -- security_invoker must have survived the replace. Without it the view runs
  -- as its owner (postgres, BYPASSRLS) and any future grant reads both base
  -- tables with RLS bypassed.
  if not exists (
      select 1 from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'public' and c.relname = 'library_compliance_v'
        and c.reloptions @> array['security_invoker=true']
  ) then
    raise exception
      '146 §4: library_compliance_v lost security_invoker=true on replace. '
      'This is the migration-129 failure mode — restore the option before '
      'continuing (142 §3 explains why it is load-bearing).';
  end if;

  -- anon must not have been granted the sidecar.
  if has_table_privilege('anon', 'public.service_guide_channels', 'SELECT') then
    raise exception
      '146 §4: anon can SELECT service_guide_channels. This wing is public '
      'through FastAPI, never through PostgREST.';
  end if;
end $$;

commit;

-- ── 5. AFTERWARDS, IN THIS ORDER ────────────────────────────────────────────
--   1. python scripts/build_guide_channels.py --apply     (fills the sidecar)
--   2. select public.refresh_search_index('compliance');  (BM25 reads the view,
--      so the composed titles only reach search after this runs)
--      select public.refresh_bm25_stats('compliance');
--   3. purge ISR for /compliance and /compliance/page/N — the hub is baked and
--      will keep serving the old card titles. Detail pages re-render on demand.
--
-- ROLLBACK: `delete from public.service_guide_channels;` — every guide reverts
-- to its corpus title through the coalesce, with no schema change and no
-- redeploy. Re-run step 2 afterwards so search follows.
