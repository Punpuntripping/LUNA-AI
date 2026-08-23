import {
  fetchBlogUrls,
  fetchSectionUrls,
  getCalculatorUrls,
  getComplianceEntityUrls,
  getSectorUrls,
  getStaticUrls,
  renderUrlset,
  type SitemapUrl,
} from "@/lib/seo/sitemap";

// Per-section <urlset>, served at `/sitemaps/{section}` and referenced by the
// sitemap index (`app/sitemap.xml`).
//
//   static                   → hardcoded marketing + legal pages (no backend).
//   calculators              → local code registry (no backend).
//   compliance-entities      → local code registry, the 28 «الجهة» page-1 URLs.
//   sectors                  → the 38 sector OVERVIEW pages, from `/sectors`.
//   blog                     → backend feed `.../sitemap/blog?page=N`.
//   regulations / articles   → backend feed `.../sitemap/{section}?page=N`.
//   judgments                → backend feed, `indexable` rows only (3k of 10k).
//   compliance               → backend feed, the service guides themselves.
//   other                    → 404.
//
// Fail-safe rule: if the backend is unreachable we return an EMPTY but VALID
// <urlset> with a 200 — Google must never see a 5xx / error from a sitemap.
// The `blog` fetch is cached with `next: { revalidate: 3600 }`; the whole
// response also carries an hour of CDN caching.

// `noindex` keeps the XML itself out of the index; NOT `nofollow` — the whole
// point of the file is that crawlers follow the URLs it lists.
const XML_HEADERS = {
  "Content-Type": "application/xml",
  "Cache-Control": "public, max-age=3600, s-maxage=3600",
  "X-Robots-Tag": "noindex",
} as const;

interface RouteContext {
  params: Promise<{ section: string }>;
}

export async function GET(
  _request: Request,
  { params }: RouteContext,
): Promise<Response> {
  const { section } = await params;

  let urls: SitemapUrl[];
  switch (section) {
    case "static":
      urls = getStaticUrls();
      break;
    case "calculators":
      // Local registry — no backend, never throws.
      urls = getCalculatorUrls();
      break;
    case "compliance-entities":
      // The 28 `/compliance/{entity}` SECTION pages, page 1 only. Local code
      // registry like `calculators` — the vocabulary is code on both sides, so
      // this never throws and never serves an empty urlset. It is listed while
      // `courts` is not because this axis is deliberately NOT a paid section
      // (compliance_entity_sections D1): an anonymous visitor gets the cards
      // here, not a wall, so there is no `noindex` for the sitemap to
      // contradict. See `getComplianceEntityUrls`.
      urls = getComplianceEntityUrls();
      break;
    // `courts` was a case here until 2026-08-11. A court section is paid-only
    // now, so its pages are `noindex` and there is nothing to list — the section
    // is gone from `SITEMAP_SECTIONS` too, and falls through to the 404 below
    // like any other unknown name. See that tuple's comment to restore it.
    case "sectors":
      // The 38 sector OVERVIEW pages. The 109 `/library/{sector}/{type}` lists
      // left with the paid-only gate (they are `noindex` — see `getSectorUrls`).
      // Soft-fails to [] like the feeds.
      urls = await getSectorUrls();
      break;
    case "blog":
      // fetchBlogUrls never throws — returns [] on backend failure.
      urls = await fetchBlogUrls();
      break;
    case "regulations":
    case "articles":
    case "circulars":
    case "forms":
    case "judgments":
    case "compliance":
      // Same feed contract; fetchSectionUrls never throws. `articles` is the
      // ~50k per-مادة URL feed (backend joins seo_articles × regulation slugs);
      // `forms` lists approved+published rows only (empty until review).
      // `judgments` lists ONLY the rows flagged `seo_item_meta.indexable` —
      // 3,000 PDPL-cleared rulings of the 10,000 published, the same flag
      // `app/judgments/[slug]` reads for its `robots` meta. Re-closing the wing
      // is a data change (`UPDATE seo_item_meta SET indexable = false …`), which
      // leaves this case serving a valid empty <urlset> rather than a 404.
      // `compliance` lists the service guides — ungated pages with no `robots`
      // key of their own, so nothing here can contradict what the page serves.
      urls = await fetchSectionUrls(section);
      break;
    default:
      return new Response("Not Found", { status: 404 });
  }

  return new Response(renderUrlset(urls), { headers: XML_HEADERS });
}
