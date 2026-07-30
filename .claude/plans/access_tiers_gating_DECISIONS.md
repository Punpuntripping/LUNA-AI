# Access Tiers — Implementation Decisions (2026-07-27)

Companion to [`access_tiers_gating.md`](access_tiers_gating.md). **Read the plan first, then this.**
Where the two disagree, THIS FILE WINS — it resolves ambiguities against the live codebase
and live DB, verified 2026-07-27.

---

## BUILD STATUS — 2026-07-27

**Phases A · B · B2 · C are BUILT. NOT committed, NOT deployed.**
Migrations 103–107 ARE applied to prod (additive and safe).

| Check | Result |
|---|---|
| `pytest backend/tests` | **510 passed**, 2 failed |
| those 2 failures | `test_wave_8b_legacy_removal.py` — pre-existing; `agents/agent_writer/publisher.py` is absent from disk **and from `HEAD`**. Unrelated to this work. |
| `test_deep_search_artifact_persist.py` | pre-existing **collection** error (`No module named 'agents.state'`) — must be `--ignore`d, not merely tolerated |
| `cd frontend && npx tsc --noEmit` | **clean (exit 0)** |
| `npx next lint` | clean |
| `npx next build` | exit 0 |

New durable test suites (all re-included in `.gitignore`, which ignores `backend/tests/*`):
`test_library_gating.py` · `test_library_enforcement.py` · `test_library_mine.py` ·
`test_rate_limit_library.py` · `test_reference_source.py` · `test_blog_snapshot_gating.py`

