# Product Analytics — visitor behaviour, device, and gate abandonment

**Status:** PLANNED, nothing built.
**Decided 2026-08-17:** own Supabase table · session-only identity, no persistent visitor ID · plan before code.

---

## §0 The five questions this must answer

1. **Mobile or desktop?** — device class per visit, and the mobile/desktop split per wing.
2. **Which browser / OS?**
3. **Which pages did they land on?** — entry page, the path through the site, time on each.
4. **Where did they decide NOT to sign in?** — which gated item, which gate surface, on which page, at what depth.
5. **Where do they drop off, and where do they come from?** — exit page, bounce rate, referrer / entry channel.
6. **Authed or anonymous?** — every number above split by user type.
7. **Do they wait for the answer?** — after sending, did they background the tab and return, leave and never
   return, or close it? **Before or after the answer arrived?** And did they open the workspace item? (§3b)

Everything below exists to answer exactly these. Anything that does not serve one of them is out of scope —
this is a funnel instrument, not a general event bus.

---

## §1 Starting position (verified 2026-08-17)

- **No analytics of any kind exists.** No PostHog / GA / Plausible / Segment in `frontend/package.json`, no
  events table in Supabase, no client tracker. This is a from-zero build.
- **Question 4 is already computed and thrown away.** `AnonCtaPopup` decides when a gated pitch appears, records
  the impression, and knows when the reader clicked a CTA versus dismissed it — all in `sessionStorage` via
  `frontend/lib/anon-cta/session.ts`. Nothing ever leaves the browser. The hard part of the gate funnel is done;
  what is missing is a wire.
- **Stable DOM hooks already exist** on every conversion surface: `data-anon-cta` (`FullContentGate`,
  `HubCtaWall`, `BlogConversionCta`) and `data-gate-cta="anon"` (`GateBanner`).
- **There is a working anonymous-POST precedent** — `backend/app/api/public_ask.py`: mounted under `/api/v1`,
  deliberately no `Depends(get_current_user)`, IP-keyed rate limit via `resolve_client_ip`. The beacon endpoint
  is the same shape.
- **There is a "capture the refusal" precedent** — `unsent_messages` (135) stores what users tried to ask when
  the quota gate refused them, "for product analysis". Question 4 is the anonymous-visitor version of that idea.
- **Logfire is live again** (fixed 2026-08-17) and instruments every FastAPI request — but public library and
  blog pages are **Next.js ISR routes that never touch FastAPI**, so backend spans cannot see the traffic this
  plan is about. That is the main reason capture has to be client-side.

---

## §2 Privacy posture — session-only, and why it is not a compromise

**Decision: a visit is tracked, a person is not.** The session key lives in `sessionStorage`, dies with the tab,
and is never linked across visits.

This is not a new position — it is the one the codebase already took. `lib/anon-cta/session.ts` chose
`sessionStorage` explicitly because it "dies with the tab, so nothing persists about a visitor who never created
an account — a PDPL-friendly default and one less thing to declare in /privacy". This plan keeps that promise
instead of quietly breaking it.

**What we therefore CANNOT answer**, stated up front so nobody expects it later:

- "Did this visitor come back on Tuesday?" — no returning-visitor metric, no multi-session attribution.
- "This signup came from a LinkedIn click three days ago" — attribution works **within one session only**. In
  practice that covers most of it: the gate CTA goes straight to `/login?next=…&mode=register`, so gate → signup
  happens in the same tab.

**What we deliberately never store:**

| Never stored | Why |
|---|---|
| Raw `User-Agent` string | A near-unique fingerprint. We parse it, bucket it, and discard it — the buckets are what was asked for anyway. |
| IP address | Used transiently for rate limiting (as `public_ask` already does), never written to a row. |
| Full referrer URL | The source page's own query string can carry someone else's search terms. Host only. |
| Raw query strings | `?q=` on our search surfaces is **user-typed legal text** — potentially a case description. See §7 T4. |
| Any cross-session or cross-device identifier | The whole point of the decision above. |

**PDPL / policy check:** with no persistent identifier, no IP retention, and no raw UA, this is aggregate
measurement of a visit rather than profiling of a person, and it stays consistent with the current `/privacy`
text and with the planned residency move to Alibaba Riyadh (data never leaves your own Postgres). **If Phase 4
is ever adopted (durable ID), that changes and `/privacy` must be updated first** — see §8.

---

## §3 Event taxonomy

Nine events. Deliberately small — every one maps to a question in §0.

