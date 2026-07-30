# Navigation / Enumeration Defence — protecting the hub traversal endpoints

> **EXECUTE FROM [`cloudflare_navigation_hardening.md`](cloudflare_navigation_hardening.md)**
> (2026-07-28) — Part 2 there is the agreed scope: filter hole, per-user item budget,
> yield-to-open alert. The cursor surface split (§2.1 / N4 below) is **DEFERRED**. This
> document remains the reference for the analysis behind those choices.

**Status:** PLANNED 2026-07-27 — nothing built.
**Scope:** the multi-page browse surface (`/regulations` + `/page/{n}` and its siblings), not
document gating. Companions: [`defence_in_depth.md`](defence_in_depth.md) (edge + ledger) ·
[`access_tiers_gating.md`](access_tiers_gating.md) (entitlement).

**Premise:** gating documents while leaving traversal open still hands over the **map** — a
complete index of what exists, which is most of the work of cloning a legal library. But the
hub is also the SEO surface and the primary browse UX, so the defence has to sit somewhere
that costs a real lawyer nothing.

---

# §1 — THE THREAT IS NOT "SOMEONE READ PAGE 32"

It is "someone reconstructed the full index of 3,373 أنظمة cheaply". There are **three**
traversal dimensions, and depth is the least important:

| Dimension | Surface | Cost to a scraper today |
|---|---|---|
| **Depth** | `page=1..375` (9/page, `HUB_PAGE_SIZE`) | capped at 3 for anon — the one everybody thinks about |
| **Filters** | `entity` × `doc_type` × `sector` × **`q`** (ilike on `clean_title`) | **uncapped** |
| Sitemap | `/public/library/sitemap/{section}` | 5,000 URLs/request — closed in `defence_in_depth.md` §3.2 rule 4 |

## ⚠ 1.1 The filter dimension defeats the depth cap entirely

`q` is an unbounded `ilike` (`library_service.py:979-1007`, `_apply_reg_filters`). Each
distinct filter signature yields its own fresh 3 pages × 9 items = **27 items**, so
~125 well-chosen `q` values partition the entire regulation corpus **without ever requesting
page 4**. Every filter combination is a new corpus slice.

**Therefore: any control that counts PAGES is measuring the wrong thing.** The correct unit
is **distinct items yielded per session**, which is dimension-independent — depth, filters,
repeat queries and future sort orders all draw from one pool.

## 1.2 The asymmetry that makes this easy — exploit it

The hub is **ISR-cached** (`RegulationsHubView.tsx` is a server component; `/regulations` and
`/regulations/page/{n}` are static routes with a 1h revalidate). So the backend hub endpoint
sees roughly **one fetch per page per revalidation window from the Next server** — and
essentially nothing else.

Once server→server calls move to Railway private networking (`defence_in_depth.md` §2),
**any traffic arriving at `/api/v1/public/library/*` from the internet has almost no
legitimate origin.** That is a far stronger signal than counting page numbers, and it costs
real users nothing because real users never touch that endpoint directly — their browser
gets HTML from the ISR cache.

Enumerate the exceptions before relying on this (client-side callers of the public API:
اسأل ريحان popup, any future client-side filter UI) and give those a named allowance.

---

# §2 — ASSESSMENT OF THE TWO PROPOSALS (user, 2026-07-27)

## 2.1 «you can't just request page=32» — **ADOPT, refined**

The instinct is right: random access is what makes parallel fan-out cheap. Forcing sequential
traversal turns a 375-request burst into a serial walk that is attributable to one session.

Two corrections to the shape:

**(a) It cannot be bolted onto the current surface.** `/regulations/page/{n}` is a *shared,
ISR-cached static route*. "Did this visitor walk here?" is per-visitor state; putting it on a
shared-cached route either poisons the cache for the next visitor or forces the route
dynamic — the exact trap in `access_tiers_gating.md` PART 9 §2. **Split the surface instead:**

| Range | Who | Rendering | Pagination |
|---|---|---|---|
| Pages 1–3 | anon + Googlebot | ISR, `public, max-age=3600`, crawlable, `rel=next` | classic `/page/{n}` — **unchanged, zero friction** |
| Page 4+ | authed only (already the policy) | dynamic, `private, no-store` | **keyset cursor**, signed |

Deep pages are *already* an authed-only feature in the access plan's hub-depth table, so the
dynamic half is per-user by definition — cursors belong there and cost nothing.

The cursor is an HMAC-signed token carrying `(filter signature, last-seen sort key, issued_at,
session)`. You cannot fabricate `page=32`; you can only follow `next`. Short TTL (~10 min).

**(b) Never "redirect page=32 → page 1".** For a subscriber who bookmarked or shared a deep
link that is a silent data-loss bug, and for a scraper it is a one-line workaround. An
unknown or expired cursor should render **page 1 plus an «استأنف التصفح» affordance** — a
recoverable state, not a redirect.

