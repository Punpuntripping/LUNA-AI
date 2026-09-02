import type { Metadata } from "next";
import Link from "next/link";
import { BlogPageShell } from "@/components/blog/BlogPageShell";
import { SubjectGrid } from "@/components/blog/SubjectGrid";
import { getBlogSubjects } from "@/lib/blog/api";

// The FULL subject index — every موضوع carrying at least one public blog.
// `.claude/plans/blog_subjects.md` §3 + §7.
//
// ⚠ THIS IS A LITERAL STATIC SEGMENT, AND THAT IS THE WHOLE RESERVATION
// MECHANISM. Next matches `app/blog/subjects/` before `app/blog/[slug]/`
// unconditionally, so the dispatcher never receives «subjects» and needs no
// reserved-word branch to protect this page. `RESERVED_BLOG_SLUGS`
// (`lib/blog/slug.ts`) exists so the PUBLISH path refuses to mint a blog at
// this address — reserved slugs are refused by the dispatcher's writer, not
// discovered by its reader.
//
// ⚠ THE `>= 1` FILTER IS THE SAME CONTRACT THE HUB AND THE SITEMAP APPLY
// (plan §7, D13). The vocabulary is seeded ahead of its content, so most
// subjects sit empty for months; this index lists what a reader can actually
// read. The counts endpoint still returns the whole active vocabulary — a
// curator sees the zeroes, a reader never does.
//
// Sorted by blog count (plan §12.2): with `type` carried by the BLOG rather
// than the subject there is no grouping key left, so volume is the order.
//
// Server component; soft-fails to an empty index rather than a 5xx.

export const dynamic = "force-dynamic";

export function generateMetadata(): Metadata {
  const title = "مواضيع المدونة — ريحان";
  const description =
    "تصفّح مواضيع مدونة ريحان — مقالات وتحليلات قانونية سعودية مرتّبة حسب الموضوع.";
  return {
    title,
    description,
    alternates: {
      canonical: "/blog/subjects",
    },
    openGraph: {
      title,
      description,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: "/blog/subjects",
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
export default async function BlogSubjectsPage() {
  const subjects = (await getBlogSubjects())
    .filter((subject) => subject.blog_count >= 1)
    .sort((a, b) => b.blog_count - a.blog_count || a.sort_rank - b.sort_rank);

  return (
    <BlogPageShell showCta={false}>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
        <nav className="mb-4 text-xs text-muted-foreground">
          <Link href="/blog" className="transition-colors hover:text-primary">
            المدونة
          </Link>
        </nav>

        <h1 className="text-2xl font-bold tracking-tight text-foreground">
          المواضيع
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          كل موضوع يجمع مقالات المدونة المتعلقة به — اختر موضوعًا لتصفّح مقالاته.
        </p>

        {subjects.length === 0 ? (
          <p className="py-16 text-center text-sm text-muted-foreground">
            لا توجد مواضيع منشورة بعد.
          </p>
        ) : (
          <SubjectGrid subjects={subjects} className="mt-6" />
        )}
      </main>
    </BlogPageShell>
  );
}
