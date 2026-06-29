import type { MetadataRoute } from "next";

// Tells search crawlers what to index. Public marketing/legal pages are
// allowed; the authenticated app routes are disallowed (they redirect to
// /login for anonymous visitors anyway and have no SEO value).
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/chat", "/chats", "/cases", "/templates", "/login", "/auth"],
    },
    sitemap: "https://rayhanai.com/sitemap.xml",
    host: "https://rayhanai.com",
  };
}
