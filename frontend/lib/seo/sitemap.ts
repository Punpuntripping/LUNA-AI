// Shared helpers for the sitemap index (`app/sitemap.xml`) and the per-section
// URL sets (`app/sitemaps/[section]`). Keeping the section list + XML helpers in
// one place means adding a future wing (regulations, judgments, …) is a
// one-line edit here plus a case in the section route.

import { CALCULATORS } from "@/lib/calculators/registry";
import { getSectors } from "@/lib/library/api";
import {
  LIBRARY_TYPES,
  sectorPath,
  sectorTypePath,
  sectorTypeRobots,
} from "@/lib/library/sectors";
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
 * adds `regulations` + `compliance`. Phase 3 adds `calculators`, which is served
 * LOCALLY from the code registry (no backend feed). Every backend-fed section
 * uses the same `{ urls, page, total_pages }` feed contract. Later phases append
 * `judgments`, `circulars`, `articles`, …
 *
 * TODO(pdpl): `judgments` is DELIBERATELY absent. The /judgments wing ships
 * `robots: { index: false, follow: false }` on every page until the PDPL
 * anonymization audit confirms no party-identifying details survive in judgment
 * text — listing it here would invite exactly the crawl those pages must not
 * get yet. Add "judgments" to this tuple + a case in `app/sitemaps/[section]`
 * at the same time the `robots` keys come out of the three page files under
 * `app/judgments/`. Do not do one without the other.
 */
export const SITEMAP_SECTIONS = [
  "static",
  "blog",
  "regulations",
  "compliance",
  "articles",
  "circulars",
  "forms",
  "calculators",
  "sectors",
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

/** Hardcoded marketing + legal pages — the `static` section. `/learn` is
 *  intentionally EXCLUDED while it is a placeholder hub (it carries
 *  `robots: noindex` until real content lands); `/vs-chatgpt` ships with full
 *  content, so it is indexable and listed here.
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
    "/vs-chatgpt",
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
 * The sector wing — `/library/{sector}` plus every INDEXABLE
 * `/library/{sector}/{type}` list (`library_sectors.md` §12.6).
 *
 * Built LOCALLY from the `/sectors` counts endpoint rather than from a backend
 * sitemap feed, because that endpoint already carries everything the decision
 * needs: the slugs, and the 152 per-type counts. A second feed would be a second
 * copy of the indexability rule, and the two would drift.
 *
 * WHICH URLS QUALIFY — the filter is `sectorTypeRobots()`, THE SAME PREDICATE
 * THE PAGES THEMSELVES USE for their `<meta name="robots">`. That shared call is
 * the point: a sitemap that lists a URL the page marks `noindex` is a
 * self-contradiction Search Console reports as "Submitted URL marked noindex",
 * and it is the exact drift that happens when the rule gets restated here.
 * `capped: false` is correct — only page 1 of each list is ever listed, and page
 * 1 is inside every tier's depth cap.
 *
 * That currently yields 147 URLs (verified against the live corpus 2026-08-01):
 * 38 overviews + 109 sector×type lists. The plan's "~142" estimate predates two
 * exclusions it did not account for — the 38 أحكام combinations held back by the
 * PDPL gate, and 4 more under the D9 thin-page threshold.
 *
 * Fail-safe: `getSectors()` returns [] rather than throwing when the backend is
 * unreachable, so this degrades to an empty-but-valid <urlset>, never a 5xx.
 */
export async function getSectorUrls(): Promise<SitemapUrl[]> {
  const sectors = await getSectors();
  const urls: SitemapUrl[] = [];

  for (const sector of sectors) {
    urls.push({ loc: `${SITE_URL}${sectorPath(sector.slug)}` });
    for (const type of LIBRARY_TYPES) {
      const count = sector.counts[type] ?? 0;
      if (count === 0) continue; // renders no tab, 404s on a direct hit (D9)
      if (sectorTypeRobots(type, count, false)) continue; // noindex — do not list
      urls.push({ loc: `${SITE_URL}${sectorTypePath(sector.slug, type)}` });
    }
  }

  return urls;
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
 * Every fed section (`blog`, `regulations`, `compliance`, …) shares this shape.
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