**Bonus, and it justifies the work on its own:** today's pagination is OFFSET-based over a
two-partition scan (`library_service.py:1009-1034`), so page cost grows with depth — a
scraper walking deep pages is a DB-load amplifier. Keyset pagination makes deep pages O(1).
This is a performance fix that happens to be the defence.

## 2.2 «Cloudflare CAPTCHA every 2 pages» — **DON'T. Replace it.**

Three independent reasons:

1. **Not expressible in Cloudflare.** Managed Challenge fires on bot score or a rate-limit
   threshold — there is no per-N-requests counter. Approximating it means a rate limit of
   2/period, which challenges *everyone* constantly. And once passed, the visitor holds a
   `cf_clearance` cookie for the zone-wide Challenge Passage window, so "every 2 pages"
   cannot happen anyway without setting that window absurdly low for the whole site.
2. **It is the highest UX cost available.** It breaks `defence_in_depth.md` §0 invariant 4
   and lands precisely on the surface that does the SEO and the discovery. A lawyer browsing
   a filtered list is the most valuable anonymous session in the product.
3. **The bot cost is low.** Commercial solvers pass Managed Challenge; the bots that can't
   are already stopped by Super Bot Fight Mode. Net effect: heavy tax on users, light tax on
   scrapers — the wrong trade in both directions.

**Replacement with the same intent — behavioural step-up, not a fixed counter.** Friction
keyed to *how* the traversal looks:

- Trigger only when a session exceeds its item budget (§3) **and** shows non-human timing
  (sub-second inter-page dwell, no document opens between list pages).
- Then require **one Turnstile** — already implemented and wired in this codebase
  (`public_ask.py:181-182`), so this is configuration plus a call site, not a new dependency.
- Solving clears the session. A real user never sees it; a scraper hits it immediately and
  must solve one per session, per rotation.

---

# §3 — THE CONTROL: A SESSION TRAVERSAL BUDGET (items, not pages)

Count **distinct content ids yielded** by any hub/list endpoint, per session (anon) or per
user (authed), on a rolling window.

| Tier | Distinct items / hour | On breach |
|---|---|---|
| Anon | 60 (≈7 hub pages — more than any pre-signup browse) | Turnstile step-up |
| Free | 300 | Turnstile step-up, then 429 |
| Basic / Pro / Max | 500 — the whole 3,373-title index would otherwise fall in ~1.7h | detection only (§6 of defence plan), never a challenge |
| Verified crawler | ∞ | never counted, never challenged |

Implementation: a Redis set keyed `nav:{session|user}:{hour}` holding ids, 1h TTL. Ids only —
small, cheap, and it makes the budget immune to *how* the items were reached. Filters, deep
pages, repeated `q` probes and any sort order added later all draw from the same pool.

⚠ **Never in the ISR path.** The shallow cached pages must not touch Redis — they are served
from the Next cache and the origin sees one fetch per revalidation. The budget lives on the
backend hub endpoints and the dynamic cursor surface only.

---

# §4 — ADDITIONAL HARDENING, SPECIFIC TO NAVIGATION

1. **`total_pages` leaks the corpus size on capped responses.** `public_library.py:658-669`
   returns the *true* total with `items=[]` and `cap_reached=true`. That confirms corpus size
   and validates filter partitions — free reconnaissance. Return the capped value for anon.
2. **Constrain `q`.** Require ≥3 characters and cap the number of *distinct* `q` values per
   session. No human types `q=ا` to find a statute; single-character `ilike` probes are pure
   partition-enumeration.
3. **Bound the filter cross-product.** `entity` and `doc_type` are closed vocabularies —
   validate against the known set and reject unknown values rather than passing them to the
   query. Cheap, and it stops filter-space probing.
4. **Keep `noindex` on deep pages** (already true) — the shallow range is the only SEO
   surface, which is exactly what makes §2.1's split free.
5. **Future sort orders multiply the surface.** Each new sort is a fresh permutation of the
   same corpus. Because §3 budgets by item, adding sorts stays neutral — but only if the
   budget lands first.

---

# §5 — WHAT THIS COSTS A REAL USER

| Persona | Experience |
|---|---|
| Googlebot | Unchanged. Pages 1–3 classic, crawlable, never challenged, never counted |
| Anon visitor | Unchanged. Sees the same 3 pages; 60 items/hour is far past a pre-signup browse |
| Free account | Unchanged in normal use |
| Subscriber browsing deeply | Infinite-scroll / «التالي» on page 4+; back-button and bookmarks preserved via the resume affordance |
| Subscriber sharing a deep link | Recipient lands on page 1 with «استأنف التصفح» rather than a broken page |
| Scraper | Cannot fan out; serial only; trips the item budget regardless of which dimension it walks |

---

# §6 — THE INDEX ITSELF IS THE ASSET (user concern, 2026-07-27)

