import { SITE_URL, SITEMAP_SECTIONS } from "@/lib/seo/sitemap";

// Sitemap INDEX. `robots.ts` points Google at `/sitemap.xml`, so this URL must
// stay `/sitemap.xml`. Rather than emit one giant file, we serve a
// <sitemapindex> that lists the per-section sitemaps at `/sitemaps/{section}`.
// Each section file (see `app/sitemaps/[section]/route.ts`) stays under the
// 50k-URL / 50MB Google limit; new sections (regulations, judgments, …) are
// added by appending to SITEMAP_SECTIONS — no change needed here.
//
// Fully static: no request input, no backend calls. Prerendered at build.

export const dynamic = "force-static";

export function GET(): Response {
  const entries = SITEMAP_SECTIONS.map(
    (section) =>
      `  <sitemap>\n    <loc>${SITE_URL}/sitemaps/${section}</loc>\n  </sitemap>`,
  ).join("\n");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${entries}
</sitemapindex>`;

  // `noindex` keeps the XML itself out of the index; NOT `nofollow` — the
  // whole point of the file is that crawlers follow the URLs it lists.
  return new Response(xml, {
    headers: {
      "Content-Type": "application/xml",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
      "X-Robots-Tag": "noindex",
    },
  });
}