| Event | Fired when | Key props |
|---|---|---|
| `session_start` | First event of a tab | `entry_path`, `referrer_host`, `utm_*` |
| `page_view` | Every route change (incl. client-side nav) | `path` |
| `page_exit` | Tab hidden / navigating away | `path`, `dwell_ms`, `max_scroll_pct` |
| `gate_view` | A gated surface became **visible** | `gate_kind`, `path`, `content_type` |
| `gate_cta_click` | Signup/login clicked **from a gate** | `gate_kind`, `path`, `cta` (register\|login) |
| `gate_dismiss` | Gate popup closed without clicking | `gate_kind`, `path` |
| `signup_started` | `/login?mode=register` rendered | `next_path` |
| `signup_completed` | Account created | — |
| `quota_blocked` | Authed user refused by a limit | `limit_kind` |

**Derived, not stored** (these are queries, not events — storing them would let them drift):

- **Bounce** = a session whose `page_view` count is 1.
- **Exit page** = the last `page_view` of a session.
- **Gate abandonment** = a session with `gate_view` and no `gate_cta_click`. ← question 4.
- **Gate conversion rate** = `gate_cta_click` sessions ÷ `gate_view` sessions, grouped by `gate_kind` / `path`.

`gate_kind` enumerates the real surfaces, so the funnel can tell them apart:
`anon_popup` (AnonCtaPopup) · `full_content` (FullContentGate) · `gate_banner` (GateBanner) ·
`hub_wall` (HubCtaWall) · `blog_cta` (BlogConversionCta) · `search_modal` (SearchCtaModal) ·
`judgment_summary` (JudgmentSummary).

---

## §3b Event taxonomy — chat depth

**The question:** a user sends a message; deep_search takes minutes. Did they wait? Did they background the tab
and come back, or leave and never return, or close it outright? Was that **before or after** the answer landed?
And did they ever open the workspace item it produced?

**One asymmetry makes this far stronger than the public funnel: chat is authed.** Every event here carries a real
`user_id`, so the session-only decision in §2 does not bite. "Did they ever come back and read it, two days
later?" is answerable for chat *without* any persistent anonymous identifier — the account already is the
identity, and it is one the user knowingly created.

### Events

| Event | Fired when | Key props |
|---|---|---|
| `chat_send` | Message submitted | `conversation_id`, `message_id`, `family`, `has_attachment` |
| `run_first_token` | First `token` SSE arrives | `ms_since_send` |
| `run_done` | `done` SSE arrives | `ms_since_send`, `was_visible` |
| `run_failed` | `error` SSE | `ms_since_send`, `stage` |
| `run_paused` | `agent_question` SSE — agent asked, awaiting reply | `ms_since_send` |
| `tab_hidden` | `visibilitychange → hidden` | `run_state`, `ms_since_send`, `stage` |
| `tab_visible` | `visibilitychange → visible` | `ms_hidden`, `run_state` |
| `page_leave` | `pagehide` with `persisted === false` | `run_state`, `ms_since_send` |
| `answer_seen` | Assistant bubble ≥50% visible for ≥1s **after** `done` | `ms_since_done` |
| `wi_created` | `workspace_item_created` SSE | `wi_id`, `kind` |
| `wi_opened` | `WorkspaceCard` `onClick` | `wi_id`, `kind`, `ms_since_created` |
| `wi_dwell` | Viewer closed | `wi_id`, `dwell_ms` |
| `conversation_opened` | A conversation is loaded | `conversation_id`, `has_unseen_answer` |

`run_state` is the field the whole question turns on — `in_flight` | `paused` | `completed` | `idle`. It is what
separates *left before the answer* from *left after reading it*, on the exact same browser event.

### The five outcomes, derived

| Outcome | Signature |
|---|---|
| **Waited and read it** | no `tab_hidden` between `chat_send` and `run_done`, then `answer_seen` |
| **Left and came back** | `tab_hidden(in_flight)` → `tab_visible` → `answer_seen`. `ms_hidden` = how long they were gone |
| **Left, never came back (this session)** | `tab_hidden(in_flight)`, no `tab_visible`, no `page_leave` |
| **Closed it outright** | `page_leave(in_flight)` |
| **Came back later, new session** | `conversation_opened` by the same `user_id` after `run_done`, in a later session |

The last row is the one that reframes the other four: a user who closes the tab during a five-minute
deep_search and reads the answer the next morning has **not** churned, and a metric that counts them as
abandoned would push you to optimise the wrong thing.

### Metrics this yields

- **Wait tolerance** — distribution of `ms_since_send` at the *first* `tab_hidden(in_flight)`. Literally "how
  long will people stare at the progress bar before giving up", split by `family`. This is the number that says
  whether the +3–5 min deep_search expectation note is working.
