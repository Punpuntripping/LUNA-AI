# Cloudflare + Navigation Hardening — the execution plan

**Status:** IN PROGRESS. **The entire Cloudflare-side config is now BUILT (2026-07-28) and 100%
inert** — 3.1, 3.12, SSL `strict` (3.3a), 4 WAF rules (3.9), 2 cache rules (3.10) and the
`X-Edge-Secret` Transform Rule (3.4a) are all live in the zone, but **every record is still
grey-clouded**, so no request touches any of it. Nothing can break until the orange cloud flips.
What remains is **all code**: Part 1, Part 2, 3.2, 3.2b, 3.5, 3.6, 3.7, and 3.4's middleware.
Full detail in the cutover log at the end of Part 3.
**Cost:** $0 — **start on Cloudflare Free** (decision 2026-07-28); upgrade to Pro ($20/mo
annual) at the triggers in Part 5. Steps marked **[PRO]** are staged but not executed yet.
**Baseline:** access-tier Phases A/B/B2/C are BUILT (migrations 103–107 applied, code
uncommitted). Entitlement and metering are done — this plan is only the edge + the traversal
residue.

Rationale for every decision lives in [`defence_in_depth.md`](defence_in_depth.md) and
[`navigation_enumeration_defence.md`](navigation_enumeration_defence.md). **This document is
the one to execute from**; those two are the reference behind it.

## The contract every step must satisfy

1. Verified search crawlers are never challenged, never rate-limited, never counted.
2. Signed-in users never see a challenge.
3. No IP `block` on human-facing paths — `managed_challenge` only (Saudi CGNAT, firm NAT).
4. Every threshold ships 3–5× loose, observed 7 days, then tightened. GSC crawl stats are the
   rollback trigger.

---

# PART 1 — SHIP FIRST (no Cloudflare dependency)

Independent of everything below. All small.

| # | Action | Where |
|---|---|---|
| 1.1 | ✅ **BUILT 2026-07-28** — the plan's "just set the env var" was **wrong**: the backend was coded, the frontend hard-coded `turnstile_token: null` (`ask.ts:91`), so setting the secret would have 403'd **every** anonymous ask. Widget now built + a real CF widget exists (sitekey `0x4AAAAAAEAMNZfHSmuNL4Vm`, managed, `no_clearance`, domains incl. the Railway host and `localhost`). ⚠ **Set the secret only AFTER the frontend is deployed and verified sending a token** | env + frontend |
| 1.1b | ☐ **DECISION NEEDED before setting the secret** — validated live: `managed` mode did **not** stay invisible on an automated browser; it painted a «التحقق من أنك إنسان» checkbox. The submit button is deliberately never gated and `resolveTurnstileToken` waits at most 4 s, so a visitor who clicks إرسال before solving sends `null` → 403 «فشل التحقق الأمني، حاول مجدداً». Recovery works (the gate remounts for a fresh token) but that cohort's **first attempt fails by construction**. Invisible while the secret is unset. Options: accept it · lengthen the grace · gate the button only while a challenge is actually displayed | — |
| 1.2 | robots.txt: `Disallow: /` for AhrefsBot · SemrushBot · SiteAuditBot · DotBot · rogerbot · MJ12bot · BLEXBot · DataForSeoBot · Barkrowler · CCBot | `robots.txt` |
| 1.3 | `X-Robots-Tag: noindex` on sitemap responses — the XML itself should not be indexable | `app/sitemaps/[section]/route.ts`, `app/sitemap.xml/route.ts` |
| 1.4 | Add `no-transform` to the SSE `Cache-Control` | `messages.py:73-77` |
| 1.5 | ⚠ **RE-SCOPED 2026-07-28 — do NOT take `/internal/*` off the public internet.** Supabase DB triggers and the marketing/editorial caller both reach it from outside; both are shared-secret authed. Correct scope: verify both secrets are set and non-trivial in prod (`INTERNAL_WEBHOOK_SECRET`, `EDITORIAL_SERVICE_KEY`), and confirm fail-closed when unset. See the rule 3 correction in Part 3 | backend |

**Sitemap paths stay standard** (decision 2026-07-28 — randomization dropped). Keep the
`Sitemap:` line in robots.txt. The sitemap is protected by WAF rule 4 (verified search
crawlers only) plus step 3.2b, which is the stronger control anyway.

---

# PART 2 — NAVIGATION (3 items, that's all)

