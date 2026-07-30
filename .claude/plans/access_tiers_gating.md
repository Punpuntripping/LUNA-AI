# Access Tiers & Metered Gating — anon · free · basic · pro · max

**Status:** PLANNED 2026-07-26 — nothing built.
**Goal:** Publish the full corpus for SEO reach while making bulk extraction bounded,
metered and detectable. Today one free account can pull the entire library; this plan
replaces the binary anon/authed gate with a four-level entitlement system and a
period-scoped unlock ledger that spans BOTH the public library and in-app chat citations.

Companion plans: [`seo_public_library.md`](seo_public_library.md) (what gets published) ·
[`cloudflare_protection.md`](cloudflare_protection.md) (edge/ops track).

---

## Why this exists (verified 2026-07-26)

| Hole | Evidence |
|---|---|
| Rate limiter does not bound enumeration | `backend/app/middleware/rate_limit.py:107` — the Redis key embeds `request.url.path`, so **every distinct item gets its own 60/min bucket**. Breadth-first scraping never trips it. |
| Full library is unmetered for any account | `backend/app/api/public_library.py:1049` — *"Deliberately NO quota/points wiring — reading the full library never costs points."* |
| Chat references ship full text unasked | `backend/app/services/references_service.py:162` → `build_source_view()` embeds `source_view.content` (full case bodies, **uncapped circulars up to 168KB**) in the references-list response, before any user click. |
| Hub depth cap is dead for everyone | `hub_page_allowed(page, is_authed)` is called with `is_authed=False` **hardcoded** at all five hub endpoints (`public_library.py:658,749,814,885,957`). No authed variant exists. |
| Redis blip removes all limits | `rate_limit.py:64-66` — fail-open. |

---

# PART 1 — POLICY (locked)

## 1.1 Access levels

| Level | Plan id | Unlocks / period | Period length | Hub depth |
|---|---|---|---|---|
| Anonymous | — | **0** | — | **1 page** |
| Free | `free` | **10** | calendar month | 3 pages |
| Basic | `basic` | **100** | 7 days (`duration_days`) | unlimited |
| Pro | `pro` | **200** | 30 days | unlimited |
| Max | `max` | **1,000** | 30 days | unlimited |
| Dev | `dev` | NULL = unlimited | — | unlimited |
| Marketing | `marketing_lawyer` | same as `basic` | 7 days | unlimited |

Ladder check (unlocks per SAR): repeat-Basic 2.04 · Pro 2.25 · Max 5.29 — monotonic, so
volume buyers are pushed up the ladder rather than into repeat-Basic.

## 1.2 Quota semantics — READ THIS TWICE (revised 2026-07-27)

**Unlocks are PERMANENT; access to them is contingent on holding a paid plan.**

- Unlocking an item is a **one-time charge**. It is never charged again, in any period.
- The per-period allowance caps **NEW** unlocks only (Pro = 200 new per 30 days).
  Previously unlocked items do not count against it.
- On downgrade or lapse the accumulated library **freezes — it is never deleted**. The
  user behaves as a free account (their free-period unlocks still work).
- On re-upgrade the entire stored library unfreezes at once.
- ~~The **official source URL is always shown**, gated or not (`OfficialSources` renders on
  gated pages too).~~ **REVERSED 2026-07-28 — see `access_tiers_gating_DECISIONS.md` §D-OS.**
  The block is a per-item deep link carrying the source system's own identifier (BOE law
  UUID; opaque encrypted NCAR document ids), i.e. a corpus-wide slug → official-ID
  crosswalk — not the generic public link this line assumed. It is now part of what an
  unlock buys, withheld at Layer A and served from the metered reveal.
- Gating must read as a curated feature, never a paywall slap.

**The single access predicate:**

```
access = row exists AND (current plan is paid OR row.period_key = current period)
```

A paid user reaches every row ever unlocked. A free user reaches only the current
period's rows — which is precisely "behaves as a free account" — while paid-era rows sit
frozen and intact. Re-upgrading flips the first clause true and restores everything. No
`plan_at_unlock` column is needed; the predicate covers every case.

Implementation: `UNIQUE (user_id, content_type, content_id)` — one row per user per item,
forever. `ON CONFLICT DO NOTHING` makes re-opens free permanently. `period_key` is
demoted to recording *which* period was charged, and the quota is
`count(*) WHERE user_id = ? AND period_key = <current>`.

