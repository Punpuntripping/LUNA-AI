# Defence in Depth — total protection stack for rayhanai.com

> **EXECUTE FROM [`cloudflare_navigation_hardening.md`](cloudflare_navigation_hardening.md)**
> (2026-07-28) — it carries the agreed, trimmed action list. This document remains the
> reference for *why* each decision was made: threat model, plan limits, corpus numbers,
> the regulation analysis (§10) and the traps.

**Status:** PLANNED 2026-07-27 — nothing built.
**Assumes:** [`access_tiers_gating.md`](access_tiers_gating.md) is FULLY implemented (ledger,
entitlement, مكتبتي, reference unification). This plan is what wraps around it.
**Decision:** Cloudflare **Pro** ($20/mo annual, $25 monthly) — approved by user 2026-07-27.
**Supersedes the edge track of** [`cloudflare_protection.md`](cloudflare_protection.md),
which becomes Layer 1 below.

**Design goal:** make bulk extraction expensive and *attributable*, without a single
visible friction point for a real lawyer or a single lost crawl from Google.

---

# §0 — UX INVARIANTS (the balance contract)

Any rule that violates one of these does not ship. These are not preferences; they are the
acceptance criteria for the whole plan.

1. **Verified search crawlers are never challenged and never rate-limited.** The library
   exists to be crawled. A crawl-rate drop is a P1 rollback, not a tuning exercise.
2. **A signed-in user never sees a challenge.** Ever. Authed abuse is metered by identity
   (the ledger), which is strictly stronger than anything the edge can do.
3. **No IP-based `block` on any human-facing path — `managed_challenge` only.** Saudi mobile
   traffic is behind carrier-grade NAT (STC/Mobily/Zain) and law firms sit behind one office
   NAT. A block on a shared IP takes out a whole carrier; a Managed Challenge costs a real
   browser a sub-second invisible round-trip and then issues a clearance cookie.
4. **No interstitial CAPTCHA on public content pages.** Turnstile belongs on the anon *ask*
   action (already built, see §5.2), not on reading.
5. **No country or ASN blocking.** Saudi lawyers travel and VPN.
6. **Gated ≠ walled.** The free layer and the official source URL always render
   (`access_tiers_gating.md` §1.2/§1.3). Gating reads as a curated feature.
7. **Observe before enforce.** Every threshold ships at 3–5× the measured p99 for 7 days,
   then tightens. Numbers below are starting points, not targets.
8. **GSC crawl stats are the canary.** Any drop after a change ⇒ roll back that change.

---

# §1 — THE ORGANIZING PRINCIPLE

> **The edge handles anonymous traffic. The app handles authenticated traffic.**

This is forced, not chosen. Cloudflare Pro rate limiting counts by **IP only** — cookies,
headers and user identity are Business/Enterprise characteristics. So the edge structurally
*cannot* meter a user; it can only meter an address. Meanwhile the app already knows exactly
who is asking and, once the access-tier ledger is live, exactly what they have taken.

Splitting on that line resolves the CGNAT problem for free: authed users are skipped at the
edge entirely (§3.2 rule 2), so shared-IP collisions can never hit a paying customer.

| Threat | Controlled by | Layer |
|---|---|---|
| Naive scripted scraper (no JS, one IP) | SBFM + rate limits | 1 |
| Headless-browser farm, residential proxies | JS Detections + challenge; then cost, not prevention | 1 |
| AI training crawlers | AI Crawl Control per-bot | 1 |
| List enumeration (sitemap feed) | Verified-bot gate | 1 |
| Direct-to-origin bypass | Origin lock | 0 |
| **Bulk pull with a valid paid account** | **Ledger + anomaly detection** | **3 + 4** |
| Free-account farming | Email verification + anon=0 | 3 |
| LLM cost attack (anon ask) | Turnstile + session cap + daily budget | 3 |
| Corpus already copied | Canaries + terms | 5 |

Note the heaviest threat — a credentialed account methodically pulling — is untouchable by
Cloudflare at any plan tier. That is the case for spending effort on Layer 4, not Layer 1.

---

# §2 — LAYER 0: ORIGIN LOCK (do this first or nothing else is real)

Without this, every rule below is one hostname away from bypass:
`luna-backend-production-35ba.up.railway.app` answers to anyone.

Railway offers no IP allowlist on public domains, so use a shared secret:

