import type { MetadataRoute } from "next";

// SEO / backlink / archive crawlers blocked outright: they walk the entire
// public library to resell URL inventories and send no traffic back. Search
// and AI-answer crawlers are deliberately NOT on this list — being indexed and
// being cited are both discovery channels. Mirrored at the edge by WAF rule 0
// (see .claude/plans/cloudflare_navigation_hardening.md), because the polite
// ones obey robots.txt and the rest do not.
const BLOCKED_CRAWLERS = [
  "AhrefsBot",
  "SemrushBot",
  "SiteAuditBot",
  "DotBot",
  "rogerbot",
  "MJ12bot",
  "BLEXBot",
  "DataForSeoBot",
  "Barkrowler",
  "CCBot",
];

// Tells search crawlers what to index. Public marketing/legal pages are
// allowed; the authenticated app routes are disallowed (they redirect to
// /login for anonymous visitors anyway and have no SEO value).
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/chat", "/chats", "/cases", "/templates", "/login", "/auth"],
      },
      // One group per agent rather than a single multi-User-Agent block, so a
      // crawler with a sloppy parser cannot miss its own directive.
      ...BLOCKED_CRAWLERS.map((userAgent) => ({ userAgent, disallow: "/" })),
    ],
    sitemap: "https://rayhanai.com/sitemap.xml",
    host: "https://rayhanai.com",
  };
}