⚠ **Security delta, named explicitly.** This reverses the anti-accumulation property of
the earlier period-reset model. Max at 1,000 new unlocks/month reaches the full corpus in
~85 months (~16,000 SAR). Freezing on downgrade is a **retention** mechanism, not an
anti-copy control — it cannot un-copy what a user already read. The real extraction bound
is now the per-period RATE alone. Accepted trade-off (user decision 2026-07-27) for a
materially better product story.

⚠⚠ **The ~85-month figure above is WRONG for regulations — corrected 2026-07-27.** It
assumes the unlock unit is one مادة. But `/library/full/regulation/{slug}` returns EVERY
مادة of a نظام untruncated for the SAME one unlock (`library_service.py:3551-3563`), so a
rational extractor only ever charges at `content_type='regulation'`: **3,373 unlocks ≈ 3.4
periods on Max ≈ ~640 SAR** for the whole statutory corpus (50,923 مواد across 1,794
regulations, median 18/نظام, max 716). 25× cheaper than stated. See
[`defence_in_depth.md`](defence_in_depth.md) §10 for the live numbers and the fix.

**The saving grace, which must become an invariant:** `get_full_regulation` returns
`{id, title, text}` sections and **no `sharh_md`** — شرح is reachable only one-مادة-at-a-
time via `/library/full/article`, which keeps the AI layer at ~50,923 unlocks (~9,600 SAR).
Raw statute is public domain; the شرح is Rayhan's. **Never bundle شرح into the continuous
regulation payload** — a "continuous reading with شرح" feature would collapse the moat ~15×.
Add a regression test asserting the exclusion.

### 1.2.1 Unlock cost is WEIGHTED, not flat (added 2026-07-27)

One unlock must not mean both "a paragraph" and "a 716-article statute":

```
cost(article)    = 1
cost(regulation) = clamp(ceil(n_articles / 25), 1, 8)   -- chunk-only regs: weight by body length
cost(judgment | circular | form) = 1
```

The median نظام (18 مواد) still costs 1, so the common case is unchanged. Unlocking a نظام
**implicitly covers all its مواد** — re-charging when the user clicks into a مادة they just
read in the continuous view is exactly the "trick" feeling §5.1 forbids. Weighting alone
only buys ~1.4× against a cloner; the real bounds are the per-user rate limit on
`/library/full/regulation/*` (10/min) and the detection signal (>50 regulation-level
unlocks/day is not a lawyer).

**Conversion surface:** a downgraded user hitting a frozen item is the strongest upgrade
prompt in the product — «لديك {n} مصدراً محفوظاً في مكتبتك».

## 1.3 Exposure classes

| Class | Elements |
|---|---|
| **Never gated** | Compliance/services (all) · calculators · summaries & lead paragraphs · TOCs · metadata cards · breadcrumbs · topic chips · blog · form intro + when-to-use · citation lists (the mesh) · ~~official source URLs~~ |
| ↳ correction | **Official source URLs moved to "Always gated" 2026-07-28** (DECISIONS §D-OS) — they carry per-item source-system identifiers, so corpus-wide publication is a crosswalk. Compliance keeps its sources, being never gated at all. |
| **Partially gated** | Regulation article bodies · judgment body text · circular bodies (>800 chars) · form template bodies |
| **Always gated** | شرح (AI explanation, teaser only) · full اسأل ريحان answers · form downloads · PDF documents |

## 1.4 Partial-exposure formula

```
visible_chars = clamp(0.20 × len(text), 600, 5000)
```

Applies to judgment sections, circular bodies, article bodies, form bodies.

**Invariant that overrides the formula:** at least one section must always remain behind
the gate. Without it a short single-section document renders 100% open while still
reporting `gate='gated'` — a bug this codebase already shipped once
(`JUDGMENT_FREE_LEADING_SECTIONS`, `test_single_section_document_is_still_gated`).

## 1.5 Regulations — the 20%-of-articles rule

A regulation exposes **20% of its مواد, in full** (50 articles → 10 open). Not 20% of each
article's text.

Deterministic selection: the first `ceil(0.20 × n)` articles by `article_number`. Early
articles are definitions and scope — the most-searched, and the most useful ungated.
Per-item `seo_item_meta.gate_override` still wins over the computed default, so any
article can be pinned open or closed by hand.

