import type { Metadata } from "next";
import Link from "next/link";
import { Eye } from "lucide-react";
import { BlogPageShell } from "@/components/blog/BlogPageShell";
import { SERVER_API_BASE, serverFetchInit } from "@/lib/library/api";
import type { BlogCardPublic, PublicBlogsResponse } from "@/types";

// This is a PUBLIC, anon-accessible route — the SEO-indexable مدونة gallery.
// It is a SERVER component that fetches the ``is_public`` listing from the
// backend with a plain ``fetch`` (no auth header), NOT through the token-aware
// ``apiFetch`` client. The route is dynamic (``cache: "no-store"``) so the
// build never tries to pre-render it against a backend that may be offline.
//
// SERVER→SERVER (plan 3.2 / 3.4): the origin is ``SERVER_API_BASE``
// (``INTERNAL_API_URL`` → ``NEXT_PUBLIC_API_URL`` → localhost), so once the
// Railway private network is wired this call leaves the edge entirely — and
// therefore no longer picks up Cloudflare's ``X-Edge-Secret``. ``serverFetchInit``
// re-attaches it from the server-only ``EDGE_SECRET``; without that the origin
// lock would 403 the whole gallery into an empty state.

export const dynamic = "force-dynamic";

// Subtype → Arabic chip label. Copied from PublicAnswerView.tsx so the gallery
// speaks the same vocabulary as the public article surfaces.
const SUBTYPE_LABEL: Record<string, string> = {
  report: "تقرير",
  contract: "عقد",
  memo: "مذكرة",
  summary: "ملخص",
  memory_file: "ذاكرة",
  legal_opinion: "رأي قانوني",
  legal_synthesis: "تحليل قانوني",
};

// Arabic long-form Gregorian date (e.g. «١٢ يونيو ٢٠٢٦»). Module-level so the
// Intl formatter is built once, not per card render.
const DATE_FORMATTER = new Intl.DateTimeFormat("ar-EG", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return DATE_FORMATTER.format(date);
}

async function fetchGallery(): Promise<BlogCardPublic[]> {
  try {
    // Only the HEADERS are taken from `serverFetchInit` — its `next.revalidate`
    // window is meaningless on a `force-dynamic` route, and mixing it with
    // `cache: "no-store"` would change this fetch's caching semantics. With
    // `EDGE_SECRET` unset the value is `undefined`, which `fetch` treats exactly
    // as an absent key, so the request on the wire is unchanged.
    const res = await fetch(`${SERVER_API_BASE}/api/v1/public/blogs`, {
      cache: "no-store",
      headers: serverFetchInit(0).headers,
    });
    if (!res.ok) return [];
    const data = (await res.json()) as PublicBlogsResponse;
    return data.posts ?? [];
  } catch {
    // Backend unreachable / network error: render an empty gallery rather
    // than crashing the render.
    return [];
  }
}

export function generateMetadata(): Metadata {
  const title = "المدونة — ريحان";
  const description =
    "مقالات وتحليلات قانونية مُنشأة عبر ريحان — المساعد القانوني الذكي للمحامين السعوديين.";
  return {
    title,
    description,
    alternates: {
      canonical: "/blog",
    },
    openGraph: {
      title,
      description,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: "/blog",
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
export default async function BlogGalleryPage() {
  const posts = await fetchGallery();

  return (
    <BlogPageShell showCta={false}>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
        <h1 className="mb-6 text-2xl font-bold tracking-tight text-foreground">
          المدونة
        </h1>

        {posts.length === 0 ? (
          <p className="py-16 text-center text-sm text-muted-foreground">
            لا توجد مقالات منشورة بعد.
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {posts.map((post) => (
              <BlogCard key={post.token} post={post} />
            ))}
          </div>
        )}
      </main>
    </BlogPageShell>
  );
}

function BlogCard({ post }: { post: BlogCardPublic }) {
  const subtypeLabel = post.subtype
    ? SUBTYPE_LABEL[post.subtype] ?? post.subtype
    : null;
  const title = (post.title ?? "").trim() || "بدون عنوان";
  const date = formatDate(post.created_at);

  return (
    <Link
      href={`/blog/${post.token}`}
      className="flex flex-col rounded-xl border bg-card p-4 shadow-sm transition hover:border-primary/30 hover:shadow-md"
    >
      {subtypeLabel && (
        <span className="mb-2 inline-flex w-fit items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
          {subtypeLabel}
        </span>
      )}

      <h2 className="line-clamp-2 text-base font-bold leading-snug text-foreground">
        {title}
      </h2>

      {post.snippet && (
        <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-muted-foreground">
          {post.snippet}
        </p>
      )}

      <div className="mt-4 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{date}</span>
        <span className="inline-flex items-center gap-1">
          <Eye className="h-3.5 w-3.5" />
          {post.view_count}
        </span>
      </div>
    </Link>
  );
}
