import {
  fetchBlogUrls,
  fetchSectionUrls,
  getCalculatorUrls,
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
//   sectors                  → built from the `/sectors` counts endpoint.
//   blog                     → backend feed `.../sitemap/blog?page=N`.
//   regulations / compliance → backend feed `.../sitemap/{section}?page=N`.
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
    case "sectors":
      // `/library/{sector}` + every INDEXABLE `/library/{sector}/{type}`, built
      // from the `/sectors` counts endpoint and filtered through the SAME
      // `sectorTypeRobots()` the pages use. Soft-fails to [] like the feeds.
      urls = await getSectorUrls();
      break;
    case "blog":
      // fetchBlogUrls never throws — returns [] on backend failure.
      urls = await fetchBlogUrls();
      break;
    case "regulations":
    case "compliance":
    case "articles":
    case "circulars":
    case "forms":
      // Same feed contract; fetchSectionUrls never throws. `articles` is the
      // ~50k per-مادة URL feed (backend joins seo_articles × regulation slugs);
      // `forms` lists approved+published rows only (empty until review).
      urls = await fetchSectionUrls(section);
      break;
    default:
      return new Response("Not Found", { status: 404 });
  }

  return new Response(renderUrlset(urls), { headers: XML_HEADERS });
}