## 1.6 Open-tier regulations (300–500)

Data checked live: **36,133 judgment citations collapse to only 98 distinct regulations**
(10 with ≥100 citations). Citation rank alone cannot reach 300–500.

Candidate pool is `in_force` (2,188) + `in_force_amended` (176) = **2,364**.
`consultation_ended` (974 draft laws) and `cancelled` (28) are excluded by hard rule — a
draft or repealed law must never be a flagship open page.

Selection = rank 1 the 98 citation-ranked, then fill by substantive-statute signals
(article count, issuing entity, نظام vs لائحة/قرار). Re-tier from GSC query data later.

---

# PART 2 — ARCHITECTURE

Three layers that must stay separate. Conflating them is what poisons caches.

### Layer A — Classification (tier-free, cacheable, ISR-safe)
`resolve_gate(content_type, content_id) -> 'open' | 'gated'`
Answers *"is this item gated?"* — a property of the **item**, never of the viewer.
**Unchanged by this plan.** Keeps `_gate_defaults_cache` and `_published_ids_cache` safe:
neither ever gains a per-user dimension, so neither can be poisoned.

### Layer B — Entitlement (per-user, never shared-cached)
`resolve_access(user_id, content_type, content_id) -> AccessDecision`
Answers *"may THIS user unlock THIS gated item right now?"* Runs **only** on authed
endpoints that ship `Cache-Control: private, no-store`. Never touches a server component
that feeds the ISR cache.

### Layer C — Metering (the ledger)
`library_unlocks` — period-scoped, idempotent, append-only.

**Why this split matters:** the anon/ISR render path only ever calls Layer A, so
tier-varying bytes are structurally incapable of reaching the shared cache. This is the
correctness property the whole design rests on.

The existing `FullContentGate` pattern already implements exactly this shape (ISR anon
HTML + client-side authed fetch that swaps in place, `frontend/components/library/FullContentGate.tsx`).
We extend it rather than fight it.

---

# PART 3 — DATA MODEL

## Migration 102 — plan quota columns

```sql
ALTER TABLE public.plans
  ADD COLUMN IF NOT EXISTS library_unlocks_period INTEGER;  -- NULL = unlimited

ALTER TABLE public.user_subscriptions
  ADD COLUMN IF NOT EXISTS library_unlocks_override INTEGER;

UPDATE public.plans SET library_unlocks_period = 10   WHERE plan_id = 'free';
UPDATE public.plans SET library_unlocks_period = 100  WHERE plan_id IN ('basic','marketing_lawyer');
UPDATE public.plans SET library_unlocks_period = 200  WHERE plan_id = 'pro';
UPDATE public.plans SET library_unlocks_period = 1000 WHERE plan_id = 'max';
-- 'dev' left NULL = unlimited
```

Follows the established convention exactly: limits on `plans`, per-user escape hatch as a
`*_override` column on `user_subscriptions` (mirrors `points_*_override`,
`ocr_pages_monthly_override`).

## Migration 103 — the unlock ledger

```sql
CREATE TABLE IF NOT EXISTS public.library_unlocks (
    unlock_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    content_type text NOT NULL,          -- regulation|article|judgment|circular|form
    content_id   text NOT NULL,          -- matches seo_item_meta.content_id
    period_key   text NOT NULL,          -- the period CHARGED, see §3.1
    surface      text NOT NULL DEFAULT 'library',  -- 'library' | 'reference'
    unlocked_at  timestamptz NOT NULL DEFAULT now(),
    -- One row per user per item, FOREVER. Unlocks are permanent (§1.2); the
    -- period_key records which period paid for it, and is what the quota counts.
    UNIQUE (user_id, content_type, content_id)
);

CREATE INDEX IF NOT EXISTS idx_library_unlocks_user_period
    ON public.library_unlocks (user_id, period_key);
CREATE INDEX IF NOT EXISTS idx_library_unlocks_item
    ON public.library_unlocks (content_type, content_id);

ALTER TABLE public.library_unlocks ENABLE ROW LEVEL SECURITY;
-- No policies: service-role only, matching the llm_calls ledger convention (058).
```

`surface` exists purely for analytics — it must never affect the charge, or the reference
panel becomes a bypass again.

### 3.1 `period_key` derivation

