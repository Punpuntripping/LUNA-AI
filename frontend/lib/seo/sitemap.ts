// Shared helpers for the sitemap index (`app/sitemap.xml`) and the per-section
// URL sets (`app/sitemaps/[section]`). Keeping the section list + XML helpers in
// one place means adding a future wing (regulations, judgments, …) is a
// one-line edit here plus a case in the section route.

import { CALCULATORS } from "@/lib/calculators/registry";
import { getSectors } from "@/lib/library/api";
import { ENTITY_ORDER, entityPath } from "@/lib/library/entities";
import { sectorPath } from "@/lib/library/sectors";
// Backend origin + request init for the anon sitemap feed endpoints. Server-only,
// and deliberately the SAME pair the library fetchers use: `INTERNAL_API_URL`
// (runtime, Railway private network) → `NEXT_PUBLIC_API_URL` (Docker build ARG)
// → localhost, plus the `X-Edge-Secret` the origin lock demands of anything that
// did not transit Cloudflare. The feed is itself a `/api/v1/public/library/*`
// endpoint, so one definition lives next to those fetchers instead of two here.
import { SERVER_API_BASE, serverFetchInit } from "@/lib/library/api";

/** Canonical production origin. Matches `metadataBase` in `app/layout.tsx`. */
export const SITE_URL = "https://rayhanai.com";

/**
 * Sections listed in the sitemap index, each served at `/sitemaps/{section}`.
 * Phase 0 shipped `static` (hardcoded marketing/legal pages) and `blog`; Phase 2
 * adds `regulations`. Phase 3 adds `calculators`, which is served
 * LOCALLY from the code registry (no backend feed). Every backend-fed section
 * uses the same `{ urls, page, total_pages }` feed contract. Later phases append
 * `judgments`, `circulars`, `articles`, …
 *
 * `judgments` (the per-document URLs) joined on 2026-08-11, and is NOT the whole
 * wing. The backend feed lists only rows flagged `seo_item_meta.indexable`
 * (migration 130) — 3,000 of the 10,000 published rulings, PDPL-cleared and
 * diversity-selected by `scripts/build_judgment_slugs.py --indexable`. The other
 * 7,000 stay servable and stay `noindex`.
 *
 * ⚠ THE DOCUMENT PAGE READS THE SAME FLAG. `app/judgments/[slug]/page.tsx` sets
 * `robots` from `doc.indexable`; this section lists exactly the rows carrying
 * it. One rule, two consumers, and it has to stay that way — restating it
 * independently on either side is how a URL ends up listed here and `noindex`
 * there, which Search Console reports as "Submitted URL marked noindex".
 * Changing WHICH rulings are indexed is a DATA change (re-run the selector, then
 * purge ISR), never an edit to this file.
 *
 * The judgment HUB (`/judgments`, `/judgments/page/{n}`) stays out, and stays
 * `noindex`: an enumerable index of every ruling is the crawl the PDPL gate
 * exists to prevent.
 *
 * `compliance` joined on 2026-08-19 with the `service_guides` corpus — the
 * `/compliance/{slug}` service guides (169 at the time, all 337 today). It was
 * held out while the wing was deliberately EMPTY (a listed section serving an
 * empty urlset is a file Google refetches hourly to learn nothing); the guides
 * are our own authored rewrites of the entities' official PDF user-guides,
 * published in full and ungated, so every listed URL is a fully crawlable page
 * with no gate to contradict. The backend feed lists only slugged + indexable
 * rows, exactly like every other section here — which is also why hub depth is
 * irrelevant: an anonymous visitor is capped at page 1, and every guide URL
 * still ships from this file.
 *
 * ⚠ `courts` WAS HERE AND IS NOT ANY MORE (removed 2026-08-11). The 12 court
 * section pages were the one carve-out from that gate — indexable because they
 * list derived titles, never judgment text. Then a court became a paid-only
 * SECTION (`section_scope_allowed` in `public_library.py`), so what an anonymous
 * visitor — and therefore Googlebot — now gets at `/judgments/courts/{slug}` is
 * the section wall, and the pages went back to `noindex, nofollow` with the rest
 * of the wing. With no indexable page left there is no `courts` urlset to serve,
 * so the section is gone from this tuple AND its case is gone from
 * `app/sitemaps/[section]` — a listed section with an empty urlset is a file
 * Google refetches hourly to learn nothing.
 *
 * Restoring it is the same three-part edit as before, in reverse: un-gate the
 * court axis, drop the `robots` key from `app/judgments/courts/[court]/page.tsx`,
 * and re-add "courts" here plus its route case. Do not do one without the others.
 *
 * ⚠ `compliance-entities` IS LISTED THOUGH `courts` IS NOT, AND THAT IS NOT AN
 * INCONSISTENCY. Both are a wing's own SECTION axis; they differ in exactly the
 * thing this file cares about — what an anonymous visitor gets at the URL.
 * `/judgments/courts/{slug}` serves the SECTION WALL, because a court is a
 * paid-only section under `section_scope_allowed()`, so listing it would submit
 * a `noindex` page (and serving Googlebot cards no signed-out human can reach
 * would be cloaking). `/compliance/{entity}` serves the CARDS: the entity axis
 * is deliberately exempt from that gate (compliance_entity_sections D1 — the
 * wing is 100% published and ungated, all 337 guide URLs are already listed in
 * the `compliance` section above, and the guides are our own text), so those 28
 * pages carry no `robots` key at all and this section contradicts nothing.
 *
 * ⚠ PAGE-1 URLs ONLY, for the same reason. `/compliance/{entity}/page/{n}` does
 * carry `noindex, follow` whenever the anon depth cap walls it, so a deep entity
 * URL here would be precisely the "Submitted URL marked noindex" self-
 * contradiction the removed sector lists were pulled for.
 */