1. Cloudflare **Transform Rule** (request header modify) adds `X-Edge-Secret: <random 32B>`
   to every proxied request.
2. Backend + frontend middleware rejects any request missing it with 403.

**Both services must be proxied (orange cloud).** Proxying only the frontend leaves the
entire library reachable as JSON at `/api/v1/public/library/*`, and — worse — hands out
`GET /api/v1/public/library/sitemap/{section}` at **5,000 URLs per page**
(`public_library.py:1-7,568-579`). That endpoint is the single best enumeration surface in
the product; gating the frontend's `/sitemaps/*` while leaving it open is theatre.

⚠ **Traps, all of which will break production if missed:**
- **Railway healthcheck** hits the app directly, not via Cloudflare — exempt
  `/api/v1/health` from the secret check or every deploy fails healthcheck.
- **Server-side ISR fetches.** If Next server components fetch the backend through
  `api.rayhanai.com`, every ISR render arrives from **one Railway egress IP** and will trip
  any rate limit instantly, taking the whole site down. Fix: route server→server calls over
  Railway private networking (`*.railway.internal`) via a separate `INTERNAL_API_URL`, so
  only browser traffic transits Cloudflare. Second-best: a WAF skip rule on the edge secret.
- Rotating the secret is an env-var change ⇒ triggers a master-pull deploy
  ([[feedback_railway_master_pull_trap]]).
- Trust `CF-Connecting-IP` **only** when the edge secret is present.

## 2.1 SSE through the proxy

`messages.py:72-77` already sets `X-Accel-Buffering: no` and `Cache-Control: no-cache`, and
Wave 7A heartbeats keep the stream inside Cloudflare's 100s origin timeout. Two additions:

- Add **`no-transform`** to the SSE `Cache-Control` — it stops Cloudflare compressing or
  otherwise rewriting the stream, which is the usual cause of buffered SSE behind a proxy.
- **Cache Rule: bypass cache for `/api/*`**, with one exception below.

Verify streaming E2E before and after cutover; keep grey-cloud as the rollback.

## 2.2 Free upside: edge-cache the anon library API

`/api/v1/public/library/*` already ships `public, max-age=3600` and is anon-only by design
(Layer A classification, no per-user bytes — `access_tiers_gating.md` §2). A Cache Rule
marking it eligible for cache with origin TTL respected moves the entire SEO read load off
Railway. This is a cost win that pays a chunk of the Pro subscription.

---

# §3 — LAYER 1: CLOUDFLARE PRO

Verified plan limits (2026-07-27): **20 WAF custom rules · 2 rate limiting rules, IP-only
counting, ≤60s period, ≤1h mitigation · Super Bot Fight Mode · challenge actions have no
selectable duration on Pro.**

## 3.1 Super Bot Fight Mode

| Setting | Value | Why |
|---|---|---|
| Verified bots | **Allow** | Invariant 1 |
| Definitely automated | **Managed Challenge** (not Block) | Block catches link-preview fetchers and your own tooling; challenge costs a real browser nothing |
| JS Detections | **On** | Invisible; catches headless. See CSP trap below |
| Static resource protection | **Off** | Would challenge `/_next/*` chunks |

⚠ **CSP trap.** JS Detections inject a script from `/cdn-cgi/challenge-platform/`. The
existing CSP will block it silently ([[project_domain_rayhanai]] — CSP precedes rebuild).
Add `/cdn-cgi/` to `script-src` **before** enabling, then verify the console is clean.

⚠ **Your own agents will be classified as automated.** `@validate`, `@smoke-tester` and
`@deploy-checker` drive Playwright against production. Ship rule 3 below in the same change,
or post-deploy verification starts failing for reasons that look like app bugs.

## 3.2 WAF custom rules (6 of 20 used)

| # | Match | Action |
|---|---|---|
| 0 | `http.user_agent` matches the SEO/archive inventory list (§3.4) | **Block** — MUST be rule 0, see trap below |
| 1 | `cf.client.bot` | **Skip** — rate limiting + SBFM. Belt-and-braces over SBFM's allow |
| 2 | `http.cookie contains "sb-"` **or** `http.request.headers["authorization"]` present | **Skip** — rate limiting + SBFM. This is invariant 2, and the CGNAT release valve |
| 3 | `http.request.headers["x-luna-test"]` == secret | **Skip** — all. Your Playwright/agent traffic |
| 4 | (`http.request.uri.path` matches `/sitemap*.xml` or starts with `/sitemaps/` or starts with `/api/v1/public/library/sitemap`) **and not** (`cf.client.bot` **and** `http.user_agent` contains one of Googlebot/bingbot/DuckDuckBot/YandexBot) | **Managed Challenge** — verified-bot check stops UA spoofing, UA check narrows to *search* engines |
| 5 | `http.request.uri.path` starts with `/internal/` | **Block** (allow only via edge secret / your IP). `rate_limit.py:58` exempts this family from the global limiter — it should not be publicly reachable at all |
| 6 | AI-trainer user agents not covered by AI Crawl Control | **Block** |