| Plan | Period | `period_key` |
|---|---|---|
| Has `duration_days` + `started_at` (basic/pro/max/marketing) | Subscription window | `'{plan_id}:{started_at:%Y%m%d}:{period_index}'` where `period_index = floor((now - started_at) / duration_days)` |
| `free` (no duration) | Calendar month, UTC | `'free:{YYYYMM}'` |
| `dev` / NULL limit | — | unlimited; ledger still written for analytics |

Storing the key on the row means quota counting is a plain equality filter — no window
arithmetic at read time, and no drift between the check and the count.

## Migration 104 — extend `get_user_quota_state`

Add to the RPC's return row (migration 093 is the current definition):
- `library_unlocks_limit` — effective limit after expired→free fallback and override
- `library_unlocks_used` — `count(*) FROM library_unlocks WHERE user_id = p_user_id AND period_key = <derived>`
- `library_period_key` — so the backend doesn't re-derive it

Reuses the existing expired→free fallback (`093:87-90`) for free.

---

# PART 4 — BACKEND

## 4.1 New: optional auth dependency (`backend/app/deps.py`)

**Does not exist today** — every public endpoint is anon purely by *omitting*
`Depends(get_current_user)`. Tier-aware public endpoints need a dependency that returns a
user when a token is present and `None` when it isn't, without ever raising 401.

```python
async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[AuthUser]:
    if credentials is None:
        return None
    try:
        return await get_current_user(request, credentials)
    except LunaHTTPException:
        return None          # expired/invalid token → treat as anon, never 401
```

## 4.2 New: `shared/quota` extension

Add a `library` meter alongside the existing `points` / `ocr` meters, reusing
`QuotaExceeded` (`shared/quota/__init__.py:82-103`) so the Arabic message, the
`resets_at` field, the SSE payload shape and `UsageLimitsDialog` all work unchanged.

```python
async def check_library_unlock(user_id, content_type, content_id) -> LibraryUnlockDecision
```
Returns `already_unlocked` (free, no charge) · `granted` (row inserted, quota consumed) ·
raises `QuotaExceeded("library", period, used, limit, resets_at)`.

Arabic strings: `"تم استهلاك رصيد فتح المصادر لهذه الفترة."` plus a gate-card variant
framed as a plan feature, never a scolding.

## 4.3 New: `library_service.resolve_access()` (Layer B)

```python
def resolve_access(supabase, user_id: Optional[str], content_type, content_id) -> AccessDecision
```
- `user_id is None` → `may_unlock=False, reason='anonymous'`
- item gate is `'open'` → `may_unlock=True, charged=False`
- row exists **and** (plan is paid **or** `row.period_key == current`) → `may_unlock=True,
  charged=False` (§1.2 predicate)
- row exists but plan is free **and** `row.period_key != current` →
  `may_unlock=False, reason='frozen_library'` + the stored-library count for the
  upgrade CTA
- no row, quota available → insert, `may_unlock=True, charged=True`
- no row, quota exhausted → `may_unlock=False, reason='quota_exhausted'` + `resets_at`

Never called from a cacheable path. Never memoized.

## 4.4 Modified: `/library/full/{content_type}/{key:path}`

`public_library.py:1061` gains the entitlement check ahead of the content fetch. On
refusal it returns **402-shaped JSON** (Arabic, with `resets_at` and the plan CTA), not
the content. The comment at `:1049` declaring library reads unmetered is deleted — it is
now the opposite of the policy.

## 4.5 Modified: hub depth cap

`hub_page_allowed(page, is_authed)` → `hub_page_allowed(page, tier)`:

| Tier | Max page |
|---|---|
| anon | 1 |
| free | 3 |
| basic/pro/max/dev | unbounded |

All five hub endpoints (`658, 749, 814, 885, 957`) adopt
`Depends(get_current_user_optional)` and pass the real tier. **Cache-Control must become
`private, no-store` whenever a user is present**, otherwise a subscriber's deep page
lands in the shared 1-hour cache and leaks to anon. Anonymous requests keep
`public, max-age=3600`.

Note this *tightens* anon from today's effective 3 pages to 1 — accepted, since discovery
is sitemap + mesh.

## 4.6 Modified: rate limiter

`request.scope["route"]` is **not populated inside `BaseHTTPMiddleware`** (routing hasn't
run), so the template genuinely cannot be read there. Two changes:

1. **Path normalization in the middleware** — collapse the dynamic tail of known library
   prefixes before building the key, so `/public/library/regulations/<any-slug>` shares
   one bucket. Cheap, no restructuring.
2. **Route-scoped limiter dependency** for `/library/full/*` and the new reveal endpoint,
   applied after routing where the template is available, with a much lower budget than
   60/min (suggest 20/min).
3. **Fail-closed for the library family** — a Redis outage must not silently remove the
   only enumeration bound. Fall back to a conservative in-process limit.

---

# PART 5 — FRONTEND

- **`FullContentGate`** (`frontend/components/library/FullContentGate.tsx`) — handles the
  new refusal payload: instead of silently leaving the gated render, show the quota card
  («رصيد الفترة انتهى» + reset date + upgrade CTA). Current behaviour returns `null` on
  any non-OK response, which would make an exhausted quota indistinguishable from being
  logged out.
- **Gate cards** — ~~always render `OfficialSources` so the official URL is present on
  gated pages.~~ **REVERSED 2026-07-28 (DECISIONS §D-OS)** — a gated page shows no official
  source; the block appears only after the reveal. Copy still framed as a feature.
- **Hub pagination** — anon sees the CTA wall from page 2 (was 4); free from page 4.
  `noindex` on capped pages as today.
- **`UsageLimitsDialog`** — new "فتح المصادر" bar next to points and OCR, fed by the
  extended `/api/v1/usage`.
## 5.1 Consumption model — implicit, no confirmation (user decision 2026-07-27)

**There is no stored "user decision".** The ledger row IS the decision: opening a gated
item is the consent, and `library_unlocks` is the only record. No confirmation dialog, no
consent table, no pending-decision state. The same rows also power a
«ما فتحته هذه الفترة» list for free.

**The charge sits on the REVEAL, not the page view.** A gated page renders its free layer
normally; one «اعرض النص كاملاً» action consumes an unlock and swaps in the full content
in a single click. In chat, `[n]` and «عرض المصدر» do the same.

Rationale — `FullContentGate` currently auto-fetches the moment `isAuthenticated` flips
true. If the charge sat on page view, a signed-in user skimming ten judgment summaries
would burn ten unlocks without deliberately reading a single full document, which
destroys the free summary layer that does the SEO and engagement work. So
`FullContentGate` must become **reveal-triggered rather than mount-triggered**.

Consequences that fall out of this:
- **Prefetch is safe.** Next `<Link>` prefetch fetches only the RSC payload and never runs
  client effects, so no unlock is charged. Restored background tabs do hydrate, but
  idempotency makes a repeat open free.
- **Re-visits are free by construction** — `ON CONFLICT DO NOTHING` means refreshes,
  back/forward and re-reads never double-charge within the period.
- **Balance must be passively visible** — a counter beside the unlock action plus the
  `UsageLimitsDialog` bar. No prompt, but never a silent meter.

---

# PART 5B — «مكتبتي» (the user's library surface)

Permanent unlocks (§1.2) turn the ledger into a real user asset, so it gets its own page.

**Route:** `/library/mine` (authed). Keeps the public `/library` hub free of collision.

## 5B.1 The four default layers

Rendered with the existing hub card blocks — مكتبتي is a filtered hub, not a new design
system. Default tabs, in order:

| Tab | content_type | Notes |
|---|---|---|
| الأنظمة | `regulation` | مواد (`article`) **nest under their parent نظام**, not a separate tab — a مادة without its statute reads as an orphan |
| الأحكام | `judgment` | |
| الخدمات | `service` | Never gated → populated by saves only (§5B.2) |
| التعاميم | `circular` | Mixed: long ones arrive via unlock, short (≤800 chars) ones via save |

`form` and `calculator` are secondary tabs, shown only when non-empty.

## 5B.2 Implicit + explicit — `library_items`

مكتبتي is populated **both ways** (user decision 2026-07-27):
- **Implicit** — opening an item saves it automatically, gated or not. This is what makes
  the الخدمات tab work: services are never gated and so never produce an unlock row, but
  opening one is enough to shelve it. Same for short (≤800 char) circulars and open-tier
  regulations.
- **Explicit** — a «حفظ» action pins an item the user has not opened, or marks an opened
  one as deliberately kept.

