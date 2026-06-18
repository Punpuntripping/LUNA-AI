import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PublicAnswerView } from "@/components/blog/PublicAnswerView";
import type { BlogPostPublic } from "@/types";

// This is a PUBLIC, anon-accessible route. It is a SERVER component that
// fetches the immutable snapshot from the backend with a plain ``fetch`` (no
// auth header) — NOT through the token-aware ``apiFetch`` client. The route is
// dynamic (``cache: "no-store"``) so the build never tries to pre-render it
// against a backend that may be offline.

export const dynamic = "force-dynamic";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface PageParams {
  token: string;
}

// Next 15: route ``params`` is async and must be awaited.
interface PageProps {
  params: Promise<PageParams>;
}

async function fetchPost(token: string): Promise<BlogPostPublic | null> {
  try {
    const res = await fetch(
      `${API_BASE}/api/v1/public/blog/${encodeURIComponent(token)}`,
      { cache: "no-store" },
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

  const title = truncate(post.question_text || post.title || "ريحان");
  const description = "إجابة قانونية مُنشأة عبر ريحان — المساعد القانوني الذكي.";

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      siteName: "ريحان",
      type: "article",
      locale: "ar_SA",
    },
    twitter: {
      card: "summary",
      title,
      description,
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

  return <PublicAnswerView post={post} />;
}