export const SITEMAP_SECTIONS = [
  "static",
  "blog",
  "regulations",
  "articles",
  "circulars",
  "forms",
  "calculators",
  "sectors",
  "judgments",
  "compliance",
  "compliance-entities",
] as const;

export type SitemapSection = (typeof SITEMAP_SECTIONS)[number];

/** One `<url>` entry. `lastmod` is an ISO-8601 timestamp when known. */
export interface SitemapUrl {
  loc: string;
  lastmod?: string;
}

/** Escape the five XML predefined entities so `<loc>` values stay well-formed. */
export function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/** Render a list of URLs as a complete `<urlset>` XML document. */
export function renderUrlset(urls: SitemapUrl[]): string {
  const entries = urls
    .map((u) => {
      const lastmod = u.lastmod
        ? `\n    <lastmod>${escapeXml(u.lastmod)}</lastmod>`
        : "";
      return `  <url>\n    <loc>${escapeXml(u.loc)}</loc>${lastmod}\n  </url>`;
    })
    .join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${entries}
</urlset>`;
}

/** Hardcoded marketing + legal pages — the `static` section. The `/learn` HUB
 *  joined once its second lesson (/learn/workspace) landed — it previously
 *  carried `robots: noindex` while a single lesson made it thin; its live
 *  lesson pages are full content and listed here; `/vs-chatgpt` ships with
 *  full content, so it is indexable and listed here.
 *
 *  `/library` WAS excluded for the same placeholder reason and is now listed:
 *  `library_sectors.md` D1 replaced the `ComingSoonHub` with the real unified
 *  hub and dropped its `robots: noindex`. Its 38 sector pages and their
 *  sector×type lists live in the `sectors` section below, not here. */
export function getStaticUrls(): SitemapUrl[] {
  const paths = [
    "/",
    "/pricing",
    "/terms",
    "/privacy",
    "/audiences",
    "/about_us",
    "/for-lawyers",
    "/vs-chatgpt",
    "/learn",
    "/learn/how-it-works",
    "/learn/workspace",
    "/learn/data-protection",
    "/learn/usage-limits",
    "/blog",
    "/library",
  ];
  return paths.map((path) => ({
    loc: path === "/" ? `${SITE_URL}/` : `${SITE_URL}${path}`,
  }));
}

/**
 * Calculators — the `calculators` section, built LOCALLY from the code registry
 * (`lib/calculators/registry`). No backend feed: the calculator set IS the
 * source of truth. Lists the hub plus one URL per calculator (Arabic slugs
 * encoded once).
 */
export function getCalculatorUrls(): SitemapUrl[] {
  return [
    { loc: `${SITE_URL}/calculators` },
    ...CALCULATORS.map((calc) => ({
      loc: `${SITE_URL}/calculators/${encodeURIComponent(calc.slug)}`,
    })),
  ];
}

/**
 * The sector wing — the 38 `/library/{sector}` OVERVIEW pages, and nothing else
 * (`library_sectors.md` §12.6, amended 2026-08-11).
 *
 * Built LOCALLY from the `/sectors` counts endpoint rather than from a backend
 * sitemap feed, because that endpoint already carries everything the decision
 * needs: the slugs. A second feed would be a second copy of the indexability
 * rule, and the two would drift.
 *
 * ⚠ THE 109 `/library/{sector}/{type}` LISTS WERE REMOVED WHEN THE WING WENT
 * PAID-ONLY (2026-08-11). They are `noindex` now — `sectorTypeRobots()` returns
 * a directive for every type, unconditionally — and a sitemap that lists a URL
 * the page marks `noindex` is the self-contradiction Search Console reports as
 * "Submitted URL marked noindex". The old code filtered on that shared predicate
 * for exactly this reason; with the predicate now always truthy the filter can
 * only ever skip, so the loop is gone rather than left as a branch that cannot
 * be taken. RESTORING THEM IS A ONE-PLACE EDIT — un-gate `sectorTypeRobots` and
 * re-add the inner loop here; that function's comment carries the full rule.
 *
 * The overview pages stay: `/library/{sector}` is deliberately NOT gated, still
 * carries its ≤3-item strips, and is now the wing's only crawl entry point —
 * which is why the lists it links to keep `follow`.
 *
 * That yields 38 URLs, down from 147 (verified against the live corpus
 * 2026-08-01: 38 overviews + 109 lists).
 *
 * Fail-safe: `getSectors()` returns [] rather than throwing when the backend is
 * unreachable, so this degrades to an empty-but-valid <urlset>, never a 5xx.
 */
export async function getSectorUrls(): Promise<SitemapUrl[]> {
  const sectors = await getSectors();
  return sectors.map((sector) => ({
    loc: `${SITE_URL}${sectorPath(sector.slug)}`,
  }));
}

/**
 * The entity wing — the 28 `/compliance/{entity}` page-1 URLs, and nothing else.
 *
 * Built LOCALLY from the code registry (`lib/library/entities.ts`, the mirror of
 * `shared/library/entities.py`), the `getCalculatorUrls` pattern rather than the
 * `getSectorUrls` one: the vocabulary IS code on both sides, there is no backend
 * feed to add and nothing here can 5xx or return an empty urlset because a
 * service was restarting. `/compliance/entities` (the counts endpoint) decorates
 * the switcher; it is not, and must not become, the source of these URLs.
 *
 * ⚠ PAGE 1 ONLY — see the `SITEMAP_SECTIONS` note. Deep entity pages are
 * `noindex` when the anon depth cap walls them, and only 7 of the 28 entities
 * have a page 2 at all.
 *
 * All 28 ship, including the nine one-guide entities (D3): a thin page that
 * honestly lists the one guide an entity has is a real page, and the alternative
 * — a browse grid whose tile leads somewhere the sitemap denies — is worse.
 */
export function getComplianceEntityUrls(): SitemapUrl[] {
  return ENTITY_ORDER.map((slug) => ({
    // ASCII slugs: nothing to percent-encode, unlike the Arabic calculator and
    // document slugs a few lines up.
    loc: `${SITE_URL}${entityPath(slug)}`,
  }));
}

/** Shape of one page of a backend sitemap feed (shared by every section). */
interface SectionSitemapPage {
  urls: SitemapUrl[];
  page: number;
  total_pages: number;
}

/**
 * Fetch every page of a backend sitemap section feed and flatten to URLs.
 *
 * Contract (backend `GET /api/v1/public/library/sitemap/{section}?page=N`):
 *   { "urls": [{ "loc": "https://rayhanai.com/regulations/<slug>",
 *                "lastmod": "2026-07-01T00:00:00Z" }],
 *     "page": 1, "total_pages": 1 }
 *
 * Every fed section (`blog`, `regulations`, `articles`, …) shares this shape.
 * Fail-safe: any network / parse error returns whatever was gathered so far
 * (empty on the first page). Google must NEVER see a 5xx from a sitemap, so the
 * caller renders a valid (possibly empty) <urlset> regardless.
 */
export async function fetchSectionUrls(section: string): Promise<SitemapUrl[]> {
  const collected: SitemapUrl[] = [];
  // Hard ceiling so a bad `total_pages` can't loop forever.
  const MAX_PAGES = 1000;

  try {
    let page = 1;
    let totalPages = 1;

    do {
      const res = await fetch(
        `${SERVER_API_BASE}/api/v1/public/library/sitemap/${section}?page=${page}`,
        serverFetchInit(3600),
      );
      if (!res.ok) break;

      const data = (await res.json()) as SectionSitemapPage;
      if (Array.isArray(data.urls)) {
        for (const entry of data.urls) {
          if (entry && typeof entry.loc === "string") {
            collected.push({ loc: entry.loc, lastmod: entry.lastmod });
          }
        }
      }

      totalPages =
        Number.isFinite(data.total_pages) && data.total_pages > 0
          ? data.total_pages
          : 1;
      page += 1;
    } while (page <= totalPages && page <= MAX_PAGES);
  } catch {
    // Swallow — return whatever we managed to collect.
  }

  return collected;
}

/** Blog section feed — thin alias kept for the existing sitemap route case. */
export function fetchBlogUrls(): Promise<SitemapUrl[]> {
  return fetchSectionUrls("blog");
}