```sql
CREATE TABLE IF NOT EXISTS public.library_items (
    item_row_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    content_type    text NOT NULL,
    content_id      text NOT NULL,
    source          text NOT NULL DEFAULT 'auto',   -- 'auto' (opened) | 'manual' (saved)
    use_count       integer NOT NULL DEFAULT 0,
    first_used_at   timestamptz,
    last_used_at    timestamptz,
    saved_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, content_type, content_id)
);
ALTER TABLE public.library_items ENABLE ROW LEVEL SECURITY;
-- Service-role only, same convention as library_unlocks.
```

Each use = one upsert:
```sql
INSERT INTO library_items (user_id, content_type, content_id, use_count,
                           first_used_at, last_used_at)
VALUES (..., 1, now(), now())
ON CONFLICT (user_id, content_type, content_id) DO UPDATE
SET use_count = library_items.use_count + 1,
    last_used_at = now();
```

**`library_unlocks` stays immutable.** It is a billing record — inserted once via
`ON CONFLICT DO NOTHING`, never updated. Usage counts live on `library_items` so that no
page view ever writes to the cost ledger; otherwise every quota count and cost audit
reading `library_unlocks` would silently be measuring something else. One table is money,
the other is behaviour.

Explicit saving is **free at every tier** — it stores a pointer, never content, so it
grants no access and costs no unlock. Saving a gated item you have not unlocked is
allowed; it shows in مكتبتي locked, which is a useful intent signal.

## 5B.3 `use_count` and ranking

`use_count` + `last_used_at` are what مكتبتي ranks by (default: recency; secondary:
**«الأكثر استخداماً»**), and they can later weight reference-preview ordering — a source a
lawyer returns to repeatedly is the one worth surfacing first.

Vocabulary is deliberate and must not drift: the user-facing concept is **usage**
(«استخدام»), not «فتح». Internal naming matches end-to-end — `use_count`, `last_used_at`,
`sort=most_used` — so there is never a translation layer between the label and the column.

⚠ **ISR TRAP — this is the blog's exact mistake.** The blog's view-count-on-read is why it
runs `force-dynamic` while the library runs ISR. The counter upsert must therefore **never
run in the cached server render**: it rides on the authed client call (the reveal request
for gated items, a small authed beacon for free ones). A server-side write would either
poison the shared cache or, worse, be skipped on every cache hit and undercount silently.

Reading history is sensitive — it is a record of what a lawyer researched. `library_items`
is RLS-on/service-role-only like the other ledgers, is user-visible in مكتبتي, and is
covered by the existing account-deletion cascade (`ON DELETE CASCADE` on `user_id`).

## 5B.4 Freeze behaviour — the conversion surface

When a user is on free with paid-era rows frozen (§1.2), مكتبتي **still lists every
item** with a lock badge. This is safe: titles, metadata, entity, dates and topic chips
are all in the never-gated class (§1.3), so listing leaks nothing.

That listing is what makes the upgrade prompt concrete — «لديك {n} مصدراً محفوظاً في
مكتبتك» is only persuasive if the user can see the shelf. A frozen library rendered as an
empty page is a worse product AND a worse conversion surface.

## 5B.5 Endpoints

- `GET  /api/v1/library/mine?content_type=&sort=&page=` — authed, `private, no-store`,
  hub-shaped envelope so the existing card components drop straight in. Each row carries
  `{ ...card fields, source, use_count, last_used_at, was_unlocked, is_frozen }`.
  `sort` ∈ `recent` (default) | `most_used` | `saved`.
- `POST /api/v1/library/mine/use` — the authed beacon that records a free-item use
  (gated items record theirs inside the reveal call, so one use never counts twice).
- `POST /api/v1/library/mine/save` · `DELETE .../save` — explicit pin/unpin.

---

# PART 6 — REFERENCE UNIFICATION (the largest item)

## 6.1 The problem, precisely

`fetch_item_references` (`references_service.py:80-164`) calls `_attach_source_views`
(`:162`), which embeds **full source text** in the references-list response — full case
bodies, full chunk content, and uncapped circular bodies (168KB outliers). The panel has
everything before the user clicks anything; `[n]` and «عرض المصدر» are client-side state
changes only (`ReferencePanel.tsx:268-278`, `AgentSearchViewer.tsx:68-74`).

**Metering is impossible in this shape.** No server call happens at reveal time.

## 6.2 The restructure