- **Abandon-by-stage** — `stage` on that event, joined to `agent_progress`, i.e. *which* stage loses people.
- **Return rate** and **return latency** for in-flight abandoners.
- **Answer-seen rate** — `answer_seen` ÷ `run_done`. The honest denominator for "was this answer worth
  generating", and directly a cost question given the ledger in `llm_calls`.
- **Paused-run abandonment** — `run_paused` with no reply. The agent asked a clarifying question and the user
  never answered; that is a distinct and expensive failure worth its own number.
- **WI click-through** — `wi_opened` ÷ `wi_created`, by `kind`, plus dwell.

### Capture points

| File | Hook |
|---|---|
| `frontend/hooks/use-chat.ts` | The SSE switch already has every case needed: `message_start`, first `token`, `done`, `error`, `agent_question`, `workspace_item_created`. One `track()` per case. |
| `frontend/components/chat/ChatInput.tsx` | `chat_send` at submit — before the POST, so a quota block is still measured. |
| `frontend/components/workspace/WorkspaceCard.tsx` | `wi_opened` in the existing `onClick(item.item_id)` — the single funnel point for opening a WI. |
| `frontend/components/workspace/WorkspaceItemViewer.tsx` | `wi_dwell` on close. |
| A `useRunVisibility()` hook in `lib/analytics/` | Owns `tab_hidden` / `tab_visible` / `page_leave`, reading current `run_state` from `chat-store`. Single owner, so visibility is never double-counted by two components. |

---

## §4 Schema — migration `138_analytics_events.sql`

```sql
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

create index on public.analytics_events (occurred_at desc);
create index on public.analytics_events (session_key);
create index on public.analytics_events (event_name, occurred_at desc);
create index on public.analytics_events (path) where path is not null;
create index on public.analytics_events (user_type, occurred_at desc);
-- The chat-depth queries (§6b) all walk one run's events in order.
create index on public.analytics_events ((props->>'message_id'), occurred_at)
  where props ? 'message_id';

alter table public.analytics_events enable row level security;
-- ZERO policies = deny-all for anon AND authenticated; service_role bypasses RLS.
-- The 118 lockdown posture, same as unsent_messages / library_unlocks / payment_methods.
-- Do NOT add a policy here: behavioural data has no user-facing read.
```

**Retention: 90 days**, enforced by a purge job. `backend/app/main.py` already runs an APScheduler with
`cleanup_old_pdf_attachments` and `purge_expired_accounts` — this is one more entry, not new infrastructure.
Raw events past a quarter answer nothing that a rollup cannot; keeping them is pure PDPL exposure.

---

## §5 Capture points

### 5.1 Client library — `frontend/lib/analytics/`

| File | Responsibility |
|---|---|
| `session.ts` | Get-or-create the `sessionStorage` key (`rayhan_analytics_v1`). Fails closed exactly like `anon-cta/session.ts` — unusable storage ⇒ **tracking silently off**, never a crash and never an unkeyed event. |
| `client.ts` | `track(name, props)`. Buffers events and flushes via `navigator.sendBeacon` (falls back to `fetch(…, {keepalive:true})`). Fire-and-forget: a failed flush is dropped, never retried, never surfaced. |
| `events.ts` | The typed event names + prop shapes from §3. One place, so a typo is a compile error rather than a silent hole in a funnel. |

### 5.2 `<AnalyticsTracker />` — mounted in `frontend/components/providers.tsx`

`Providers` is already a client component wrapping the whole app in the root layout, so one mount covers every
route — public wings, blog, chat, checkout. (Note `AnonCtaPopup` is *not* global; it is mounted per-shell in
`LibraryPageShell` / `BlogPageShell`. The tracker must not copy that pattern or it will miss the authed app.)

Responsibilities: `session_start` once per tab · `page_view` on every `usePathname()` change · `page_exit` on
`visibilitychange → hidden` carrying dwell and max scroll depth.

### 5.3 Gate instrumentation — the question-4 wiring

| File | Change |
|---|---|
| `components/marketing/AnonCtaPopup.tsx` | `gate_view` where `recordImpression()` already succeeds; `gate_cta_click` inside the existing `handleCtaClick`; `gate_dismiss` in `handleOpenChange(false)` when no CTA was clicked. **All three moments already exist in this file** — this is three `track()` calls, not new logic. |
| `components/library/FullContentGate.tsx`, `blocks/GateBanner.tsx`, `hub/HubCtaWall.tsx`, `blog/BlogConversionCta.tsx`, `search/SearchCtaModal.tsx` | `gate_view` on first visibility via one shared `useGateImpression(gateKind)` hook built on `IntersectionObserver` — these are static surfaces, so "rendered" is not "seen". `gate_cta_click` on their CTA links. |