Skip **can** target rate limiting rules and Super Bot Fight Mode, which is what makes rules
1–3 work. (It cannot skip the free-plan Bot Fight Mode — which is another reason to leave
that product off.)

⚠ Rule 2 means anyone who signs up bypasses every edge control. That is intentional: they
are then metered per-user by the ledger and the in-app limiter (§4), which is a tighter
bound than an IP bucket. It only holds if §4 is done.

## 3.3 Rate limiting — the 2-rule budget

Pro expressions may use Host, URI, Path, Full URI, Query, Verified Bot. **Not** method, not
headers, not cookies. Both rules are effectively anon-only thanks to skip rules 1–3.

| # | Scope | Start at | Action | Notes |
|---|---|---|---|---|
| **RL1** | `/api/v1/public/*` (the machine-readable surface) | 120 req/min/IP → tighten toward 60 | Managed Challenge | No verified crawler needs the JSON API — Googlebot reads the HTML. Depends entirely on the ISR-egress fix in §2 |
| **RL2** | Frontend document paths: `/regulations/*`, `/judgments/*`, `/circulars/*`, `/compliance/*`, `/forms/*` | 60 req/min/IP | Managed Challenge | A human never opens 60 documents in a minute; a Managed Challenge that a real browser passes issues a clearance cookie for the session |

`/api/v1/public/ask` needs no rate-limit slot — it already carries a session cap, a global
daily budget, a kill switch and Turnstile (§5.2). Use exact-path matching if you ever do
scope a rule there, so the `GET /public/ask/{question_id}` teaser poll isn't counted
(`public_ask.py:129,210`).

## 3.4 ⚠ `cf.client.bot` INCLUDES the SEO inventory crawlers — rule order is load-bearing

Cloudflare's **Verified Bots** list is not "search engines". It spans search, SEO, AI,
monitoring and advertising — **AhrefsBot, SemrushBot and Moz's DotBot are all verified
bots**, and `cf.client.bot` is a broad pass over every category. Filtering by
`verified_bot_category` requires Enterprise Bot Management.

So on Pro, rules 1 and 4 as originally written would have (a) exempted Ahrefs/Semrush from
every rate limit and SBFM, and (b) **served them the sitemap — a complete title dump in one
request.** Precisely the companies whose URL inventories we are trying to stay out of.

Two fixes, both required:
1. **Rule 0 blocks them by user agent and must sit ABOVE the skip rules.** WAF custom rules
   evaluate in order and a Block terminates evaluation.
2. **The sitemap gate narrows `cf.client.bot` with a search-engine UA check** (rule 4). The
   verified-bot condition defeats UA spoofing; the UA condition defeats verified-but-unwanted.

**Inventory crawler list for rule 0 + robots.txt:** `AhrefsBot` · `SemrushBot` ·
`SiteAuditBot` · `DotBot` · `rogerbot` · `MJ12bot` · `BLEXBot` · `DataForSeoBot` ·
`Barkrowler` · `CCBot`. All of these publicly honour robots.txt, so robots.txt is the
primary control and rule 0 is enforcement against spoofers.

**Limits, stated honestly:** these tools also discover URLs from *backlinks on other sites*,
so blocking the crawler stops them fetching your pages but cannot keep a linked URL out of
their index. And nothing removes what is already archived — Common Crawl's existing captures
persist. This reduces future accumulation; it does not un-publish.

## 3.5 AI-bot policy

Use **AI Crawl Control per-bot dials**, not the blunt one-click block:
- **Block** bulk trainers (CCBot, Bytespider, …).
- **Allow** AI *search* crawlers (OAI-SearchBot, PerplexityBot). Being cited in an AI answer
  is a discovery channel aimed at exactly this audience; blocking them is a growth
  self-injury dressed as security.

---