**The stated fear:** an authed scraper harvesting all 3,373 regulation *names*. Having the
name list is most of the data-acquisition work, because the statute text is public domain —
the list converts a research problem into a fetch problem.

**That is correct about the value, and wrong about the door.**

## 6.1 The title list is already public by construction

Slugs ARE the titles: `نظام-المحاماة`, `نظام-التجارة-الإلكترونية`, `نظام-المعاملات-المدنية`
(verified live 2026-07-27). Therefore **a URL is a title**, and the list leaks through
channels that have nothing to do with authenticated scraping:

| Channel | Cost to the taker |
|---|---|
| `/public/library/sitemap/regulations` | **1 request** = 5,000 URLs = 5,000 titles |
| Google's index of rayhanai.com | free, and it is a public mirror of your list by design |
| Ahrefs / Semrush / Moz URL inventories | ~$100/mo, no contact with your servers at all |
| Common Crawl archive | free, permanent |

The authed hub is the **narrow** door (375 paginated requests). The SEO strategy is the wide
one, and it is wide *on purpose*. No amount of authed gating closes it while the titles sit
on indexed public pages.

## 6.2 Restricting publication — CONSIDERED AND DECLINED (user, 2026-07-27)

A three-tier split (indexed ~300–500 / listed-but-`noindex` / unlisted-authed-only) would
keep the corpus *completeness* private at near-zero traffic cost, because value-for-SEO and
value-as-a-secret are almost perfectly anti-correlated: the head carries the traffic, the
obscure tail is what a competitor cannot assemble independently.

**Declined — reach beats index secrecy at this stage.** Everything is published. Revisit only
if a competitor demonstrably clones the list. It stays a data decision if so (published iff
`seo_item_meta.slug IS NOT NULL`), never a build.

## 6.3 What is done instead

1. ⭐ **The yield-to-open ratio — the cleanest detection signal in the system for this
   threat.** A real lawyer browses a little and opens documents; an index harvester yields
   thousands of list rows and opens nothing. Alert on **>200 list items yielded with 0
   document opens in a session** — computable the moment `library_items` exists
   (`access_tiers_gating.md` §5B.2), with essentially no false positives.
2. **The subscriber traversal budget** (§3, 500 items/hour) turns a full-index pull into
   ~7 hours of sustained, loud traffic.
3. **Block the SEO/archive inventory crawlers** — robots.txt is the primary control (they all
   honour it) plus a WAF rule against spoofers. Full list, the rule-order trap and the honest
   limits: `defence_in_depth.md` §3.4.

## 6.4 Fingerprint the index instead of trying to hide it

Since the head of the list must be public, make copying **provable** rather than impossible:

- **`clean_title` normalization is already a fingerprint.** The cleaning pipeline is
  idiosyncratic — a competitor whose corpus matches your normalized titles character-for-
  character, quirks included, did not derive them independently. **Freeze a snapshot and
  write the normalization rules down now**, so it is evidence later. Zero cost.
- A handful of unique slug **aliases** that only you use, pointing at real regulations.
- ⚠ **Never seed fabricated أنظمة as trap streets.** Map-makers can afford fictitious
  streets; a legal product cannot afford a fake law reaching a user. Canaries live in
  aliases and normalization, never in invented legal content.

---

# §7 — PHASES

| Phase | Scope | Done when |
|---|---|---|
| **N1** | `total_pages` cap · `q` min-length + distinct-value cap · closed-vocabulary validation on `entity`/`doc_type` | Anon capped response no longer reveals the true total; single-char `q` rejected |
| **N2** | Session traversal budget (Redis, items-not-pages) at all hub endpoints, generous thresholds, **observe only** for 7 days | Real p99 per session measured; no user near the limit |
| **N3** | Enforce budget + Turnstile step-up on breach (anon), 429 + Arabic card (free) | Scripted 200-page walk trips it; a scripted filter-partition walk trips it *too* |
| **N4** | Surface split: keyset cursor + `private, no-store` for page 4+; pages 1–3 unchanged ISR | Googlebot still crawls 1–3; deep bookmark resumes cleanly; deep page latency flat with depth |

N1 is a same-day change. N4 is the largest and depends on the access plan's hub-depth
rework, so it should ship with it rather than before it.

## Verification

- [ ] GSC crawl stats flat across N1–N4; pages 1–3 still indexed
- [ ] A subscriber's bookmarked deep link resolves to a usable page (no redirect-to-1)
- [ ] Scripted walk of `page=1..200` → budget trips, Turnstile appears once
- [ ] Scripted walk of 200 distinct `q` values, page 1 only → **budget trips too** (this is
      the test that proves the unit is right)
- [ ] Normal lawyer session (30 min, filters + 20 documents) → never challenged, never 429
- [ ] Deep-page p95 latency no longer grows with page number