### 5.4 Signup funnel

`app/login/page.tsx` — `signup_started` when `mode=register`, carrying `next` so a signup can be attributed back
to the page whose gate sent them. `signup_completed` on success. Same session ⇒ the join works.

### 5.5 Backend — `POST /api/v1/public/events`

New `backend/app/api/analytics.py`, modelled on `public_ask.py`:

- No `Depends(get_current_user)` — anonymous by design. Reads the bearer token opportunistically to fill
  `user_id` when present.
- Accepts a **batch** (`{events: [...]}`, max 20) — `sendBeacon` fires once per flush, not once per event.
- **Derives** `device_type` / `browser` / `os` from `Sec-CH-UA-Mobile` (a single reliable boolean Chrome sends by
  default) with a small in-repo UA-regex fallback for Safari/Firefox. Discards the raw string. No new dependency
  — see T7.
- IP-keyed rate limit via the existing `resolve_client_ip`; the IP is never persisted.
- **Drops the batch entirely when `x-verified-bot: 1`** — see T1.
- Returns `204` always. Analytics must never surface an error to a reader.

---

## §6 The queries it must answer (acceptance criteria)

The build is done when these run against `analytics_events` and return sane numbers:

1. Mobile vs desktop share, overall and per wing.
2. Browser and OS breakdown.
3. Top 20 entry pages; top 20 exit pages.
4. Bounce rate overall and per entry page.
5. Referrer host / utm_source breakdown of sessions.
6. **Gate abandonment: for each `gate_kind` and each `path`, sessions that saw a gate ÷ sessions that clicked
   through** — ranked by lost sessions. This is the deliverable that pays for the rest.
7. Signup funnel: `gate_view` → `gate_cta_click` → `signup_started` → `signup_completed`.

8. **Authed vs anonymous split** on every one of the above.

### §6b Chat depth

9. Wait-tolerance distribution (p50/p75/p95 of `ms_since_send` at first `tab_hidden(in_flight)`), by `family`.
10. Of runs abandoned in flight: what share returned in-session, returned in a later session, or never returned.
11. Answer-seen rate — and its inverse, **answers generated but never read**, joined to `llm_calls` for the
    money those runs cost.
12. Abandonment by deep_search `stage`.
13. Paused-run (`agent_question`) abandonment rate.
14. WI click-through and dwell by `kind`.

Ship these as `scripts/analytics_queries.sql` so they are reproducible rather than retyped each time.

---

## §7 Traps

**T1 — Googlebot renders JavaScript.** It will execute the tracker and pollute every metric, and the library
wings are built for crawlers. Drop on `x-verified-bot: 1` (Cloudflare's `cf.client.bot`, already wired for
`public_library.is_verified_crawler` — see `lib/library/crawler-signal.ts`). Note `AnonCtaPopup` sidesteps this
today only because Googlebot doesn't scroll; `page_view` has no such natural immunity.

**T2 — never call the beacon from a server component.** The library runs ISR with a **shared** cache; anything
server-side is baked into the page every subsequent visitor receives. `AnonCtaPopup`'s header carries this rule
as "⚠ T9 — PURE CLIENT, PERMANENTLY". The tracker inherits it. Client-side beacons are fine — they run after
hydration and cannot enter the bake.

**T3 — do NOT reuse `rayhan_ask_session`.** `lib/library/ask.ts` keeps that key in **localStorage**, i.e. it is
durable across sessions, because claiming your answer after signup needs it to be. Reusing it for analytics
would silently convert this into persistent visitor tracking and break §2 without anyone noticing. Analytics
gets its own **sessionStorage** key.

**T4 — never send the raw query string.** `?q=` on the navigation search surfaces is user-typed legal text in a
product for lawyers. Send `path` only. (Capturing `q` later is a real product opportunity, but it is a
`/privacy` change and a separate decision.)

**T5 — `page_exit` must use `visibilitychange`, not `unload`.** `unload` does not fire reliably on mobile Safari
— exactly the population question 1 is about. `sendBeacon` on `visibilitychange → hidden` is the only
combination that survives a backgrounded tab.

**T6 — a gate that rendered is not a gate that was seen.** `GateBanner` and `HubCtaWall` sit far below the fold.
Counting renders would report a huge fake denominator and make gate conversion look catastrophic. Use
`IntersectionObserver`, as `AnonCtaPopup`'s own `whenAnonCtaVisibility` already does.