1. **Strip `source_view` from the list payload.** Keep `n`, `title`, `snippet`, `ref_id`,
   `domain`, links, `cross_refs` — the citation list and its mesh stay free, as policy.
2. **New endpoint** `GET /api/v1/workspace/{item_id}/references/{n}/source`
   — `get_current_user` required, `private, no-store`, route-scoped rate limit.
   Resolves `ref_id` → `(content_type, content_id)` → `resolve_access()` → returns the
   `SourceView` or the 402-shaped refusal.
3. **Frontend** fetches on dialog open / `[n]` click, with a loading state and the same
   quota card on refusal.

Side benefit: removes up to 168KB per reference from every panel load — a real
performance win independent of the gating.

## 6.3 `ref_id` → `seo_item_meta` resolver

The mapping is not 1:1 and must be written carefully:

| `ref_id` shape | Domain | Maps to |
|---|---|---|
| `reg:<uuid>` | regulations | uuid is a **`chunks_v2` id**, not a regulation id → join chunk → `regulation_id`. If the chunk owns a single مادة, prefer `content_type='article'`, `content_id='{regulation_id}#{article_no}'`; else `content_type='regulation'` |
| `case:<case_ref>` | cases | `cases.case_ref` → `content_type='judgment'`, `content_id` = the id used by `build_judgment_slugs.py` |
| `circular:<uuid>` | circulars | direct → `content_type='circular'` |
| `compliance:<sha1>` | compliance | services are **never gated** → always free, never charged |

Unresolvable `ref_id` → **fail closed** (refuse the reveal), never fail open.

⚠ A single chat answer can cite more than 10 sources, so a free user can exhaust a period
in one conversation. **Accepted by explicit decision (2026-07-26)** — citation-checking is
the moment of peak willingness to pay. The UI must state the balance clearly so this
never feels like a trick.

---

# PART 7 — EDGE (Cloudflare / DNS)

Detail lives in [`cloudflare_protection.md`](cloudflare_protection.md); this is what the
edge must enforce for *this* design to hold.

- **Sitemaps to verified crawlers only** — reverse-DNS-verified Googlebot/Bingbot may
  fetch `/sitemaps/*`; everyone else gets 403. Not cloaking: page content stays
  byte-identical for all visitors; only the machine-readable index is restricted.
  Test after enabling — a 403 shows as a fetch error in the GSC Sitemaps report.
- **Managed challenge on library document paths** for unverified non-browser agents.
  Never challenge verified search crawlers.
- **Origin lock** — backend accepts traffic only from Cloudflare IPs, so the Railway
  origin can't be hit directly to bypass edge rules.
- **DNS TXT survival** — the GSC Domain-property verification record must be carried
  across the DNS migration, or verification silently breaks. Either verify after the
  migration or migrate the TXT record deliberately.
- **Canary strings** seeded during شرح generation → provable attribution if a corpus leaks.

The free/anon layer cannot be made scrape-proof — it exists to be crawled. The edge makes
it bounded and detectable; the ledger makes the *scarce* layer genuinely metered.

---

# PART 8 — PHASES

## Phase A — Meter foundation (no user-visible change)
Migrations 102/103/104 · `get_current_user_optional` · `shared/quota` library meter ·
`resolve_access()` · ledger writes. Ship with limits set generously high so nothing
blocks while the counters are observed.
**Agents:** @sql-migration → @fastapi-backend → @validate.
**Done:** unlock rows appear with correct `period_key`; re-open inserts nothing; quota
state RPC returns limit + used; no behaviour change for existing users.

## Phase B — Library enforcement
`/library/full` entitlement · hub depth by tier + `no-store` when authed · rate-limiter
fixes · `FullContentGate` quota card · usage dialog bar.
**Agents:** @fastapi-backend → @nextjs-frontend → @security-reviewer → @validate.
**Done:** anon curl on gated = truncated; free account exhausts at 10 and gets the Arabic
card; pro at 200; ISR cache never serves a subscriber's deep hub page to anon (verify by
curling anon immediately after an authed hit).

## Phase B2 — «مكتبتي»
`library_items` migration · `GET /library/mine` + open beacon + save/unsave · four default
tabs reusing hub cards · implicit auto-save on use + explicit pin · `use_count` ranking
(«الأكثر استخداماً») · frozen-list rendering with the stored-count upgrade CTA.
**Agents:** @sql-migration → @fastapi-backend → @nextjs-frontend → @validate.
**Done:** all four tabs populate; opening a service (never gated, never unlocked) shelves
it; مواد nest under their نظام; saving costs no unlock and grants no access; one use
increments `use_count` exactly once (not twice for gated items); NO counter write happens
in an ISR-cached render; a downgraded account still SEES its full shelf with lock badges.