**Not done / deliberately out of scope:** Phase D (publish ramp — expand the open tier to
300–500, sitemap waves, GSC) and Phase E (Cloudflare/edge) — the edge stack is not
finalized. PART 10's remaining user items still stand: open-tier list approval, gate-card
copy review (drafted, see D10 + the agents' string inventories), GSC DNS TXT, PDPL audit.

## D-INT. Cross-layer contract audit (2026-07-27) — 0 wire mismatches, 6 frontend defects, ALL FIXED

Six agents built this in parallel against a written spec rather than against each other's
code. An end-to-end audit of all eight contracts found **zero wire-shape mismatches** — the
pinned-interface approach held — but six frontend-side defects, since fixed:

1. **429 was indistinguishable from a network error on `/library/full`.** It shares ONE
   20/min budget with the reference-source endpoint, so opening two reference dialogs then
   hitting a reveal reaches it in normal use — and the reader was told their *connection*
   was broken. `rateLimitedCopy` already existed («لم يُحتسب أي مصدر من رصيدك») but only the
   chat surface used it. Added `'rate_limited'` to `FullContentError` and wired it.
2. **404 rendered as a retryable transport error** with a retry button that can never
   succeed (unknown slug, unpublished form, vanished corpus row). Now `sourceUnavailableCopy`.
3. **The مكتبتي beacon was the only library client call routed through `apiFetch`**, whose
   401 path does `window.location.href = "/login"` — and it fires on PUBLIC document pages.
   A stale token plus a failed refresh would eject a reader off `/regulations/{slug}` over a
   fire-and-forget shelf write. Now plain `fetch` + bearer, failure swallowed, matching its
   four siblings (`fetchFullContent`, `fetchLibraryBalance`, `fetchAuthedHubPage`,
   `getReferenceSource`).
4. **`FullSection.title` and `FullArticle.sharh_md` were typed non-nullable** while the wire
   sends `null`. Fixing the types immediately surfaced a real latent bug: `section.title` was
   being passed to a `string | undefined` prop. Exactly the failure the honest type prevents.
5. **Four docstrings still asserted the PRE-revision D16.2 rule**, contradicting shipped
   behaviour — the highest-risk item on the list, since the next agent would have "restored"
   the double-counting. Rewritten in `library_mine.py`, `library_items_service.py`,
   `lib/api.ts`, `hooks/use-my-library.ts`.
6. **`UsageLimitsDialog` suppressed the library bar's `resets_at` at zero usage.** `BarRow`
   is shared with the rolling points/OCR meters, whose `used === 0` branch shows «متاحة
   بالكامل» — defeating the one behaviour the backend implements specifically for this meter.
   Added a `fixedWindow` prop: a calendar/subscription window has a real boundary at zero
   usage; a rolling window anchored on first use does not.

## D-SEC. Security review (2026-07-27) — 0 CRITICAL, 0 HIGH, 5 MEDIUM, 6 LOW

Clean verdicts on the load-bearing questions: **the ISR cache-leak property holds
end-to-end** (`resolve_gate` never gained a user dimension, hub headers branch before the
CTA-wall early return, the Next fetchers send no `Authorization`, no server component
renders per-user state); the reference-source endpoint enforces workspace ownership before
anything else runs; every `get_full_*` caller is behind `resolve_access`; ledger `user_id` is
always a server-derived `users.user_id`; the route limiter keys off a verified token.
Prod DB verified: RLS on with 0 policies, no `anon`/`authenticated` grants on either table
or either RPC, view widened to 30 columns, limits seeded 10/100/200/1000 + dev NULL.

**Fixed in this pass (migration 108 applied):**
1. **MEDIUM-1 — the last meter bypass.** `POST /forms/{slug}/open-in-writer` copied
   `forms.body_md` verbatim into `user_templates` with **no entitlement check** — the same
   bytes `/library/full/form/{slug}` charges an unlock for, and §1.3 puts form bodies in the
   ALWAYS-GATED class. Any authed account (including exhausted / frozen / plan-less) could
   take every published form for free and read it back from قوالبي. Now runs `resolve_access`
   before the copy, behind the shared reveal limiter. Liability gate order preserved.
2. **MEDIUM-2 — read amplification.** `unlock_cost` paged through every `chunks_v2.content`
   row of a chunk-only نظام *before* the quota check, so an already-exhausted account could
   force 20 full-corpus scans/minute forever at zero cost to itself. Cheap refusal on
   `UNLOCK_COST_MIN` now comes first — a sound lower bound, so it can never wrongly refuse.
3. **MEDIUM-3 — unbounded shelf writes.** `/library/mine/use` performed *no* `content_type`
   validation and no field had a length cap, so an authed account could write ~86k junk
   rows/day into the shared table and corrupt the «الأكثر استخداماً» signal. Validation moved
   into `_resolve_ref` (the single funnel for use/save/unsave), `max_length` on every field,
   plus DB CHECK constraints (migration 108) on **both** tables. Also rejects `,()"` — the
   latent PostgREST `in.(a,b,c)` filter-injection primitive.
4. **MEDIUM-4 — the meter could fail open silently.** `_insert_unlock` returned the same
   `False` for a conflict and for a write ERROR, so the caller reported `already_unlocked`
   and a broken ledger looked exactly like normal re-read traffic. Since `library_unlocks` is
   the sole revenue control, that is total-bypass mode behind one WARNING line. Now returns
   three outcomes; a failure logs at **ERROR** with the stable marker
   `event=library_ledger_write_failed` and surfaces as `reason='ledger_unavailable'`.
   Access is still granted — a DB blip must not paywall a paying customer.
5. **MEDIUM-5 — a 429 turned live pages into 404s for Googlebot.** `fetchJson` returned
   `null` on *any* non-OK, and document routes turn `null` into `notFound()`. Because path
   normalization collapses a whole section into one bucket and all anon traffic arrives
   through the ISR renderer on one IP, a crawl burst or cold cache could 429 and Google would
   record 404s on real pages — a self-inflicted SEO outage on the exact surface the
   publishing programme exists for. Now only a real 404 returns `null`; 429/5xx/network
   **throw**, so Next renders the error boundary and never caches a 404.

**Deliberately not fixed (scheduled, not blocking):** short-circular charge inconsistency
between `resolve_access` and `reference_resolver` (LOW — UI hides the reveal, reachable only
by direct API call); quota TOCTOU allowing over-grant of up to (concurrency−1) under
concurrent reveals of *distinct* items (LOW — over-grant only, bounded by the 20/min
limiter); `purge_user_data` not clearing `library_items`/`library_unlocks` (LOW — the
`ON DELETE CASCADE` chain still removes them at the final GoTrue delete, but reading history
should be purged early and the ledger's retention deliberately decided); `Cache-Control`
absent on hub *error* responses (LOW — no per-user bytes in those bodies today).

## D-OS. «المصادر الرسمية» is GATED — reverses §1.2 and §1.3 (user decision 2026-07-28)

⚠ **This overrides the plan in three places.** §1.2 says "The **official source URL is
always shown**, gated or not (`OfficialSources` renders on gated pages too)"; §1.3 lists
"official source URLs" in the **never gated** class; §5 repeats it. **All three are now
wrong.** The official-sources block is part of what an unlock buys.

**Rationale (the thing that changed the call):** the block is not a generic outbound link to
a public site. It is a per-item deep link carrying the *source system's own identifier* —
`laws.boe.gov.sa/BoeLaws/Laws/LawDetails/<uuid>/1`, or for NCAR-hosted أنظمة an **opaque
encrypted document id** (`ncar.gov.sa/document-details/eyJpdiI6...`). Publishing that across
3,373 regulations hands out a ready-made slug → official-ID crosswalk of the corpus, and the
NCAR ids are not guessable at all. That is a materially different thing from "a link to a
public website", which is what §1.2's "public domain anyway" reasoning assumed.

**Scope:** regulation · judgment · circular — **withheld on EVERY item of those wings,
including OPEN-TIER ones and short (≤800-char) تعاميم.** NOT compliance (never gated at all).
`article` and `form` carry no block of their own (a مادة's parent نظام holds them;
`FormDetail` has no such field).

⚠ **THE TIER EARNS NO EXEMPTION — this was got wrong once.** An earlier pass withheld only
when `resolve_gate(...) == 'gated'`, reasoning that an open item has nothing to protect. But
the thing being withheld is the **crosswalk**, not the body: an open-tier نظام publishes the
same source-system identifier, and its page is the one a crawler reaches first. That pass
left all 54 open-tier أنظمة emitting their crosswalk anonymously, AND rendered
«المصادر الرسمية» **twice** on them — once always-visible from the public page, once again
after the reveal — because an open-tier item still shows a reveal button when its long
sections are truncated. "Open tier" and "has a reveal button" are independent; do not assume
one implies the other.

Withholding unconditionally makes the **authed reveal the single renderer**, so the block
can never appear twice. On an open item that reveal is free (`reason='open'`, no ledger row):
it is *anonymous* access to the crosswalk being closed, not paid access to the link.

Pinned by `test_the_anon_judgment_payload_ALWAYS_withholds_its_official_source`,
`test_an_OPEN_tier_page_still_withholds_its_official_sources`,
`test_an_OPEN_tier_reveal_DOES_return_the_official_sources`.

**Level:** behind the unlock, exactly like body text. Anon → hidden. Signed-in without an
unlock → hidden. Unlocked → shown. No separate charge; it rides the item's existing unlock.

**The architecture that keeps this safe:**
- The anon/ISR payload withholds it at **Layer A** — keyed on the ITEM's gate, never on the
  viewer (`get_regulation_doc`, `get_judgment_doc`, `get_circular_doc` each emit
  `official_sources: []` when gated). So the shared cache never varies by tier and D11 holds
  unchanged.
- The block is served ONLY from the metered reveal, via
  `library_service.official_sources_for_item(supabase, content_type, content_id)` on
  `LibraryFullResponse.official_sources`. Keyed on the resolved `content_id`, not the slug,
  so it agrees with the ledger about which item this is.
- The circular case keys off `gate_effective`, so a short (≤800 char) تعميم — which renders
  fully open to anonymous visitors — keeps showing its source. Nothing about it is metered.
- A failure building the block never breaks a paid reveal: the content the user just
  unlocked matters more than the link to it.

**Frontend:** `<OfficialSources>` returns `null` on an empty list, so the page-level render
needs no change — it simply disappears on gated pages. `FullContentGate` renders the block
itself, inside the revealed branch, from `full.official_sources`.

⚠ **EXACTLY ONE BLOCK MAY RENDER — and the two sides must be disjoint BY CONSTRUCTION.**
`official_sources_for_item` returns `[]` unless `resolve_gate(...) == 'gated'`, i.e. unless
the anon payload withheld them. Without that check an OPEN-TIER نظام rendered
«المصادر الرسمية» **twice** — once always-visible from the public page, once again after the
reveal — because an open-tier item still shows a reveal button when its long sections are
truncated (`gated={doc.gate === "gated" || hidden_section_count > 0 || …}`). "Open tier" and
"has a reveal button" are independent; do not assume one implies the other. Pinned by
`test_an_OPEN_tier_reveal_returns_no_official_sources`.

**Cost noted honestly:** this removes the outbound-authority SEO signal from gated public
pages and means a gated page carries no source attribution at all. That was the user's call
after the trade-off was put to them; the counter-argument (legal credibility, and raw
statute being public domain regardless) is recorded here so the decision can be revisited
with both sides intact.

Pinned by `test_a_GATED_judgment_withholds_its_official_source`,
`test_the_reveal_serves_the_official_sources`, `test_a_refused_reveal_leaks_no_official_source`,
`test_anon_reveal_leaks_no_official_source`.

## D-REVEAL. «عرض المصدر» is METERED, never REMOVED (user correction 2026-07-28)

**The rule:** «عرض المصدر» and the `[n]` source preview behave exactly as they always did —
the click opens the source. The ONLY change Phase C was allowed to make is that opening it
now costs an unlock. Removing the affordance anywhere is a bug, not a gating decision.

**What went wrong.** Phase C gated the reveal on `canReveal = !!itemId && has_source`, and
the two blog views pass no `itemId` (a reader is not the author, so the workspace endpoint's
ownership check 404s them). Net effect: on every public blog post the «عرض المصدر» button
disappeared and `[n]` degraded to scroll-and-flash. Compounding it,
`strip_frozen_source_views` set `has_source: False` on legacy rows, which would have hidden
it on all 95 pre-cutover posts even after the first bug was fixed. Both are fixed:

- **New endpoint** `GET /api/v1/public/blog/{token}/references/{n}/source` — addressed by
  `(token, n)` against the frozen `references_json`. The post's unguessable token IS the
  capability; it already grants the page.
- **Optional auth, so anon gets a 402 not a 401.** An anonymous reader sees the button,
  clicks, and gets «سجّل مجاناً لعرض المصدر». A 401 here would trip the frontend's global
  redirect-to-login and throw them off a public page (D14).
- **Charged against the READER, never the author** — the author published once; each reader
  pays their own way. Pinned by `test_the_blog_reveal_charges_against_the_READER_not_the_author`.
- `strip_frozen_source_views` now keeps `has_source: True` — strip the BODY, keep the FACT
  that a body exists.
- `canReveal` is `(itemId || blogToken) && has_source` and is deliberately **not** gated on
  being signed in.
- Publish now snapshots via `fetch_item_references_payload`, so new posts carry `has_source`.
- One shared `fetchReferenceSource` transport behind both endpoints, so the workspace and
  blog surfaces cannot drift on how 402/404/429 is classified.

Shares the same 20/min budget as every other reveal surface (D13.2), and `private, no-store`.

**The generalisable lesson:** "move X behind the meter" is not a licence to remove X on
surfaces where metering is awkward. If a surface can't charge, that is a question to raise,
not a feature to silently drop.

## D-SHELF. EVERYTHING IN مكتبتي IS UNGATED (user decision 2026-07-28) — SUPERSEDES D16.2

⚠ **This is the settled model. It overrides §5B.2 and every earlier `record_use` rule in
this document, including D16.2 and its own revision.** Read this one.

| Action | Unlock | Shelf |
|---|---|---|
| View a **gated** page | — | — |
| View an **open** item (service · open-tier نظام · ≤800-char تعميم) | — | ✓ free |
| «اعرض النص كاملاً» | ✓ | ✓ |
| «عرض المصدر» | ✓ | ✓ |
| «حفظ» | ✓ | ✓ |
| Downgrade freeze (§5B.4) | — | still listed, lock badge |

**What it reverses.** §5B.2 said opening shelves an item "gated or not" and that «حفظ» is
"free at every tier and grants no access", listing unreachable items with a lock badge as an
intent signal. Both are gone: a save now runs `resolve_access` and is refused (402) if it
cannot unlock, and a gated page view does nothing at all.

**Why the gated page view had to become a no-op.** If everything shelved must be ungated,
and viewing shelved things, then viewing would charge — and a signed-in user skimming ten
judgment summaries would burn ten unlocks, destroying the free summary layer that does the
SEO work (§5.1). So viewing a gated page stops shelving instead.

**The invariant: the beacon and the server endpoints cover DISJOINT sets.**
`LibraryUseBeacon` fires **only** when `gate === "open"` (it defaults to `"gated"`, the safe
direction, so a caller that forgets the prop shelves nothing). `/library/full`, the
reference-source endpoints and `/library/mine/save` record for gated items. Nothing
double-counts, and nothing unreadable reaches the shelf. **If either half changes, change
the other in the same commit** — the disjointness is the invariant, not either half alone.

The one surviving lock badge is the §5B.4 freeze, which a lapsed subscription causes rather
than a shelving action — so it does not violate "everything is ungated".

Pinned by `test_a_charged_reveal_DOES_record_one_use`, `test_a_REFUSED_reveal_shelves_nothing`,
`test_save_is_refused_when_the_quota_is_exhausted`, `test_saving_a_never_gated_service_is_free`.

### D-SHELF.1 «unlocked but unpublished» is the COMMON case on the shelf

Only **100 of 3,373 regulations carry a slug** — the library is still in sample mode — so an
item unlocked from a chat citation usually has **no public library page**. `is_available`
therefore means "hydrated" (we have a title), NOT "linkable"; `url` decides linkability.
Such rows render as normal hub cards via `CardShell href={null}` — an anchor-less card —
instead of the muted «غير متاح» box, which made the shelf look broken for most of its
contents and told the reader something false (they had unlocked it and read it in chat).
Phase D publishing the corpus resolves this wholesale.

Also fixed: the nested-مواد chip said «مادة واحدة» next to a لائحة with 79 مواد, reading as a
claim about the statute. It now says «مادة واحدة محفوظة» — the count is what is on YOUR
shelf.

## D0. Scope

Phases **A, B, B2, C** only. Phase D (publish ramp) and Phase E (Cloudflare/edge) are OUT —
the Cloudflare stack is not finalized. Do not touch DNS, sitemap gating, or edge rules.

Deliverable = built + tested locally. **Do NOT commit, do NOT deploy.** Migrations ARE
applied to prod (they are additive and safe).

---

## D1. Migration numbers shift by +1

The plan says 102/103/104. **`102_workspace_item_references_circulars_domain.sql` already
exists.** Actual numbering:

| Plan calls it | Actual file |
|---|---|
| Migration 102 (plan quota columns) | `shared/db/migrations/103_library_unlock_limits.sql` |
| Migration 103 (unlock ledger) | `shared/db/migrations/104_library_unlocks.sql` |
| Migration 104 (extend RPC) | `shared/db/migrations/105_quota_state_library.sql` |
| Phase B2 `library_items` | `shared/db/migrations/106_library_items.sql` |

---

## D2. Free-tier period = **calendar month, UTC** (user decision)

`period_key = 'free:{YYYYMM}'`. No per-user anchor. PART 10 open item CLOSED.

---

## D3. Real limits from day one (user decision)

No soak step. Migration 103 seeds the real numbers: free 10 · basic 100 ·
marketing_lawyer 100 · pro 200 · max 1000 · dev NULL (unlimited).

---

## D4. Weighted cost is STORED, and the quota SUMs it

§1.2 says the quota is `count(*)`; §1.2.1 (added later, same day) introduces weighted cost.
**§1.2.1 wins.** Resolution:

- `library_unlocks` carries `cost integer NOT NULL DEFAULT 1`.
- Quota used = `COALESCE(SUM(cost), 0)` for the current `period_key`, never `count(*)`.
- Cost function (single source of truth, `library_service.unlock_cost()`):
  ```
  article   -> 1
  judgment  -> 1
  circular  -> 1
  form      -> 1
  service   -> never charged (open by policy)
  regulation-> clamp(ceil(n_articles / 25), 1, 8)
               n_articles = count of seo_articles rows for the regulation.
               If the regulation has no seo_articles rows (chunk-only), weight by body
               length instead: clamp(ceil(total_chars / 25000), 1, 8).
  ```

## D5. Unlocking a نظام implicitly covers all its مواد

§1.2.1: "re-charging when the user clicks into a مادة they just read in the continuous view
is exactly the 'trick' feeling §5.1 forbids."

`resolve_access(user, 'article', '{regulation_id}#{article_no}')` must, before charging,
check for an existing **`('regulation', regulation_id)`** unlock row for that user and, if
the §1.2 access predicate passes on it, return `may_unlock=True, charged=False`.
The reverse does NOT hold — unlocking one مادة does not unlock the نظام.

## D6. The شرح exclusion is an INVARIANT with a regression test

`get_full_regulation` (`library_service.py:3551`) returns `{id,title,text}` sections and
**no `sharh_md`**. Never add it. Phase A ships
`backend/tests/test_library_gating.py::test_full_regulation_never_includes_sharh`
asserting no key named `sharh*` appears anywhere in its payload. §1.2 explains why —
bundling شرح into the continuous payload collapses the moat ~15×.

---

## D7. Live-DB facts the plan did not have

- `plans` columns: `plan_id, name_ar, name_en, points_monthly, points_weekly,
  points_session, ocr_pages_monthly, web_calls_monthly, duration_days, created_at,
  updated_at, price_sar, billing_cycle`. There is **no `display_name`**.
- `user_subscriptions` has **no `status` column** — it was dropped by migration 091/092.
  Do not reference it. Effective plan comes from the `expires_at <= now() -> 'free'`
  fallback already inside `get_user_quota_state`.
- Live plan rows: free (duration NULL) · basic (7d, 49 SAR) · pro (30d, 89) ·
  max (30d, 189) · marketing_lawyer (7d) · dev (60d).
- `seo_item_meta` row counts: regulation 3373 · service 4717 · circular 1843 ·
  judgment 100 · article **5**. There are **no `form` rows**. So most `article` and all
  `form` gate decisions come from `seo_gate_defaults` / the fail-closed fallback in
  `resolve_gate` — never assume a sidecar row exists.
- `get_user_quota_state(p_user_id uuid)` currently returns 18 columns; migration 105 adds
  exactly three more (see D8). `grant_plan` preserves `started_at` across same-plan
  renewals, so `period_index` keeps incrementing correctly.

---

## D8. `period_key` derivation lives in SQL, and only in SQL

**STATUS: migrations 103–106 are WRITTEN AND APPLIED TO PROD (2026-07-27). Verified live.**

Migration 105 extends `get_user_quota_state` with FOUR columns (the plan named three;
`resets_at` was added so Python never re-derives a period boundary either):
- `library_unlocks_limit     integer` — `CASE WHEN ep.plan_id IS NULL THEN 0 ELSE
  COALESCE(s.library_unlocks_override, ep.library_unlocks_period) END`. NULL = unlimited,
  0 = locked account.
- `library_unlocks_used      integer` — `COALESCE(SUM(cost),0)` over the derived period.
- `library_period_key        text`
- `library_period_resets_at  timestamptz` — free → first instant of next UTC month;
  paid → `started_at + (period_index + 1) * duration_days`.

Verified live output: free → `10 / free:202607 / 2026-08-01T00:00:00Z`; dev → `NULL`
limit, `dev:20260626:0`; locked account → limit `0`, key `NULL`, resets_at `NULL`.

`user_subscriptions_live` was dropped and recreated with the widened LATERAL alias list
(it pins the column list, so it breaks if you change the RPC without recreating it).

Derivation, using the **effective** plan `ep` (so an expired sub falls back to the free
calendar month automatically):
```sql
CASE
  WHEN ep.plan_id IS NULL THEN NULL
  WHEN ep.duration_days IS NOT NULL THEN
       ep.plan_id || ':' || to_char(s.started_at AT TIME ZONE 'UTC','YYYYMMDD') || ':' ||
       floor(extract(epoch from (now() - s.started_at)) /
             (ep.duration_days * 86400))::bigint::text
  ELSE 'free:' || to_char(now() AT TIME ZONE 'UTC','YYYYMM')
END
```
Python must **never** re-derive this. Read `library_period_key` off the RPC row.

---

## D9. Meter naming

`shared/quota` uses `Meter = Literal["ocr","ord","web"]` (`redis_store.py:61`) where `"ord"`
is points. Add **`"library"`** to that Literal and to `METERS`. The `/api/v1/usage` report
key is `"library"`, matching the existing `points`/`ocr`/`web` report keys.

`QuotaExceeded(meter="library", period="period", used, limit, resets_at)` — reuse the
dataclass unchanged so `to_event_payload()` and `UsageLimitsDialog` keep working.
Add to `_AR_METER`: `"library": "فتح المصادر"`.

`resets_at` for the library meter is the **end of the current period_key window**, not a
rolling window: free → first instant of next UTC month; paid → `started_at +
(period_index+1) * duration_days`.

---

## D10. Arabic copy (DRAFT — flagged for user review)

Framed as a plan feature, never a scolding. Put every string in ONE place so the user can
edit them in a single pass: `shared/quota/__init__.py` for meter messages,
`frontend/lib/library/gate-copy.ts` for UI cards.

| Key | Draft string |
|---|---|
| quota exhausted (meter msg) | `تم استهلاك رصيد فتح المصادر لهذه الفترة.` |
| gate card title | `رصيد فتح المصادر لهذه الفترة انتهى` |
| gate card body | `يتجدّد رصيدك {reset}. يمكنك الترقية للوصول إلى عدد أكبر من المصادر الكاملة.` |
| gate card CTA | `عرض الباقات` |
| anon reveal CTA | `سجّل مجاناً لعرض النص كاملاً` |
| authed reveal CTA | `اعرض النص كاملاً` |
| balance chip | `متبقٍ {n} من {limit} مصدراً هذه الفترة` |
| unlimited balance chip | `فتح غير محدود` |
| frozen item badge | `محفوظ في مكتبتك` |
| frozen upgrade CTA | `لديك {n} مصدراً محفوظاً في مكتبتك — رقِّ باقتك لفتحها من جديد.` |
| مكتبتي empty | `لم تفتح أي مصدر بعد. كل ما تفتحه أو تحفظه سيظهر هنا.` |

**Always render `OfficialSources` on gated pages** (§1.2). The official URL is never gated.

---

## D11. Layer discipline — the correctness property

- `resolve_gate` **must not gain a tier/user parameter**. `_gate_defaults_cache`
  (`library_service.py:108`) and `_published_ids_cache` (`:132`) are global, time-keyed and
  would be poisoned across users. (PART 9 trap 1.)
- `resolve_access` (Layer B) runs ONLY on endpoints that set `Cache-Control:
  private, no-store`. Never memoize it. Never call it from a server component.
- Per-user bytes reach the browser ONLY through the client-side authed fetch.
- Any hub endpoint that saw a token must switch to `private, no-store`. Today the header is
  set at the TOP of each hub handler (`public_library.py:655,746,811,882,954`) — move/branch
  it so an authed request never lands in the shared 1-hour cache. (PART 9 trap 2.)

---

## D12. `hub_page_allowed` signature change

`library_service.py:533` → `hub_page_allowed(page: int, tier: str) -> bool` where
`tier ∈ {'anon','free','paid'}`:

| tier | max page |
|---|---|
| `anon` | 1 |
| `free` | 3 |
| `paid` (basic/pro/max/marketing_lawyer/dev) | unbounded |

Keep `ANON_HUB_MAX_PAGE = 1` as the anon constant and add `FREE_HUB_MAX_PAGE = 3`.
`max_anon_page` on the hub response models must report the caller's actual cap, not the
anon constant — the frontend uses it to decide the CTA wall. Rename the response field to
`max_page` and keep `max_anon_page` as a deprecated alias for one release so the frontend
does not break mid-build.

All five call sites (`658, 749, 814, 885, 957`) adopt `Depends(get_current_user_optional)`.

---

## D13. Rate limiter — three changes, no Cloudflare assumptions

Cloudflare is NOT finalized, so **do not** switch client-IP extraction to
`CF-Connecting-IP` (traps 10/11 are deferred to the edge track). Do:

1. **Path normalization in `RateLimitMiddleware`** — before building the key
   (`rate_limit.py:107`), collapse the dynamic tail of known library prefixes so
   `/api/v1/public/library/regulations/<slug>` shares one bucket with every other slug.
   Normalize: `/api/v1/public/library/{section}/*` → `/api/v1/public/library/{section}/:item`
   and `/api/v1/library/full/{type}/*` → `/api/v1/library/full/{type}/:item`.
2. **Route-scoped limiter dependency** for `/library/full/*` and the new reference-source
   endpoint: **20/min**, keyed off the **verified** `AuthUser` (never the unverified JWT
   `sub` the middleware uses — trap 11).
3. **Fail-closed for the library family only.** When Redis is unavailable, the library
   route-scoped limiter falls back to a conservative in-process limiter
   (per-process token bucket, 20/min/user). Chat and everything else keep failing open —
   do not change that.

---

## D14. Refusal payload shape (backend → frontend contract)

One shape everywhere a reveal is refused. HTTP **402**.

```json
{
  "error": {
    "code": "LIBRARY_QUOTA_EXCEEDED",
    "message": "تم استهلاك رصيد فتح المصادر لهذه الفترة.",
    "status": 402
  },
  "detail": "تم استهلاك رصيد فتح المصادر لهذه الفترة.",
  "reason": "quota_exhausted",
  "used": 10,
  "limit": 10,
  "resets_at": "2026-08-01T00:00:00Z",
  "stored_count": 42
}
```
`reason` ∈ `anonymous` | `quota_exhausted` | `frozen_library` | `unresolvable`.
`stored_count` is the user's total `library_unlocks` row count, present only for
`frozen_library` (drives the §5B.4 upgrade CTA). Anonymous refusal is **401-free** — it
returns 402 with `reason='anonymous'`, because `/library/full` is reached from public pages
and a 401 would trip the frontend's global redirect-to-login.

Matching `ErrorCode` entries go in `backend/app/errors.py`.

---

## D15. Phase C — `ref_id` resolver, fail closed

Per §6.3, verified shapes (`references_service.py:207-233, 396-423`):

| `ref_id` | Resolution |
|---|---|
| `reg:<uuid>` | uuid is a **`chunks_v2` id**. Read `chunks_v2.regulation_id` and `chunks_v2.owns`. `owns` is jsonb of shape `{"MADDA": [6]}` / `{"MADDA": [7,8,9]}`. If `owns['MADDA']` has **exactly one** entry → `('article', '{regulation_id}#{article_no}')`; otherwise → `('regulation', regulation_id)` |
| `case:<case_ref>` | `cases.case_ref` → `cases.id` (uuid). `('judgment', cases.id)` — confirmed against `scripts/build_judgment_slugs.py:405`, which writes `cases.id` as the sidecar `content_id` |
| `circular:<uuid>` | `('circular', uuid)` |
| compliance (bare sha1) | services are **never gated** → free, never charged, never a ledger row |

Anything else, or an unresolvable id → **refuse** (402, `reason='unresolvable'`). Never
fail open.

### D15.1 Verified corpus shape — the majority case is `regulation`, not `article`

Live counts (2026-07-27): 50,923 مواد across **11,455 distinct chunks**, of which only
**2,140 chunks own exactly one مادة**. So ~81% of `reg:` citations resolve to
`content_type='regulation'`, not `article`.

That is **correct and intended**, not a bug — but implement it knowingly:
- Charging at regulation granularity costs `clamp(ceil(n/25),1,8)` (median نظام = 18 مواد
  → **1**), and the resulting ledger row grants the **whole نظام**, including
  `/library/full/regulation/{slug}` and every مادة under D5.
- So a typical chat-citation reveal costs 1 unlock and hands over an entire statute. This
  is the §1.2.1 trade-off already accepted by the user, applied consistently: a user who
  paid regulation price gets regulation access. Do NOT "fix" it by charging regulation
  price for chunk-only access — that is exactly the trick feeling §5.1 forbids.
- The UI must therefore say what was unlocked: after a `reg:`-backed reveal, the balance
  chip / toast names the نظام, not the chunk.

---

## D16. Phase B2 — the two tables do different jobs

- `library_unlocks` is **money**: insert-once via `ON CONFLICT DO NOTHING`, never updated,
  never read for behaviour.
- `library_items` is **behaviour**: upserted on every use, `use_count` incremented.

A page view must never write to `library_unlocks`. The `use_count` upsert must never run in
a cached server render (§5B.3 ISR trap) — it rides the authed client call: the reveal
request for gated items, `POST /library/mine/use` for free ones. One use counts exactly
once — a gated reveal records its use **inside** the reveal handler and the frontend must
NOT also fire the beacon.

Explicit save is free at every tier and grants no access.

---

## D16.1 PINNED INTERFACES — Phase A is BUILT (2026-07-27). Code against these exactly.

Do not redesign, rename or re-derive any of this. It exists and is tested (52 tests).

```python
# backend.app.deps
async def get_current_user_optional(request, credentials=Depends(_bearer_scheme)
) -> Optional[AuthUser]          # None on 401 · RE-RAISES 503 (auth down ≠ anonymous)

# shared.quota
@dataclass
class LibraryQuotaState:
    limit: Optional[int]; used: int; period_key: Optional[str]
    resets_at: Optional[datetime]; effective_plan_id: Optional[str]
    locked: bool; is_paid: bool
    remaining -> Optional[int]        # property; None = unlimited
    has_room(cost: int) -> bool
async def library_state(supabase, user_id: str) -> LibraryQuotaState
LIBRARY_QUOTA_EXHAUSTED_AR: str

# backend.app.services.library_service   (Layer B block, appended after hub_page_allowed)
@dataclass
class AccessDecision:
    may_unlock: bool; charged: bool; reason: str
    cost: int = 0; used: int = 0; limit: Optional[int] = None
    resets_at: Optional[datetime] = None; stored_count: int = 0
def  unlock_cost(supabase, content_type, content_id) -> int                  # SYNC (run_db)
def  parent_regulation_of_article(content_id, parent_regulation_id=None) -> Optional[str]
async def resolve_access(supabase, user_id: Optional[str], content_type: str,
                         content_id: str, *, surface: str = "library",
                         parent_regulation_id: Optional[str] = None) -> AccessDecision
async def stored_library_count(supabase, user_id: str) -> int

# backend.app.errors
LIBRARY_REFUSAL_STATUS = 402
def library_refusal_response(decision) -> JSONResponse   # sets Cache-Control: private, no-store
def library_refusal_payload(decision) -> dict            # duck-typed on .reason etc.
```

`reason` values actually produced: `open` · `already_unlocked` · `granted` · `anonymous` ·
`locked` · `quota_exhausted` · `frozen_library` · `unresolvable`. **D14's list omitted
`locked`** — it is real (no plan assigned; `period_key` is NULL so no ledger row is even
possible) and maps to `LIBRARY_QUOTA_EXCEEDED` with the «حسابك غير مفعّل بعد…» string.

`resolve_access` **returns** a decision and never raises. PART 4.2's
`check_library_unlock` and its "raises `QuotaExceeded`" control flow are **dead spec** —
a refused reveal must return a 402 body carrying `reason`/`resets_at`/`stored_count`, not
abort a stream. Nothing should import `check_library_unlock`.

`user_id` everywhere is a **`users.user_id`**, never an `auth_id`. Map with
`case_service.get_user_id(supabase, current_user.auth_id)`.

Two counts legitimately differ and must not be conflated: `stored_library_count` counts
**rows** (shelf inventory → «لديك {n} مصدراً»), the quota counts **SUM(cost)**.

## D16.2 PINNED INTERFACE — `library_items_service` (Phase B2 builds it, Phase B/C call it)

Agreed signature so three agents compose. B2 **creates** it; B and C **call** it.

```python
# backend.app.services.library_items_service        (NEW FILE, owned by the B2 agent)
async def record_use(supabase, user_id: str, content_type: str, content_id: str) -> None
    """Upsert the مكتبتي shelf row and increment use_count. Idempotent per call, NOT
    deduped — each call is one use. Never raises to the caller: a shelf-write failure
    must never break a content read."""
```

**REVISED 2026-07-27 after the beacon landed — this supersedes the "gated → inside the
reveal handler" rule stated earlier in this section.**

§5B.2 shelves an item when it is **OPENED, "gated or not"**. So:

| Surface | Who records | Why |
|---|---|---|
| Any public document page | `LibraryUseBeacon` on mount (authed only) | The page view IS the use. Fires for gated and open items alike, which is what §5B.2 says. |
| `/library/full` reveal | **nobody** | The reveal always happens ON a document page that already recorded the visit. |
| Workspace reference source | the endpoint | No document page is involved, so nothing else can record it. |

The earlier split (beacon for open, reveal handler for gated) was wrong in two ways: a
gated page the user never revealed was never shelved at all — contradicting "gated or
not" — and once the beacon was wired, every gated reveal would have counted **two** uses
against one for an open item, systematically biasing «الأكثر استخداماً» toward gated
content. Pinned by `test_a_charged_reveal_records_NO_use`.

`public_library._record_library_use` is retained but unused — it is the correct helper if a
future non-page reveal surface appears.

## D16.3 Traps found DURING the build (2026-07-27) — none of these are in the plan

1. **PEP 563 silently 422s every rate-limited route.** `library_rate_limit` is a callable
   INSTANCE. FastAPI resolves a dependency's annotations via
   `getattr(call, "__globals__", {})`; an instance has no `__globals__`, so with
   `from __future__ import annotations` in `route_limits.py` every annotation stays an
   unevaluated ForwardRef, `request: Request` is reclassified as a **query parameter**, and
   every call returns `422 {"loc":["query","request"]}`. **`route_limits.py` must never
   regain that import.** Pinned by `test_route_limits_annotations_are_runtime_resolvable`.
2. **`.gitignore:19` ignores `backend/tests/*`** with one exception. Every test written for
   this work would have existed locally and vanished at commit. The five access-tier suites
   are now explicitly re-included — add a line for any new one.
3. **The rate-limiter path normalization has an ISR consequence.** Anonymous library traffic
   reaches the backend *through the Next ISR renderer*, so after collapsing item paths,
   every anon cache miss in a section arrives from ONE IP. The collapsed bucket therefore
   cannot distinguish scraper from crawler from reader and is a **runaway-client backstop,
   not an enumeration control** (`LIBRARY_ITEM_RATE_LIMIT`, default 600/min). The real
   bounds are the ledger and the 20/min verified-identity route limiter on the paid bytes.
   Consistent with PART 7: the free layer exists to be crawled.
4. Three latent bugs fixed in passing: a 429 leaving with `X-RateLimit-Remaining: 55`
   stamped by the outer middleware; a ZADD member collision (`str(now)` dedups, undercounting
   concurrent requests on one tick) that only mattered once a whole section shared a bucket;
   and hub endpoints *already* sharing one aggregate IP bucket because pagination rides a
   query param.
5. **Anon is refused `/library/full` even for an OPEN-tier item** — `resolve_access` checks
   `user_id is None` (step 2) before the gate (step 3). Correct: the open item's anon page
   already ships full bytes, so anon loses nothing, and the alternative puts an
   entitlement-evaluated response on a path anon can hammer.
6. **Trap 6 surfaces as 404, not 402.** An unapproved form resolves to `None` before
   entitlement runs, so a Max subscriber gets 404 «النموذج غير موجود» and is never charged —
   stronger than a 402, which would confirm the slug exists.
7. **`get_current_user_optional` re-raises 503**, so during a JWKS outage the public hubs
   503 *for callers sending a token*. Anon callers and Googlebot are unaffected (no
   credentials → immediate `None`, no JWKS touch). Deliberate, but it now applies to public
   pages.
8. Unbounded paid hub depth goes on the wire as `max_page: 9999` because the frontend types
   the field as `number`; the largest real hub is ~375 pages.
9. **PART 6 left a side-channel open: `cross_refs[].content`.** §6.2 says "keep `cross_refs`",
   and §1.3 puts *citation lists (the mesh)* in the never-gated class — but `CrossRef.content`
   carries the **resolved body** of each cross-referenced مادة, up to `MAX_CROSS_REFS_REF`
   (10) per reference, shipped free. Measured live: **21.7% of the post-Phase-C payload**,
   one panel 64.7%. §1.3 puts *regulation article bodies* in the PARTIALLY GATED class, so
   both rules hold at once: the mesh survives intact and the body is cut to
   `CROSS_REF_REFERENCE_FREE_CHARS = 500` — **parity with what the public article page
   already shows anonymously**. Chat now leaks nothing a logged-out visitor cannot read.
   `for_aggregator` is deliberately NOT gated (the model needs full text; that payload never
   reaches the user). Both paths are covered: the reg domain's `cross_refs` (`list[CrossRef]`)
   and the case domain's `referenced_regulations` (**`list[dict]`** — different shape, same
   panel).
10. **95 of 100 published blog posts were an unmetered anon mirror of the corpus.**
   `blog_posts.references_json` is a publish-time snapshot served by the anonymous
   `GET /public/blog/{token}`. New publishes capture no source views, but legacy rows carried
   full case bodies / chunk content / uncapped circulars — **~3.4 MB readable with a share
   link, no account, no meter**. Closed by `blog_service.strip_frozen_source_views()`, applied
   on **read** and on **import-copy**. Stripped on read rather than backfilled: the snapshot is
   the historical record of what was published, a backfill is irreversible, and a read filter
   also covers rows written by an older deployment during a rolling release.
11. **Short circulars must stay free in chat.** A تعميم ≤ 800 chars renders fully open to
   anonymous visitors on `/circulars/{slug}` (`effective_circular_gate`). Charging an unlock
   for the same bytes in chat makes signing in strictly worse than not — the §5.1 trick
   feeling. The resolver returns `always_free=True` for them, reusing `effective_circular_gate`
   verbatim so the two surfaces cannot drift.
12. **`use_count` needed an RPC.** §5B.2's `SET use_count = use_count + 1` is not expressible
   over PostgREST; a read-modify-write loses increments under a double-click. Migration **107**
   ships `record_library_item_use(uuid, text, text)` so the plan's statement runs atomically,
   with the read-modify-write kept only as a fallback. Its `ON CONFLICT` deliberately does not
   touch `source`, so a later open never demotes an explicit «حفظ» pin back to `'auto'`.

## D17. File ownership per wave (avoid collisions)

| Wave | Agent | Owns (exclusive write) |
|---|---|---|
| 1 | backend-A | `backend/app/deps.py`, `shared/quota/__init__.py`, `shared/quota/redis_store.py`, `backend/app/errors.py`, `backend/app/api/usage.py`, `library_service.py` (ONLY the new `resolve_access`/`unlock_cost`/`period` block appended near `hub_page_allowed`), `backend/tests/test_library_gating.py` |
| 2 | backend-B | `backend/app/api/public_library.py`, `backend/app/middleware/rate_limit.py`, `library_service.py` (`hub_page_allowed` only) |
| 2 | backend-B2 | **new** `backend/app/services/library_items_service.py`, **new** `backend/app/api/library_mine.py`, `backend/app/main.py` (router mount only) |
| 2 | backend-C | `backend/app/services/references_service.py`, `backend/app/api/workspace.py`, `agents/deep_search_v4/source_viewer.py` (read-only unless required) |
| 3 | frontend-B | `components/library/FullContentGate.tsx`, `lib/library/full-content.ts`, **new** `lib/library/gate-copy.ts`, `components/library/blocks/GateBanner.tsx`, hub views + `HubCtaWall.tsx`, `components/Settings/UsageLimitsDialog.tsx`, `types/index.ts` (usage types only) |
| 3 | frontend-B2 | **new** `app/library/mine/page.tsx` + `components/library/mine/*` + `hooks/use-my-library.ts`, `lib/api.ts` (new `myLibraryApi` block only) |
| 3 | frontend-C | `components/workspace/ReferencePanel.tsx`, `hooks/use-workspace-item-references.ts`, **new** `hooks/use-reference-source.ts`, `types/index.ts` (Reference type only) |

Two agents editing `types/index.ts` and `lib/api.ts` is tolerable ONLY because each appends
its own clearly-delimited block. Append at the end of the relevant section; never reflow
neighbouring code.

---

## D18. Definition of done per phase

Take these from PART 8 of the plan verbatim, plus:
- `cd frontend && npx tsc --noEmit` clean.
- `python -m pytest backend/tests -q` green (existing suite must not regress).
- New tests: unlock idempotency, weighted cost, نظام-covers-مادة, period_key rollover,
  frozen-library predicate, شرح exclusion, `hub_page_allowed` per tier, `ref_id` resolver
  incl. the fail-closed branch, single-section document still gated.
