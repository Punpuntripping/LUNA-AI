import type { Metadata } from "next";
import { BlogCard } from "@/components/blog/BlogCard";
import { BlogPageShell } from "@/components/blog/BlogPageShell";
import { SubjectGrid } from "@/components/blog/SubjectGrid";
import { getBlogSubjects, getPublicBlogGallery } from "@/lib/blog/api";
import type { BlogSubject } from "@/types";

// The مدونة HUB — a PUBLIC, anon-accessible, SEO-indexable page: the capped
// subject grid over the wing's browse axis, then the recent articles.
// `.claude/plans/blog_subjects.md` §3 + D13.
//
// ⚠ IT READS `public_blogs`, NOT `blog_posts`. The gallery moved to the
// versioned public wing (plan D15/D16): cards are addressed by an Arabic
// `slug`, never a token, and every card carries the blog's `type` badge. The
// legacy share links keep working at `/blog/{token}` through the `[slug]`
// dispatcher next door; they are simply not listed anywhere.
//
// Server component: plain `fetch` through `lib/blog/api.ts` (no auth header,
// server→server origin, `X-Edge-Secret` re-attached). Both fetchers soft-fail
// to an empty list, so an unreachable backend renders an empty gallery instead
// of a 5xx — Google must never see an error from a public page.
//
// ⚠ STAYS `force-dynamic` WHILE `/blog/[slug]` MOVED TO ISR (2026-09-17), and
// the asymmetry is deliberate. This is a STATIC route, so a revalidate window
// would make Next prerender it during `npm run build` — and the Railway build
// runs while the backend may be unreachable, where `getPublicBlogGallery` soft-
// fails to `[]`. That bakes an EMPTY gallery into a 200 response, the exact
// trap `project_isr_bake_docker_cache_trap` records, and a same-commit rebuild
// cannot clear it. `/blog/[slug]` is immune because it is a dynamic segment
// with no `generateStaticParams`: nothing prerenders at build, each article is
// rendered on first request and cached from there.
//
// The cost of staying dynamic is one uncached render of one URL. The gallery is
// also the ONLY crawl path into the articles (they link to the library, never
// to each other), so serving it live means a newly approved article is
// reachable immediately rather than at the end of a window.

export const dynamic = "force-dynamic";

/**
 * Hub cap (plan §12.1). Twelve tiles, ranked by public-blog count and then by
 * the curator's `sort_rank`, with «كل المواضيع» leading to the full index.
 *
 * ⚠ THE `>= 1` FILTER IS A CONTRACT, NOT AN OPTIMIZATION (plan §7). The
 * vocabulary is seeded ahead of its content — most subjects will sit empty for
 * months — and a tile that promises articles and delivers an empty page is the
 * same broken promise a sitemap entry with an empty urlset makes.
 */
const HUB_SUBJECT_CAP = 12;

function rankSubjects(subjects: BlogSubject[]): BlogSubject[] {
  return subjects
    .filter((subject) => subject.blog_count >= 1)
    .sort(
      (a, b) => b.blog_count - a.blog_count || a.sort_rank - b.sort_rank,
    );
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
  const [blogs, subjects] = await Promise.all([
    getPublicBlogGallery(),
    getBlogSubjects(),
  ]);

  const ranked = rankSubjects(subjects);
  const topSubjects = ranked.slice(0, HUB_SUBJECT_CAP);

  return (
    <BlogPageShell showCta={false}>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
        <h1 className="mb-6 text-2xl font-bold tracking-tight text-foreground">
          المدونة
        </h1>

        {/* The browse axis, first: a flat reverse-chronological list of one-off
            answers is not a destination — subjects are the axis a reader can
            enter on (plan §0). Hidden entirely while every subject is empty,
            rather than rendering a grid of promises. */}
        {topSubjects.length > 0 && (
          <section className="mb-10">
            <h2 className="mb-3 text-sm font-bold text-foreground">
              المواضيع
            </h2>
            <SubjectGrid subjects={topSubjects} moreHref="/blog/subjects" />
          </section>
        )}

        <h2 className="mb-3 text-sm font-bold text-foreground">أحدث المقالات</h2>

        {blogs.length === 0 ? (
          <p className="py-16 text-center text-sm text-muted-foreground">
            لا توجد مقالات منشورة بعد.
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {blogs.map((blog) => (
              <BlogCard key={blog.slug} blog={blog} />
            ))}
          </div>
        )}
      </main>
    </BlogPageShell>
  );
}