# §4 — LAYER 2: APP-SIDE (`rate_limit.py` is currently bypassable)

Four fixes, all in `backend/app/middleware/rate_limit.py`. The first two are new findings —
they are not in `access_tiers_gating.md` §4.6.

1. ⚠ **Leftmost `X-Forwarded-For` is attacker-controlled** (`:89-90`). Cloudflare *appends*
   the real IP to a client-supplied XFF rather than replacing it, so `X-Forwarded-For:
   1.2.3.4` mints a fresh bucket per fake value. Behind the proxy, read **`CF-Connecting-IP`**
   (safe to trust because of the origin lock). Same bug in `public_ask.py:112-117`, where it
   feeds Turnstile's `remoteip`.
2. ⚠ **The authed rate-limit key comes from an unverified JWT** (`:100-103`, decoded with
   `verify_signature: False`). A forged `sub` mints a fresh bucket. Since §3.2 rule 2 hands
   all authed traffic to this limiter, it must key off the **verified** user — derive it in a
   route dependency after auth, which is where §4.6's route-scoped limiter already lives.
3. **Per-path buckets defeat breadth-first crawling** (`:107`) — normalize the dynamic tail
   of library prefixes into one bucket. *(Already in the access plan.)*
4. **Fail-closed for the library family** (`:64-66`, `:149-152`) — a Redis blip must not
   remove the only bound. *(Already in the access plan.)*

---

# §5 — LAYER 3: ENTITLEMENT (built by the access plan) + 2 additions

## 5.1 Require a verified email before an unlock counts
Anon = 0 unlocks means extraction *requires* accounts. Free = 10 unlocks/month means bulk
extraction requires ~1,600 accounts per 1% of the corpus. Gating unlocks behind email
confirmation makes each of those cost a real mailbox — the highest-leverage anti-farming
control in the stack, at zero UX cost since real users confirm anyway.

## 5.2 Turn Turnstile ON for anon ask
`public_ask.py:27-28,181-182` — Turnstile is **already implemented** and skipped entirely
whenever `TURNSTILE_SECRET_KEY` is unset. Setting that env var (Turnstile is free and
unmetered on any Cloudflare plan) closes the only anon endpoint that spends LLM money with
no ledger row ([[project_llm_calls_ledger]] — `llm_calls.user_id` is NOT NULL, so anon asks
are invisible to cost tracking). This is a one-variable change, not a build.

---

# §6 — LAYER 4: DETECTION (where the real defence lives)

Once `library_unlocks` and `library_items` exist you have per-user extraction telemetry that
no WAF can produce. This is the layer that catches the threat Cloudflare cannot.

**Signals** (weekly script in `scripts/`, same shape as `cost_for_day.py`):

| Signal | Query shape | Reads as |
|---|---|---|
| Burst | unlocks/hour > 40 for one user | harvesting |
| Enumeration | consecutive unlocks with monotonic `article_number` / adjacent `content_id` | scripted walk |
| No-recall breadth | many items, `open_count` all = 1, wide topic spread | copying, not researching — a real lawyer re-opens a few |
| Orphan unlocks | unlocks with no `messages` activity in the same window | not reading in context |
| Metronome | low variance in `unlocked_at` deltas | cron, not human |
| Farming | ≥N accounts from one IP/ASN in 24h, each burning ≥8/10 free unlocks | account farm |

**Response ladder — never fully automatic:**
1. Flag → weekly manual review.
2. Soft: step-up Turnstile on the reveal action for that account.
3. Throttle: per-hour unlock cap on that account.
4. Freeze unlocks, Arabic notice + support contact.
5. Revoke plan.

Levels 4–5 require a human. A false positive on a paying lawyer costs far more than a slow
scraper.

---

# §7 — LAYER 5: ATTRIBUTION

- **Canary phrasings** seeded during شرح/summary generation. These are Rayhan's copyrighted
  text; the raw statute is public domain. Canaries are what make a takedown provable.
- **Per-user watermark on the reveal payload only.** The reveal is a per-user server call
  (`access_tiers_gating.md` §6.2) and never touches ISR, so a per-user marker is safe there —
  it cannot poison a shared cache. Gives traitor-tracing, not just theft detection.
- ⚠ **Never mutate statute or judgment text.** Watermarks and canaries live in the
  AI-generated layer *only*. A legal product that alters the law it quotes has a correctness
  problem far worse than being scraped.
