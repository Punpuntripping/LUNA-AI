# Cloudflare Protection — anti-scraping + edge hardening for rayhanai.com

> **SUPERSEDED 2026-07-27 by [`defence_in_depth.md`](defence_in_depth.md)**, which folds this
> track in as Layer 1 and corrects the rule specs below against verified Pro plan limits
> (2 rate-limit rules, **IP-only** counting, ≤60s period — so ">100 per 10 min" in Step 3 is
> not expressible on Pro). Build from that document; this one is kept for the threat model
> and the DNS-cutover traps.

**Status:** PLANNED 2026-07-22 — not started. Ops track (account setup, DNS migration, manual
dashboard work) — runs AFTER the SEO library phases (`seo_public_library.md`), but is
independent of them and also benefits the whole app.

**Goal:** make bulk-cloning the public library expensive without touching Google rankings.
This protects the FREE content layer; the gated layer is already scrape-proof by server-side
truncation (SEO plan Phase 1) and never waits on this plan.

## Threat model

- Gated content (شرح, full docs, judgment texts, ask answers): never leaves the server. ✔ done elsewhere.
- Free layer (summaries, TOCs, open-tier مواد, compliance pages): the bulk-harvest target.
- Enumeration paths: hub pagination (already closed — server-side depth cap), **sitemaps**
  (closed HERE via verified-bot gating), link-by-link crawling (throttled HERE).
- Nothing here is absolute — residential-proxy crawlers can still go slow; the goal is cost.

## Step 0 — Account & prerequisites (user, manual)

1. Cloudflare account; add zone `rayhanai.com`.
2. **Plan: Pro (~$25/mo)** — Super Bot Fight Mode + enough WAF/rate-limit rule slots (free tier
   = 1 rate-limit rule, no path-scoped bot rules worth having).
3. Inventory current DNS records BEFORE migration (domain currently points at Railway,
   [[project_domain_rayhanai]]).
4. Decide AI-bot policy (see Step 4).

## Step 1 — DNS through Cloudflare (proxied)

- Move nameservers to Cloudflare; orange-cloud the frontend (and decide for backend — see SSE
  warning below). SSL mode **Full**.
- ⚠ Traps: [[project_domain_rayhanai]] (NEXT_PUBLIC_API_URL build-arg, CSP-precedes-rebuild),
  [[feedback_railway_master_pull_trap]] (env-var change triggers master-pull deploy). Verify
  CORS + CSP after cutover.
- ⚠ **SSE**: if the backend API goes behind the proxy, chat streaming must be verified
  (response buffering OFF for `/api/*` streams). Test BEFORE committing; leaving the backend
  un-proxied is acceptable (protection targets the public content pages, which are frontend).

## Step 2 — Origin lock (critical — without it everything else is bypassable)

Railway origin URLs (`*.up.railway.app`) must not serve the public: restrict to Cloudflare IP
ranges, or a shared-secret header set by a CF Transform Rule and checked in app middleware.

## Step 3 — WAF rules (path-scoped)

| Rule | Scope | Action |
|---|---|---|
| Verified-bot allowlist | global | Verified search crawlers (real Googlebot/Bingbot — CF verifies by IP/rDNS, not UA) always pass. NOT cloaking: identical content; only unverified automation is challenged. |
| Managed Challenge on automation | `/regulations/*`, `/judgments/*`, `/forms/*` (+extend as needed) | bot-score/automated → Managed Challenge |
| Rate limit (tune) | same paths | >30 req/min/IP → challenge; >100 → block 10 min |
| **Sitemap gate** | `/sitemap*.xml`, `/sitemaps/*` | Verified bots ONLY; others challenged. Sitemaps are for crawlers — zero SEO cost, closes list-enumeration. |

## Step 4 — AI-bot policy (user call)

- Block bulk trainers (CCBot, Bytespider, …) — one-click.
- CONSIDER allowing AI search crawlers (GPTBot search, PerplexityBot): being cited in AI
  answers is a discovery channel for exactly this audience. Per-bot dials.

## Step 5 — Complements (cheap, non-Cloudflare)

- Backend IP rate limits stay on (second net).
- **Canary text**: distinctive phrasings seeded in AI summaries/شرح (can be done during SEO
  Phase 3 generation — raw law is public domain, the summaries are Rayhan's copyrighted text;
  canaries prove theft for takedowns).
- Terms of use: explicit no-scraping clause (coordinate [[project_legal_docs_terms_privacy]]).

## Verification checklist

- GSC URL-inspection fetch renders normally post-cutover — CRITICAL.
- curl sitemap anonymously → challenged; GSC still reads it fine.
- Scripted 50-page crawl from one IP → rate-limited.
- Direct origin URL hit → refused.
- Login + SSE chat streaming E2E through the proxy.
- Watch GSC crawl stats 7 days — any crawl-rate drop = rule misfire, roll back that rule.

**Agents:** mostly manual (CF dashboard) + @deploy-checker (post-change verify) + @validate
(app E2E incl. SSE streaming).
**Done when:** all checklist items pass AND GSC crawl stats stable for 7 days.