## Phase C — Reference unification
Strip `source_view` from the list payload · new source endpoint · `ref_id` resolver ·
frontend on-demand fetch + loading + quota card.
**Agents:** @fastapi-backend → @nextjs-frontend → @integration-lead → @validate.
**Done:** references list carries no full text (verify payload size drop); reveal charges
exactly once per item per period; unresolvable `ref_id` refuses; compliance never charges.

## Phase D — Publish ramp
Expand open tier to 300–500 · publish wings beyond sample mode · sitemap waves · GSC
Domain property + submit index.
**Agents:** @sql-migration → @validate.
**Done:** GSC accepts the index; indexation rate healthy on wave 1 before wave 2 ships.

## Phase E — Edge
Per `cloudflare_protection.md`, after D.

---

# PART 9 — TRAPS (found during exploration, 2026-07-26)

1. **`resolve_gate` must NOT gain a tier parameter.** `_gate_defaults_cache`
   (`library_service.py:108`, 300s TTL) and `_published_ids_cache` (`:132`) are global and
   time-keyed. A per-user dimension there poisons them across users. Layer B exists
   precisely to keep this from happening.
2. **ISR is a shared cache.** Hub 3600s / doc 86400s, no auth variance
   (`frontend/lib/library/api.ts:21-22`). Any per-user byte rendered server-side leaks to
   the next visitor. Per-user content reaches the browser ONLY via the client-side authed
   fetch.
3. **`hub_page_allowed` is currently dead** — `is_authed=False` hardcoded at all five call
   sites, so nobody gets deep pagination today. Changing it changes behaviour for
   existing users.
4. **`request.scope["route"]` is unavailable in `BaseHTTPMiddleware`.** Template-based
   rate limiting must be a route dependency, not a middleware tweak.
5. **`get_full_*` functions apply no gate at all** (`library_service.py:3551+`) — auth is
   the only boundary. They are where entitlement must be enforced.
6. **Forms carry an independent liability gate** (`review_status='approved' AND
   is_published`) that survives every tier — a Max subscriber still cannot see an
   unapproved form.
7. **`llm_calls.user_id` is NOT NULL**, which is why anon-ask writes no ledger row. The
   same constraint means `library_unlocks` can never record an anonymous unlock — correct,
   since anon = 0.
8. **Circular source views are uncapped** (168KB outliers) — the reference restructure is
   also a performance fix.
9. **Rate limiter fails open on Redis loss** (`rate_limit.py:64-66`) — acceptable for chat,
   not for the library family.
10. **The limiter's client IP is attacker-controlled** (`rate_limit.py:89-90`) — leftmost
   `X-Forwarded-For`. Cloudflare *appends* to a client-supplied XFF rather than replacing it,
   so a fake header mints a fresh bucket per value. Behind the proxy read `CF-Connecting-IP`.
   Same bug at `public_ask.py:112-117` (feeds Turnstile `remoteip`). See
   [`defence_in_depth.md`](defence_in_depth.md) §4.
11. **The authed limiter key comes from an UNVERIFIED JWT** (`rate_limit.py:100-103`,
   `verify_signature: False`) — a forged `sub` mints a fresh bucket. The §4.6 route-scoped
   limiter must key off the verified user, since the edge hands all authed traffic to it.

---

# PART 10 — OPEN ITEMS

| Item | Needed for | Owner |
|---|---|---|
| Confirm `free` period = calendar month (vs rolling 30d from first unlock) | Phase A | user |
| Open-tier list 300–500 — approve the blended ranking output | Phase D | user |
| Gate-card copy in Arabic («feature, not paywall» framing) | Phase B | user |
| GSC Domain-property verification (DNS TXT) | Phase D | user |
| ~~Cloudflare account + plan~~ — **DECIDED 2026-07-27: Pro ($20/mo annual)**, dedicated ops account. Stack in [`defence_in_depth.md`](defence_in_depth.md) | Phase E | ✔ |
| PDPL audit before judgments lose `noindex` | Phase D | user |
