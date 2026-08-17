-- ============================================================================
-- Migration 139 — analytics_events: how visitors move through the product
--
-- Implements `.claude/plans/product_analytics.md` §4 (Phase 0). The DDL below is
-- the plan's, verbatim, with ONE mechanical change: every index is NAMED and
-- guarded with `if not exists`. The plan writes `create index on <table> (...)`,
-- which postgres auto-names and which HARD-FAILS on a second run — and every
-- other migration in this repo is re-runnable. Same columns, same predicates,
-- same order; only the names are added.
--
-- WHAT THIS TABLE IS. A funnel instrument, not a general event bus. Nine public
-- events (§3) plus thirteen chat-depth events (§3b) — 22 names total, and the
-- endpoint drops anything not on that list, so a typo in the client cannot
-- quietly create a 23rd bucket nobody queries. It exists to answer seven
-- questions (§0): mobile vs desktop, browser/OS, which pages, WHERE PEOPLE
-- DECIDE NOT TO SIGN IN, where they drop off and where from, authed vs anon,
-- and — for chat — whether they wait for a multi-minute deep_search answer.
--
-- ⚠ A VISIT IS TRACKED, A PERSON IS NOT (§2). `session_key` is a sessionStorage
-- key: it dies with the tab and is never linked across visits. That is the same
-- position `frontend/lib/anon-cta/session.ts` already took ("a PDPL-friendly
-- default and one less thing to declare in /privacy"); this table keeps that
-- promise rather than quietly breaking it. Four things are therefore NEVER
-- written here, and the endpoint (backend/app/api/analytics.py) is what enforces
-- it: the raw User-Agent string (a near-unique fingerprint — it is parsed into
-- the three buckets below and discarded), the IP address (used transiently for
-- rate limiting, exactly as public_ask already does), the full referrer URL
-- (host only — the source page's own query string can carry someone else's
-- search terms), and raw query strings (`?q=` on our search surfaces is
-- user-typed legal text, potentially a case description — plan §7 T4).
--
-- ⚠ NO RLS POLICIES, BY DESIGN. Same lockdown posture as `unsent_messages`
-- (135), `library_unlocks` and `payment_methods`: RLS on with ZERO policies is
-- default-deny for `anon` AND `authenticated`, and only the service-role client
-- (backend/app/deps.py get_supabase) — which bypasses RLS — can write. Read it
-- in the Supabase console or through a service-role script. Do NOT add a SELECT
-- policy: behavioural data has no user-facing read, and one policy here would
-- put every visitor's path through the site on the wire.
--
-- RETENTION: 180 DAYS, enforced by the APScheduler purge registered in
-- backend/app/main.py (job id `analytics_events_purge`, 04:00 UTC) — see
-- backend/app/services/analytics_service.py::purge_old_analytics_events. The
-- plan's §4 says 90 and its own §9 Q2 asks whether chat-depth cohorts need
-- longer; 180 is the answer that question got. Raw events past that window
-- answer nothing a rollup cannot, and keeping them is pure PDPL exposure.
--
-- ⚠ DEPLOY ORDER (plan §7 T10): apply this migration BEFORE the backend that
-- writes to it ships, or the first beacon 500s in a loop.
-- ============================================================================

create table if not exists public.analytics_events (
  event_id      bigserial primary key,
  occurred_at   timestamptz not null default now(),

  -- sessionStorage key. Dies with the tab. NOT a person.
  session_key   text        not null,
  -- Set only when the actor was signed in. ON DELETE SET NULL, not CASCADE:
  -- a deleted account must lose the LINK, while the aggregate stays honest.
  user_id       uuid        null references public.users(user_id) on delete set null,
  -- 'authed' | 'anon', as it was AT EVENT TIME.
  -- NOT derivable from `user_id is null`, for two reasons, which is why it is
  -- its own column: (1) user_id is ON DELETE SET NULL, so after an account is
  -- deleted every event that user ever fired would silently re-classify as
  -- anonymous and skew every authed/anon split backwards in time; (2) a visitor
  -- who signs up mid-session fires both kinds under one session_key, and the
  -- split has to survive that.
  user_type     text        not null default 'anon',

  event_name    text        not null,
  path          text        null,

  -- Derived from User-Agent at the endpoint. The raw string is never stored.
  device_type   text        null,   -- mobile | tablet | desktop
  browser       text        null,   -- chrome | safari | firefox | edge | samsung | other
  os            text        null,   -- ios | android | windows | macos | linux | other

  -- Entry attribution. Populated on session_start only.
  referrer_host text        null,
  utm_source    text        null,
  utm_medium    text        null,
  utm_campaign  text        null,

  props         jsonb       not null default '{}'::jsonb
);

comment on table public.analytics_events is
  'Visitor behaviour events — device class, path through the site, gate '
  'abandonment (who decided NOT to sign in), and chat-run depth (did they wait '
  'for a multi-minute answer). Session-scoped identity only: `session_key` is a '
  'sessionStorage key that dies with the tab, and NO raw User-Agent, IP, full '
  'referrer URL or query string is ever stored — see '
  '.claude/plans/product_analytics.md §2. Written ONLY by '
  'backend/app/api/analytics.py (POST /api/v1/public/events) through the '
  'service-role client. ⚠ RLS is on with ZERO policies = deny-all for anon and '
  'authenticated; behavioural data has no user-facing read, so do not add one. '
  'Retention 180 days (APScheduler job `analytics_events_purge`).';

comment on column public.analytics_events.session_key is
  'sessionStorage key (frontend/lib/analytics/session.ts). Dies with the tab, '
  'never linked across visits — a visit, not a person. Deliberately NOT the '
  'localStorage `rayhan_ask_session` key (plan §7 T3): reusing that durable key '
  'would silently convert this into persistent visitor tracking.';

comment on column public.analytics_events.user_type is
  '''authed'' | ''anon'' AS IT WAS AT EVENT TIME. Not derivable from '
  '`user_id is null`: user_id is ON DELETE SET NULL (a deleted account would '
  're-classify its whole history as anonymous), and a visitor who signs up '
  'mid-session fires both kinds under one session_key. Group authed/anon '
  'splits on THIS column, never on user_id.';

comment on column public.analytics_events.device_type is
  'mobile | tablet | desktop, derived at the endpoint from Sec-CH-UA-Mobile '
  'plus a small in-repo UA regex (backend/app/services/analytics_service.py). '
  'The raw User-Agent is parsed and discarded — it is a near-unique '
  'fingerprint and is never persisted.';

comment on column public.analytics_events.referrer_host is
  'HOST ONLY, never the full referrer URL: the source page''s own query string '
  'can carry someone else''s search terms. Populated on session_start.';

comment on column public.analytics_events.props is
  'Per-event payload from the §3/§3b taxonomy (gate_kind, run_state, '
  'ms_since_send, wi_id, ...). Scalars only, sanitized at the endpoint. NEVER '
  'put a raw query string here — `?q=` on the search surfaces is user-typed '
  'legal text (plan §7 T4).';

-- Indexes. Same set as plan §4, named + guarded so re-running is a no-op.
create index if not exists idx_analytics_events_occurred
  on public.analytics_events (occurred_at desc);

create index if not exists idx_analytics_events_session
  on public.analytics_events (session_key);

create index if not exists idx_analytics_events_name_occurred
  on public.analytics_events (event_name, occurred_at desc);

create index if not exists idx_analytics_events_path
  on public.analytics_events (path) where path is not null;

create index if not exists idx_analytics_events_user_type_occurred
  on public.analytics_events (user_type, occurred_at desc);

-- The chat-depth queries (§6b) all walk one run's events in order.
create index if not exists idx_analytics_events_message_id
  on public.analytics_events ((props->>'message_id'), occurred_at)
  where props ? 'message_id';

-- --- RLS -------------------------------------------------------------------
-- ZERO policies = deny-all for anon AND authenticated; service_role bypasses RLS.
-- The 118 lockdown posture, same as unsent_messages / library_unlocks /
-- payment_methods. Do NOT add a policy here: behavioural data has no
-- user-facing read.
alter table public.analytics_events enable row level security;