- **Terms**: explicit no-scraping / no-automation clause
  ([[project_legal_docs_terms_privacy]]).

---

# §8 — DELIBERATELY NOT DOING (and why)

| Not doing | Why |
|---|---|
| Bot Fight Mode (free product) | Zone-wide, unconfigurable, un-skippable; superseded by SBFM |
| Block action on document paths | CGNAT — takes out a carrier |
| Turnstile interstitial on content pages | Kills the SEO/engagement layer the whole plan is built on |
| Country/ASN blocking | Users travel and VPN |
| Any challenge on authed API paths | Breaks SSE and the app; identity metering is better |
| Business plan (~$200-250/mo) | Buys 5 RL rules, 10-min windows, cookie characteristics. Revisit only if Pro's logs show real distributed scraping |
| Cloudflare Registrar during cutover | Concentrates registrar + DNS in one account mid-migration |

---

# §9 — PHASES

| Phase | Scope | Done when |
|---|---|---|
| **E1** | Zone + nameservers, SSL Full (strict), origin lock both services, ISR→internal-networking fix, `no-transform` on SSE | Direct origin URL refused; SSE E2E passes; GSC URL-inspection renders |
| **E2** | SBFM + CSP fix for JS Detections + custom rules 1–6 + AI Crawl Control | Anon curl of the sitemap feed challenged; GSC still reads sitemaps; Playwright agents unaffected |
| **E3** | RL1 + RL2 at observe-level thresholds, 7 days, then tighten | Scripted 100-page crawl from one IP gets challenged; GSC crawl stats flat |
| **E4** | §4 limiter fixes · §5.1 email gate · §5.2 Turnstile env var | Forged XFF and forged `sub` no longer mint buckets; anon ask requires Turnstile |
| **E5** | Detection script + weekly review + canaries + terms clause | First weekly report runs clean; canary present in generated شرح |

E4 is independent of Cloudflare and can ship before E1 — §5.2 in particular is one env var.

## Verification checklist (after each phase)

- [ ] GSC URL-inspection live test renders normally — **critical**
- [ ] GSC Sitemaps report reads all sections (403 shows as a fetch error there)
- [ ] Anonymous `curl` of `/api/v1/public/library/sitemap/regulations` → challenged
- [ ] Direct Railway origin URL → 403
- [ ] Login + SSE chat streaming E2E through the proxy
- [ ] Signed-in user: zero challenges across a full session
- [ ] `@smoke-tester` / Playwright agents still pass against prod
- [ ] GSC crawl stats stable for 7 days — any drop ⇒ roll back the last rule

---

# §10 — THE REGULATION FAMILY (priority surface, user 2026-07-27)

Everything above applies zone-wide. This section is what to build **first**, because the
أنظمة corpus is the crown jewel and its endpoints have an exploitable asymmetry.

## 10.1 Live numbers (Supabase, 2026-07-27)

| Fact | Value |
|---|---|
| Published regulations (`seo_item_meta`) | **3,373** |
| مواد (`seo_articles`) | **50,923** across 1,794 regulations |
| Articles per نظام | median 18 · avg 28 · p90 53 · **max 716** · 605 regs >25 · 46 regs >100 |
| Regulations with NO articles (chunk-only) | 1,579 |
| Published مادة pages (`seo_item_meta` content_type='article') | **5** — مادة pages are opt-in and effectively unpublished |
| Cached شرح rows (`seo_sharh`) | **229** of 50,923 |

## 10.2 The surface

| # | Endpoint | Auth | Cache | One request yields |
|---|---|---|---|---|
| R1 | `/public/library/regulations` | anon | 1h public | 9 cards, capped at page 3 (`ANON_HUB_MAX_PAGE`) |
| R2 | `/public/library/regulations/{slug}` | anon | 1h public | metadata + **TOC — the article map** |
| R3 | `/public/library/regulations/{slug}/articles/{article_slug}` | anon | 1h public | 500 free chars (`ARTICLE_FREE_CHARS`) + 170-char شرح teaser |
| R4 | `/public/library/sitemap/{regulations,articles}` | anon | 1h public | 5,000 URLs |
| **R5** | **`/library/full/regulation/{slug}`** | **any authed** | private | **EVERY مادة of the نظام, untruncated** (`library_service.py:3551-3563`) |
| R6 | `/library/full/article/{reg}/{art}` | any authed | private | one full مادة **+ the full شرح** (`:3620-3635`) |

