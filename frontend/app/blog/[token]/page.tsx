import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PublicAnswerView } from "@/components/blog/PublicAnswerView";
import { BlogArticleView } from "@/components/blog/BlogArticleView";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildArticle } from "@/lib/seo/schema";
import { SERVER_API_BASE, serverFetchInit } from "@/lib/library/api";
import type { BlogPostPublic } from "@/types";

const SITE_URL = "https://rayhanai.com";

/** The public heading a post leads with, used for OG title + Article headline. */
function postHeadline(post: BlogPostPublic): string {
  return post.display_mode === "title"
    ? post.title || post.question_text || "ريحان"
    : post.question_text || post.title || "ريحان";
}

// This is a PUBLIC, anon-accessible route. It is a SERVER component that
// fetches the immutable snapshot from the backend with a plain ``fetch`` (no
// auth header) — NOT through the token-aware ``apiFetch`` client. The route is
// dynamic (``cache: "no-store"``) so the build never tries to pre-render it
// against a backend that may be offline.
//
// SERVER→SERVER (plan 3.2 / 3.4): the origin is ``SERVER_API_BASE``
// (``INTERNAL_API_URL`` → ``NEXT_PUBLIC_API_URL`` → localhost), so once the
// Railway private network is wired this call leaves the edge entirely — and
// therefore no longer picks up Cloudflare's ``X-Edge-Secret``. ``serverFetchInit``
// re-attaches it from the server-only ``EDGE_SECRET``. Without that the origin
// lock would 403 every share link and ``fetchPost`` would return ``null``, i.e.
// ``notFound()`` — a 404 served to Google on a live page.

export const dynamic = "force-dynamic";

interface PageParams {
  token: string;
}

// Next 15: route ``params`` is async and must be awaited.
interface PageProps {
  params: Promise<PageParams>;
}

async function fetchPost(token: string): Promise<BlogPostPublic | null> {
  try {
    // Only the HEADERS are taken from `serverFetchInit` — its `next.revalidate`
    // window is meaningless on a `force-dynamic` route, and mixing it with
    // `cache: "no-store"` would change this fetch's caching semantics. With
    // `EDGE_SECRET` unset the value is `undefined`, which `fetch` treats exactly
    // as an absent key, so the request on the wire is unchanged.
    const res = await fetch(
      `${SERVER_API_BASE}/api/v1/public/blog/${encodeURIComponent(token)}`,
      { cache: "no-store", headers: serverFetchInit(0).headers },
    );
    if (!res.ok) return null;
    return (await res.json()) as BlogPostPublic;
  } catch {
    // Backend unreachable / network error: treat as missing rather than
    // crashing the render. The page calls notFound() on a null result.
    return null;
  }
}

/** Truncate to a sensible OG title length without cutting mid-word too hard. */
function truncate(text: string, max = 70): string {
  const clean = text.trim().replace(/\s+/g, " ");
  if (clean.length <= max) return clean;
  return `${clean.slice(0, max - 1).trimEnd()}…`;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { token } = await params;
  const post = await fetchPost(token);

  if (!post) {
    // Graceful fallback — the page itself will 404, but metadata must still
    // resolve to a valid object so the route doesn't error.
    return {
      title: "ريحان",
      description: "المساعد القانوني الذكي للمحامين السعوديين",
    };
  }

  // Title-mode (مدونة) posts lead with their editorial title; question-mode
  // posts lead with the question text (the title is often empty there).
  const title = truncate(postHeadline(post));
  const description = "إجابة قانونية مُنشأة عبر ريحان — المساعد القانوني الذكي.";
  const canonical = `/blog/${token}`;
  const ogImage = `/og?title=${encodeURIComponent(title)}`;

  return {
    title,
    description,
    alternates: {
      canonical,
    },
    openGraph: {
      title,
      description,
      siteName: "ريحان",
      type: "article",
      locale: "ar_SA",
      url: canonical,
      images: [{ url: ogImage, width: 1200, height: 630, alt: title }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [ogImage],
    },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function BlogPostPage({ params }: PageProps) {
  const { token } = await params;
  const post = await fetchPost(token);

  if (!post) {
    notFound();
  }

  const headline = truncate(postHeadline(post));
  const articleSchema = buildArticle({
    title: headline,
    description: "إجابة قانونية مُنشأة عبر ريحان — المساعد القانوني الذكي.",
    url: `${SITE_URL}/blog/${token}`,
    datePublished: post.created_at,
    dateModified: post.created_at,
    image: `${SITE_URL}/og?title=${encodeURIComponent(headline)}`,
  });

  // Branch on the share template: ``title`` → editorial blog article;
  // everything else (``question``) → the default السؤال layout.
  return (
    <>
      <JsonLd data={articleSchema} />
      {post.display_mode === "title" ? (
        <BlogArticleView post={post} blogToken={token} />
      ) : (
        <PublicAnswerView post={post} blogToken={token} />
      )}
    </>
  );
}