Anon pagination is already closed — hub depth is capped at page 1 server-side and anon
browsing is served from the ISR cache. Only these remain:

| # | Action | Notes |
|---|---|---|
| 2.1 | ✅ **DONE 2026-07-28** — validation at the top of all five hub handlers, before tier resolution and before any query. ≥3 chars on every free-text (`ilike`) filter — `q`, plus `provider` and non-UUID `entity`, which are search boxes in disguise. Closed vocabularies imported, never retyped. 141 tests | Vocabularies verified against prod, not assumed: `doc_type` = 21 values (all 3,373 `regulations_v2` rows scanned, zero nulls, zero strays) · `court_level` = 3 · forms `category` = 4. **`entity` has no static list anywhere**, so the *token space* is enforced instead (UUID or `^[0-9]{1,8}$`; live space is 132 values, all numeric, ≤6 digits) — sound because the match is exact, so a wrong-shaped value can only mint a cache key, never return a row. ⚠ **`total_pages` was itself the oracle** — page 2 with any `q` returned an exact match count without ever serving an item, so capping via `min(true, ceiling)` would have leaked it. Anon now gets a flat `2` and **the count query is not issued at all** |
| 2.2 | ✅ **DONE 2026-07-28** — `services/library_budget_service.py`. Unit of account is distinct **`section:slug`** (no public hub card carries a corpus uuid; the section prefix stops two wings sharing a slug from discounting reach). One Redis ZSET per user, score = epoch of *first* sight, `ZADD … NX` so a re-seen id can neither charge twice nor extend its own residency. 34 tests | Enforced **before** the query (a refusal costs no DB round-trip), charged **after** (nobody pays for items they weren't served). ⚠ **Anon is structurally unmetered** — `enforce`/`charge` no-op when `user_id` is None, pinned by `test_anonymous_browsing_is_never_metered`, because an IP key here would meter the ISR renderer and take the public library down. ⚠ Reuses `rate_limit.py`'s 429 **verbatim** rather than the D14 quota-card body: D14's fields describe the *unlock ledger*, so filling them from a reach meter would make the card lie. Fail-closed via a bounded process-local window that can only ever count *less* than Redis, so an outage cannot manufacture a 429 |
| 2.3 | ✅ **DONE 2026-07-28** — inline detector (one count query per user per window, `SET NX EX` guarded, exceptions swallowed) + offline sweep `python -m backend.app.services.library_budget_service` | ⚠ **`library_items` can only supply half the signal** — it records document *uses*, never hub impressions. The "yielded" half is 2.2's ZSET, which is why the detector lives in that module. Stops at a WARNING log, not an alert rule: Logfire already ingests backend logs and this project has **no alerting infrastructure to hook** ([[project_agent_tracking_protocol]] is planned, not built) |

⚠ **Per-user only.** Anon library traffic reaches the backend through the Next ISR renderer
on one shared IP, so the backend cannot meter anon sessions at all. Bounding the anon layer
is the edge's job (Part 3). `LIBRARY_ITEM_RATE_LIMIT` (600/min) is a runaway-client backstop
— do not tune it as an enumeration control.

**DEFERRED: the keyset-cursor surface split.** It was the expensive piece — a second
rendering path — and with anon capped at page 1 it only ever protected against authed users,
which 2.2 handles far more cheaply. Revisit only if 2.3 actually fires.

---

# PART 3 — CLOUDFLARE CUTOVER (order is load-bearing)

| # | Step | Gate before proceeding |
|---|---|---|
| 3.1 | ✅ **DONE 2026-07-28** — Cloudflare account (dedicated ops identity, email **not** on rayhanai.com, TOTP not Google SSO). **Free plan.** Zone added | Upgrading later is a billing click — no DNS rework |
| 3.2 | **Move server→server calls to Railway private networking.** Value: `INTERNAL_API_URL=http://luna-backend.railway.internal:8000` (http, not https — no TLS on the private net; port required). Applies to the two server-only fetchers: `frontend/lib/library/api.ts`, `frontend/lib/seo/sitemap.ts` | ⚠ **Do this BEFORE any rate limit.** Otherwise every ISR render arrives from one Railway egress IP, trips the limit, and the whole site goes down. ⚠⚠ **Bind fix — and `--host ::` is the WRONG fix, it would 502 the public site.** CPython's `asyncio.base_events.create_server` sets `IPV6_V6ONLY = True` on every `AF_INET6` socket ("Disable IPv4/IPv6 dual stack support" in the source), so `::` binds IPv6-**only** and Railway's public edge, which reaches containers over IPv4, gets nothing. Correct fix, applied: **`--host ""`** — asyncio maps `''` to all interfaces and opens one socket per family (`0.0.0.0` *and* `[::]`). Verified on the pinned uvicorn 0.49.0 path: `--host ''` → 2 listeners, `--host '::'` → 1 with `V6ONLY=1` |
| 3.2b | ✅ **BUILT 2026-07-28**, env-gated `LIBRARY_SITEMAP_INTERNAL_ONLY`, **default OFF** — flipping it before 3.2 lands 404s every section at once | The single largest bulk-enumeration surface in the product. ⚠ **The hop-marker check must stay FIRST** in `_is_internal_caller`: on Railway the public edge dials the container *from a private address*, so a naive peer-address test would classify the entire internet as internal — and checking hops first also defeats a forged `Host: x.railway.internal`. A refused caller gets the **same 404 «القسم غير موجود»** an unknown section gets; an enumeration surface must not confirm its own existence. Cutover: 3.2 → verify `/sitemaps/{section}` renders → flip → re-check GSC Sitemaps report |
| 3.3 | ◐ **PARTIAL 2026-07-28** — nameservers **DONE**, SSL/TLS **`full` → `strict` DONE**, records deliberately left **grey**. Still to do: proxy **both** frontend and backend | Backend must be proxied: the public library API lives there. ⚠ Do NOT flip the orange cloud until 3.2, 3.2b, 3.5 and 3.6 are deployed — a proxied `api.` with 3.2 undone means the ISR renderer's server-side fetch is treated as a headless client and the library 404s site-wide. Full (strict) is now set, so the Railway redirect loop is pre-empted |
| 3.4 | ✅ **BUILT 2026-07-28** (inert) — edge: Transform Rule injects `X-Edge-Secret`. Backend: `middleware/origin_lock.py`, env var **`EDGE_SECRET`**, exempts `/api/v1/health`, registered **just inside CORS**, 22 tests | ⚠ **The cutover order below was WRONG in an earlier revision of this plan and is corrected here.** It is a default-OFF middleware, so setting the env var first ARMS the lock while every record is still grey — rejecting 100% of traffic. See "3.4 cutover order" below |
| 3.5 | Switch the rate limiter to `CF-Connecting-IP` (`rate_limit.py:89-90`, `public_ask.py:112-117`) | Leftmost XFF is attacker-controlled — Cloudflare *appends* to a client-supplied header |
| 3.6 | Add `/cdn-cgi/` to CSP `script-src` | Must precede 3.8, or JS Detections break silently |
| 3.7 | ✅ **DONE 2026-07-28** (all three layers) — CF Transform Rule sets `X-Verified-Bot`; `frontend/lib/library/crawler-signal.ts` (`server-only`) re-derives it; backend honours it when `TRUST_CF_HEADERS` is on, UA allowlist as fallback | ⚠ **Claim + proof, not claim alone.** The renderer forwards `X-Verified-Bot: 1` only when the header is truthy in *every* copy (Cloudflare appends, so `"1, 0"` = a forged copy → fail closed) **AND** `X-Edge-Secret` matches. Without the proof step, anyone could curl the raw `*.up.railway.app` frontend host with `X-Verified-Bot: 1` and the renderer would re-emit it carrying its own valid edge secret — laundering a forgery into a backend that trusts it. ⚠ Sends `1` or **nothing**, never `0`, so the human path is byte-identical on the wire *and* in the Data Cache key. ⚠ Exempted responses forced `Cache-Control: private, no-store`: the edge cache keys on URL, so a crawler's page-9 body left cacheable would be replayed to every anonymous human for an hour, silently lifting the cap for everyone — and the same trap exists one layer down in Next's Data Cache, closed because `init.headers` is hashed into the fetch cache key |
| 3.8 | **[PRO] SBFM**: verified bots Allow · definitely-automated → Managed Challenge · JS Detections ON · static-resource protection OFF | ⚠ **On Free, leave Bot Fight Mode OFF.** It is zone-wide and cannot be skipped by a WAF rule, so it would challenge signed-in users and your own Playwright agents with no exemption possible — breaking contract rules 1 and 2. Free therefore ships with no bot detection |
| 3.9 | ✅ **DONE 2026-07-28**, inert until proxied — all 4 WAF custom rules created in order (see below) | Order is the whole point |
| 3.10 | ✅ **DONE 2026-07-28**, inert until proxied — cache rules: `/api/v1/public/library/*` cache-eligible (respect origin TTL), all other `/api/*` bypass | Net saving on Railway compute |
| 3.11 | **[PRO] Rate limits** RL1 + RL2 at loose thresholds, observe 7 days, then tighten | Free gives 1 rule with a 10s window and 10s mitigation — not usable. Don't configure it; it would read as coverage that isn't there |
| 3.12 | ✅ **SET 2026-07-28**, inert until proxied — **AI bots**: Training = **block** · Search = **allow** · Agent = **allow** (OAI-SearchBot, PerplexityBot). Managed robots.txt ON | Being cited is a discovery channel. Cloudflare **prepends** its managed robots.txt to the origin's rather than replacing it, so `frontend/app/robots.ts` and 1.2's crawler blocks both survive. Only side-effect: GSC may report *"Syntax not understood"* on the Content-Signal lines — cosmetic |

## 3.9 — WAF rules, in evaluation order

Free allows 5 custom rules, so the three skips merge into one `or` expression — 4 rules, one
spare. Split them back out on Pro only if the analytics need per-rule attribution.

**Built 2026-07-28 exactly as below.** Matching is `lower(http.user_agent) contains …` throughout,
because Cloudflare's `contains` is case-sensitive and crawler UA casing varies.

| Order | Match | Action | Free |
|---|---|---|---|
| 0 | `lower(http.user_agent)` contains any of the 1.2 crawler list | **Block** | load-bearing |
| 1 | `cf.client.bot` **or** `http.cookie contains "sb-"` **or** `authorization` header present **or** `x-webhook-secret` header present | Skip — products `waf, rateLimit, securityLevel, bic, uaBlock, zoneLockdown, hot` | mostly no-op until Pro, **except `bic`** — Browser Integrity Check is ON today and this is what stops it hitting service callers |
| 2 | `/sitemap.xml` **or** `/sitemaps/*` **or** `/api/v1/public/library/sitemap*` **and not** (`cf.client.bot` **and** googlebot/bingbot/duckduckbot/yandexbot/baiduspider) | Managed Challenge | load-bearing |
| 3 | `/internal/*` **and not** (`authorization` **or** `x-webhook-secret` header present) | Block | see the correction below |

⚠ **Rule 1 skips PRODUCTS only, never `ruleset: current`.** If it skipped the current ruleset, any
signed-in user would also bypass rule 3 — the origin-probing block would be trivially defeated by
holding a session cookie.

⚠ **CORRECTION 2026-07-28 — `/internal/*` is NOT internal.** The original rule 3 ("`/internal/*` →
Block") and step **1.5** both assume nothing outside reaches it. Both are wrong, and shipping the
blanket block would have broken production at flip time:
- `POST /internal/summarize-workspace-item` — invoked by **Supabase database triggers**, i.e. from
  Supabase's infrastructure over the public internet. Auth: `X-Webhook-Secret` (`internal_webhooks.py:68`).
- `POST/GET /internal/blog-post-jobs` — invoked by **marketing**, over the public internet. Auth:
  `Authorization: Bearer <EDITORIAL_SERVICE_KEY>` (`deepsearch_api/auth.py`).

Both are already shared-secret authed, so they are not unprotected — they are *service-authed public*
endpoints that merely live under an `/internal` prefix chosen for visual separation
(`main.py:619-621` says so explicitly). Rule 3 therefore blocks only when **neither** auth header is
present, which still stops anonymous probing. **Step 1.5 must be re-scoped the same way** — do not
firewall `/internal/*` off the public internet, or the workspace-item summarizer dies silently and
`summary_sweeper.py` becomes the only thing keeping summaries alive.

⚠ **Rule 0 must be first.** Cloudflare's Verified Bots list includes AhrefsBot, SemrushBot and
DotBot, so `cf.client.bot` (rule 1) would otherwise exempt them from everything *and* **rule 2**
would serve them the sitemap — a one-request title dump to the exact companies reselling URL
inventories. Category filtering is Enterprise-only; ordering is the fix, and it works on Free.

## 3.4 cutover order (corrected 2026-07-28)

`origin_lock` is **default-OFF**: `EDGE_SECRET` unset or blank ⇒ every request forwards untouched.
That inverts the naive ordering. Setting the env var before the orange cloud arms the lock while
traffic is still bypassing Cloudflare entirely, so nothing carries the header and **100% of
production traffic 403s**.

1. Deploy the middleware with `EDGE_SECRET` **unset** — inert, safe to ship today.
2. ✅ **DONE** — repoint the Supabase Vault webhook. Migration 043's trigger reads its target from
   `vault.decrypted_secrets['artifact_summarizer_webhook_url']`, which pointed at the **raw Railway
   hostname** and so never transited Cloudflare. Now `https://api.rayhanai.com/internal/summarize-workspace-item`.
   Verified both hostnames return an identical `401` on that route before switching, so it was a
   no-op while grey. Revert value if ever needed:
   `https://luna-backend-production-35ba.up.railway.app/internal/summarize-workspace-item`.
3. ☐ Confirm the **marketing / editorial caller** of `/internal/blog-post-jobs` also targets
   `api.rayhanai.com`. It lives outside this repo — same failure mode, not yet verified.
4. Flip the orange cloud.
5. Verify `X-Edge-Secret` actually arrives at the origin — **and capture its on-the-wire shape while a
   client also sends the header.** `origin_lock._header_matches` uses `getlist`, which only separates
   *distinct header lines*. Proven live: two separate `X-Edge-Secret` lines (forged + real) → `200` ✅,
   but one comma-folded line `forged, real` → `403` ✗. If the edge ever folds rather than appends, a
   client who pre-sends the header would make the origin 403 its *own* legitimate proxied traffic — a
   self-inflicted DoS any third party could trigger. Not a bypass (the attacker still needs the secret),
   but check the shape, not just presence.
6. **Only then** set `EDGE_SECRET` in Railway — an env-var change, so it triggers the master-pull
   deploy trap.

⚠ **Set `TRUST_CF_HEADERS` at step 6 WITH `EDGE_SECRET`, not at step 4.** `rate_limit.py` says to flip
it "at the same moment the orange cloud is enabled", which opens a window where neither control binds.
Proven live on the current default config: 25 anonymous requests to the metered
`/api/v1/library/full/*` with a **rotating `X-Forwarded-For`** never trip its 20/min fail-closed bound —
and that bypass **survives `TRUST_CF_HEADERS=true`**, because a request arriving without
`CF-Connecting-IP` still falls back to the forgeable XFF chain. What actually closes it is 3.4 making
the raw `*.up.railway.app` hostname unreachable. **3.4 is therefore not defence-in-depth for 3.5 — it
is 3.5's precondition.** Not a regression versus today (XFF is already forgeable), but between steps 4
and 6 the cutover would buy nothing.

⚠ **3.2 and 3.4 contradict each other and the plan never said so.** Once 3.2 lands, the ISR
fetchers reach the backend over `luna-backend.railway.internal`, which never transits Cloudflare
and therefore carries no `X-Edge-Secret`. With the lock armed that 403s every anonymous library
page — and those fetchers return `null` on non-OK, so the pages call `notFound()` and **Google is
served 404s on live pages**. Fix applied: the Next server attaches the header itself on internal
calls, reading the same `EDGE_SECRET` (server-only, never `NEXT_PUBLIC_`). Rejected alternatives:
exempting `Host` ending `.railway.internal` (Host is client-supplied, trivially forged) and keeping
ISR on the public hostname (defeats 3.2 entirely).

⚠ `/internal/*` is deliberately **NOT** exempt from the origin lock. Those routes are anonymously
reachable; their shared secrets are *authentication*, the lock is their *network boundary*. A test
pins this.

## 3.11 — Rate limits (2 rules, IP-only counting on Pro, ≤60s window)

| Rule | Scope | Start | Action |
|---|---|---|---|
| RL1 | `/api/v1/public/*` | 120/min → tighten to 60 | Managed Challenge |
| RL2 | `/regulations/*`, `/judgments/*`, `/circulars/*`, `/compliance/*`, `/forms/*` | 60/min | Managed Challenge |

Both are effectively anon-only because **WAF rule 1** skips crawlers, signed-in users and your
own tooling in a single merged expression. RL1 depends entirely on 3.2 being done first.

## Cutover log

### 2026-07-28 20:05 — zone live on Cloudflare Free, everything grey-clouded

Nameservers `nsb1–4.squarespacedns.com` → `brit.ns.cloudflare.com` + `vasilii.ns.cloudflare.com`.
Registrar/DNS was Squarespace; the domain is Google Workspace-managed for mail.

**Verified from outside after the move:** all 11 records answer from Cloudflare · apex `200` ·
`www` `308` → apex · `api.rayhanai.com/api/v1/health` `200` · origin cert still
`CN=rayhanai.com` issued by Let's Encrypt.

**Final zone (11 records):**

| Type | Name | Value | Proxy |
|---|---|---|---|
| CNAME | `@` | `svuxsapz.up.railway.app` | grey |
| CNAME | `www` | `g3uyeekc.up.railway.app` | grey |
| CNAME | `api` | `2i36fg3x.up.railway.app` | grey |
| CNAME | `_domainconnect` | `_domainconnect.domains.squarespace.com` | n/a |
| MX | `@` | `smtp.google.com` (pri 1) | n/a |
| TXT | `@` | `v=spf1 include:_spf.google.com ~all` | n/a |
| TXT | `@` | `google-site-verification=PeLIbjYr1BIEfxtPC6w6zbjpfrAuEztO6HSEB-AL2xQ` | n/a |
| TXT | `google._domainkey` | DKIM, 410 chars, stored as two ≤255-char strings | n/a |
| TXT | `_railway-verify` | `railway-verify=33432e4b…89727a` | n/a |
| TXT | `_railway-verify.www` | `railway-verify=45c9a7ca…6ef753` | n/a |
| TXT | `_railway-verify.api` | `railway-verify=76f75cae…b6bf32` | n/a |

### Traps hit — read before any future DNS move

⚠ **Cloudflare's DNS auto-scan found 8 of 11 — it missed all three `_railway-verify` TXT
records** (underscore-prefixed names). They were added by hand before activation. Had they
stayed missing, Railway's periodic re-verification would have marked all three custom domains
unverified and cert renewal would have failed — **weeks later**, long after anyone would
connect the outage to the DNS move. Always diff the scan against a real zone dump.

⚠ **The apex was a Squarespace ALIAS flattened to a Railway edge IP** (`69.46.46.89`). Replaced
with `CNAME @ → svuxsapz.up.railway.app`; Cloudflare flattens root CNAMEs, so a Railway edge-IP
rotation can no longer strand the apex. Railway per-domain targets: apex `svuxsapz` ·
www `g3uyeekc` · api `2i36fg3x`.

⚠ **Ignore Cloudflare's onboarding "Recommended: only allow Cloudflare IP addresses at your
origin."** While records are grey it blocks 100% of real traffic, and Railway exposes no
IP-allowlist firewall in the first place. The correct mechanism is already **3.4** — a
Transform Rule injecting `X-Edge-Secret`, exempting `/api/v1/health`.

- **No DNSSEC on the zone** (no DS at the parent, no DNSKEY) → nothing to disable, which is the
  single most common way this move takes a domain dark. Left off deliberately: enabling
  Cloudflare DNSSEC means pushing a DS record back to Squarespace, another failure mode to
  carry mid-cutover. Revisit once the edge stack is stable.
- `_domainconnect` goes inert once nameservers leave Squarespace. Deletable, harmless.
- **One-line "am I actually proxied" check:** grey ⇒ origin cert issued by **Let's Encrypt**
  (Railway's own). Orange ⇒ issuer flips to Cloudflare's edge CA.

### 2026-07-28 (later) — edge config applied via API token; still all grey

The hosted `mcp.cloudflare.com` OAuth grant turned out to be **developer-platform scoped only**
(Workers/Pages/D1/AI). Its catalogue contains no WAF, rulesets, zone-settings-write or Turnstile
scope, so no amount of re-consenting could execute this plan — re-auth attempts actually *narrowed*
the grant. Resolved with a **user API token** (`dash.cloudflare.com/profile/api-tokens`, not the
account-token page, which is account-permissions-only). Token = Zone Settings/WAF/Transform
Rules/Cache Rules/DNS Edit + Zone Read + Turnstile Edit, scoped to `rayhanai.com`.

Applied, **all inert — every record still grey, verified 0 proxied after the change:**

| Step | State before | State now |
|---|---|---|
| 3.3 SSL/TLS | `full` | **`strict`** |
| 3.9 WAF custom | phase empty | **4 rules**, in order |
| 3.10 Cache rules | phase empty | **2 rules** |
| 3.4 Transform Rule | phase empty | **1 rule**, injects `X-Edge-Secret` |

Post-change health: apex `200`, `www` `308`, `api/v1/health` `200`, all three certs still
**Let's Encrypt** — i.e. still origin-direct, nothing silently proxied.

⚠ **`X-Edge-Secret` must reach Railway before the 3.4 middleware ships.** The rule injects a
generated value; if the middleware deploys expecting a different one, the origin rejects 100% of
proxied traffic. Order: env var in Railway → middleware deployed → orange cloud.

⚠ **Browser Integrity Check is ON** (`browser_check=on`) and was never in this plan. It challenges
requests with non-browser-shaped headers, which is exactly what the ISR renderer and both service
callers send. That is why rule 1's skip list includes `bic` — without it, going orange would break
them even on Free where SBFM doesn't exist.

Noted, deliberately **not** changed (outside plan scope, flagged for a decision):
`min_tls_version = 1.0` (weak; 1.2 is the sane floor) · `always_use_https = off` ·
`security_level = medium`.

Still unverifiable: **3.12's AI-bot settings and whether Bot Fight Mode is off** — `/bot_management`
returns `10000` because Bot Management Read wasn't included on the token. Add it, or eyeball both
in the dashboard before flipping.

### Still to confirm manually

- [ ] Google Workspace: send **and** receive a real message
- [ ] Railway dashboard: all three custom domains still read *verified*
- [ ] GSC: property still verified

---

## Live validation log — 2026-07-28, against a local stack

Five validators ran against real running servers (backend `:8000`, frontend `:3000`, plus isolated
instances on `:8011`–`:8013` for armed states, since flipping an env var on a shared instance would
corrupt every other agent's run). Redis was DOWN (Docker Desktop not running), so Redis-backed paths
exercised their fallbacks — stated wherever it affected a result.

**Passed:** 1.2 · 1.3 · 3.6 · 3.4 (16/16, incl. the health exemption under a *wrong* header, CORS
preflight not blocked, lock-403 still carrying CORS headers, prefix-of-secret refused, non-ASCII → 403
not 500, `/internal/*` correctly not exempt) · 3.5 (both flag states, empty-XFF-hop fix proven by
bucket collision) · 3.2b (gated 404 **byte-identical** to the unknown-section 404 — same sha256, empty
header diff, no timing oracle at 3.4 ms vs 3.35 ms) · `crawler-signal.ts` 15/15.

⚠ **Environment trap that produced a false FAIL:** two `next dev` processes were sharing one `.next`
directory (a stale server from an earlier session holding `:3000`, so a new launch silently fell back
to `:3001`). Identical requests returned 200, then HTML, then 404; the stale bundle served a CSP with
no Turnstile `frame-src`, reading as a code defect. **Always confirm exactly one dev server owns the
port before trusting a frontend validation result.**

### Two bugs found and FIXED

1. **HIGH — the backend read only the FIRST `X-Verified-Bot` copy.** `Headers.get()` returns the first
   matching line, so `X-Verified-Bot: 1` followed by `0` **lifted the anonymous depth cap** — proven
   live with `TRUST_CF_HEADERS=true` (`items=9`, `cap_reached=False`). Order-dependent, and the
   vulnerable order is exactly what an append produces. This is §3.5's leftmost-header trap reproduced
   on the bot header, and after cutover that single read is the *entire* trust basis of §3.7 (the UA
   fallback is disabled by design once the flag is on). Not reachable through Cloudflare — the Transform
   Rule uses `operation: set`, which overwrites — but the fix removes the dependency on a dashboard
   setting. Now `getlist()` + comma-split + **all** values truthy, mirroring `crawler-signal.ts`. Also
   fixes a benign false-negative where a legitimately duplicated `"1, 1"` slot was refused.
2. **MEDIUM — the count oracle survived one page earlier.** §2.1 capped `total_pages` only on the
   `cap_reached` body, which removed the *free* oracle (a count with no items) but **not the count**.
   Anon page 1 still returned the exact filtered total at the same ±9 granularity — measured live:
   judgments `q='نظام'`→4, `'قرار'`→5, `'محكمة'`→5, `'السعودية'`→1; regulations unfiltered→12. One
   request per filter value still read the corpus. **And it would have got worse at full-corpus
   rollout**: today that is the published-*sample* count, but in steady state the counter returns the
   TRUE filtered corpus count. Fixed with `_visible_total_pages(tier, real)` on all five served hub
   bodies — anon is clamped to the same flat ceiling, free/paid untouched. Anon can never advance past
   `ANON_HUB_MAX_PAGE`, so the real total tells them nothing actionable. Verified live: page-1
   `total_pages` is now only ever `{1, 2}` across 4 hubs × 6 filters, i.e. **one bit** ("is there more
   beyond this page?" — which the CTA wall needs) instead of ~log2(N) bits of corpus size.
3. **LOW — `_is_internal_caller` treated a present-but-EMPTY hop marker as absent.** Truthiness, so
   `X-Forwarded-For:` with an empty value let the forged-`Host: *.railway.internal` branch win for a
   genuinely public peer. Now tests `is not None`. (Method note for re-testing: `203.0.113.0/24` is
   TEST-NET-3, which `ipaddress.is_private` reports as **private** — use `8.8.8.8` as the public peer
   or the test silently proves nothing.)

# PART 4 — VERIFICATION

Run after 3.3, after 3.9, and after 3.11.

- [ ] GSC URL-inspection live test renders normally — **critical**
- [ ] GSC Sitemaps report reads every section (a 403 shows there as a fetch error)
- [ ] Anonymous `curl` of the sitemap path → challenged
- [ ] Direct Railway origin URL → 403
- [ ] Login + SSE chat streaming E2E through the proxy
- [ ] Signed-in session: zero challenges throughout
- [ ] `@smoke-tester` / Playwright still pass against prod
- [ ] Scripted 200-page walk → challenged; scripted 200-distinct-`q` walk → 429 from 2.2
- [ ] Normal lawyer session (30 min, filters + 20 documents) → never challenged, never 429
- [ ] GSC crawl stats stable for 7 days — any drop rolls back the last rule
- [ ] **3.7 actually fires.** It needs `EDGE_SECRET` **and** `TRUST_CF_HEADERS` **and** the Transform
      Rule to cover the *frontend* hostname. ✅ **Scope verified 2026-07-28:** both transform rules use
      zone-wide expressions (`http.host ne ""`, `cf.client.bot`), so they fire on `rayhanai.com` and
      `www` as well as `api.` — no per-hostname scoping to fix. The renderer logs a one-per-process
      `console.warn` when a bot *claim* arrives without valid *proof*; that is the signal separating
      "we refused a prober" from "the config is wrong"
- [ ] Confirm a deep hub page (`/regulations/page/9`) still returns **`noindex, follow`** to a verified
      crawler. `generateMetadata` is deliberately NOT exempted — it asks the anon question and its
      `cap_reached` answer drives the noindex. Exempting it would yield *indexable* deep pages and turn
      3.7 from crawl reach into index bloat

---

# PART 5 — WHEN TO UPGRADE TO PRO

**What Free leaves uncovered:** the anonymous layer is unbounded. Anon traffic reaches the
backend through the ISR renderer on one shared IP, so it cannot be metered in-app either —
this is a real, accepted gap, not something Part 2 covers. On Free the anon controls are the
sitemap gate, the crawler blocks, 3.2b and edge caching. That closes bulk *enumeration*; it
does not bound *rate*.

Upgrade the moment either becomes true:

1. **Cloudflare analytics show automated traffic the sitemap gate doesn't cover** — a rising
   share of non-browser requests on `/regulations/*` or `/api/v1/public/*`.
2. **Before publishing the مادة pages.** Today only 5 are published; the full set takes the
   anon surface from ~3,400 pages to ~54,000 overnight, and rate limiting stops being
   theoretical (`access_tiers_gating.md` Phase D).

Upgrading executes exactly three things: 3.8 (SBFM), 3.11 (RL1 + RL2), and optionally
splitting WAF rule 1 back into three. Nothing else changes — no DNS work, no redeploy.

---

# PART 6 — DECIDED AGAINST (do not re-open without new evidence)

| Rejected | Why |
|---|---|
| CAPTCHA every N pages | Not expressible in Cloudflare; highest UX cost in the product; solvers pass it anyway |
| Keyset-cursor surface split | Anon is already capped at page 1; 2.2 covers the authed case at a fraction of the cost |
| Three-tier publication (hiding the long tail) | User accepted the title-leak risk in exchange for reach |
| Business plan (~$200–250/mo) | Only if Pro's logs show real distributed scraping |
| Country / ASN blocking | Users travel and VPN |
| Free-plan Bot Fight Mode | Zone-wide, unconfigurable, cannot be skipped |