**T7 — think twice before adding a UA-parsing dependency.** The backend has an unresolved dependency
reproducibility concern and now builds from `requirements.lock`. `Sec-CH-UA-Mobile` plus ~40 lines of regex
covers mobile/tablet/desktop and the six browsers that matter; a parser library is a lot of surface for the
remainder.

**T8 — CSP and origin-lock need no change, and that is worth verifying rather than assuming.** `connect-src`
in `next.config.mjs` already lists `https://api.rayhanai.com` (and localhost in dev). The beacon must go through
the edge like all browser traffic — it must **not** be added to `origin_lock`'s `EXEMPT_PATHS`.

**T9 — analytics must never break a page.** Every call site wrapped so a storage failure, a blocked beacon, or a
503 is a no-op. A reader must never see a degraded page because a tracker failed.

**T10 — deploy order.** Migration 138 must be applied **before** the code that writes to it ships, or the first
beacon 500s in a loop. This repo has been bitten by migration-after-deploy before (see the Moyasar and
free-window ladder work).

**T11 — "closed the tab" is not perfectly observable, and the plan must not pretend otherwise.** `pagehide` is
the only real close signal, and mobile Safari drops it often enough to matter — on the exact population most
likely to background a long run. So *closed* and *left it backgrounded forever* are one bucket in practice,
distinguishable only when `page_leave` happens to arrive. Report them as **"did not return in this session"**
with `page_leave` as a confirmed-close subset, and never quote a bare "closed the tab" number as if it were
precise.

**T12 — `pagehide` with `persisted === true` is bfcache, not a departure.** The user tapped back and the page is
frozen for reuse; counting it as a close inflates abandonment. Check the flag.

**T13 — background tabs are throttled, so never measure elapsed time with a timer.** `setInterval` is clamped to
≥1s (often far worse) in a hidden tab, which is precisely when these measurements are taken. Every `ms_*` prop
is computed as a difference of `Date.now()` stamps captured at the events themselves.

**T14 — a stream reconnect is not a new send.** `use-chat.ts` reconnects with exponential backoff, and this repo
already has a documented auto-reconnect re-POST trap plus per-conversation in-flight send dedup. `chat_send`
fires on **user submit only**, never on the SSE (re)connection, or wait-tolerance will be measured against the
wrong t₀ and every reconnect will look like a fresh question.

**T15 — "rendered" is not "seen", again — and here it is the whole metric.** The assistant bubble may mount far
below the fold, or while the tab is hidden. `answer_seen` requires an `IntersectionObserver` **and**
`document.visibilityState === "visible"`, held for ≥1s. A bubble that streamed into a backgrounded tab was
never read, and counting it as read would quietly invert the one number this section exists to produce.

**T16 — do not emit an event per `token` or per `agent_progress`.** A long deep_search run emits thousands;
that is a firehose into the beacon, the table, and the reader's battery. Progress is sampled only at the moments
that matter: first token, stage transitions, and whatever stage was current when the user left.

---

## §8 Phases

| Phase | Scope | Answers |
|---|---|---|
| **0** | Migration 138 · beacon endpoint · client lib · `<AnalyticsTracker/>` · purge job | infrastructure |
| **1** | `session_start` / `page_view` / `page_exit` | Q1, Q2, Q3, Q5 |
| **2** | Gate events across all seven surfaces + signup funnel | **Q4** |
| **3** | **Chat depth (§3b)** — run lifecycle, visibility, `answer_seen`, WI engagement | **wait tolerance, abandonment, did-they-read-it** |
| **4** | `scripts/analytics_queries.sql` + a `/learn`-style internal readout | makes it usable |
| **5** | *(not adopted)* durable **anonymous** visitor ID | returning anonymous visitors, cross-session attribution on the public wings — **requires a `/privacy` update and probably a consent banner; explicitly deferred.** Note this does not block §3b: chat already has `user_id`. |

Phases 2 and 3 are the ones you asked for; Phase 1 is their denominator, which is why it comes first.

---

## §9 Open questions for review

1. **`quota_blocked` overlaps `unsent_messages`** (135), which already records authed users hitting a limit.
   Emit the event anyway for one uniform funnel, or read the existing table and skip the event?
2. **90-day retention** — right number? Chat-depth cohorts get thin faster than pageview aggregates, so a longer
   window (180d) may be worth it for §3b specifically.
3. **`answer_seen` threshold** — ≥50% visible for ≥1s is a defensible default, but it is a judgement call and it
   sets the headline "answers never read" number. Worth agreeing before it becomes a metric people quote.