## 10.3 ⚠ The granularity arbitrage — corrects `access_tiers_gating.md` §1.2

R5 and R6 both cost **one unlock** under the plan as written. But R5 returns a whole نظام
(median 18 مواد, up to 716) and R6 returns one مادة. A rational extractor never touches R6.

| Path | Unlocks | Periods on Max (1,000/30d) | ≈ SAR |
|---|---|---|---|
| Plan's stated estimate (§1.2) | — | ~85 months | ~16,000 |
| **Raw statute via R5, per-نظام** | **3,373** | **~3.4 months** | **~640** |
| With weighted cost `clamp(ceil(n/25),1,8)` | ~4,800 | ~4.8 months | ~910 |
| **شرح via R6, per-مادة (unavoidable)** | **50,923** | **~51 months** | **~9,600** |

So the real bound on the raw corpus is **~640 SAR, not 16,000** — a 25× error in the plan's
own risk estimate, and the correction is entirely about *which content_type gets charged*.

**The redeeming detail, which is currently an accident:** `get_full_regulation` returns
`{id, title, text}` sections only — **no `sharh_md`**. شرح is reachable *only* one-مادة-
at-a-time through R6. That is what keeps the actual moat at ~9,600 SAR while the
public-domain statute text sits at ~640.

## 10.4 Controls, in priority order

**1 — Make the شرح exclusion an INVARIANT, not an accident.** ⭐ highest value, zero UX cost.
`get_full_regulation` must never return `sharh_md`, with a regression test asserting it.
A future "continuous reading with شرح" feature would silently collapse the moat by ~15×;
if that feature is ever wanted, it charges per-مادة. Raw statute is public domain and
obtainable from the official portals anyway — **the شرح, the article-level structure and
the search are the only things that are actually Rayhan's.** Defend those, not the law.

**2 — Per-user rate limit on the bulk endpoint** (route-scoped, §4). `/library/full/regulation/*`
at **10/min** is invisible to a human (nobody reads ten complete statutes a minute) and puts
a hard floor of ~5.6 hours of sustained traffic on a full-corpus pull — long enough for
detection to fire. `/library/full/article/*` can stay looser (30/min).

**3 — Weight the unlock by size.** `cost(regulation) = clamp(ceil(n_articles/25), 1, 8)`;
`cost(article) = 1`. The median نظام (18 مواد) still costs 1, so the common case is
unchanged; the 716-مادة monster costs 8. This buys only ~1.4× against a cloner — its real
job is making the meter *honest*, so one unlock doesn't mean both "a paragraph" and "a
716-article statute". For the 1,579 chunk-only regulations, weight by total body length.
**Unlocking a نظام must implicitly cover its مواد** — charging again when the user clicks
into a مادة they just read in the continuous view is exactly the "trick" feeling §5.1
forbids.

**4 — Detection signal specific to this shape** (§6). Regulation-granularity unlocks are
self-labelling: nobody legitimately opens 50 complete statutes a day. Alert at
**>50 regulation-level unlocks/day** or >200/period for one user — that catches a
full-corpus pull on day one of four months.

**5 — When مادة pages publish, the exposure profile flips.** Today only 5 are published, so
the anon article surface is ~zero. Publishing all 50,923 makes R3/R4 the dominant anon
surface overnight and makes the `articles` sitemap section the single best enumeration
target in the product. Ship it in waves (`access_tiers_gating.md` Phase D) and turn on the
§3.2 rule-4 sitemap gate **before** the first wave, not after.

## 10.5 Not worth doing for regulations

- Gating R2's TOC. It is the enumeration map, but it is also the SEO mesh and is
  never-gated by policy (§1.3). Walking 3,373 reg pages reconstructs every مادة URL, so the
  sitemap gate buys *speed* (5,000/request → 1/request), not secrecy.
- Tightening `ARTICLE_FREE_CHARS` (500). It is public-domain text and the free preview is
  what earns the ranking.
- Any attempt to price-out a determined cloner of the raw statute. 3,373 documents is simply
  not a large number; the honest goal is to make it slow, attributable, and pointless
  relative to the شرح layer they still can't get cheaply.

---

# §11 — COST

**Cloudflare Pro, $20/mo annual ($240/yr).** Nothing else here is a recurring cost —
Turnstile is free and unmetered, and edge-caching the anon library API (§2.2) is a net
*saving* on Railway egress and compute.
