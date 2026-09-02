import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PublicAnswerView } from "@/components/blog/PublicAnswerView";
import { BlogArticleView } from "@/components/blog/BlogArticleView";
import { BlogCard } from "@/components/blog/BlogCard";
import { BlogPageShell } from "@/components/blog/BlogPageShell";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildArticle } from "@/lib/seo/schema";
import { toSnippet } from "@/lib/library/api";
import { formatCount } from "@/lib/library/sectors";
import {
  getBlogSubjectFeed,
  getLegacyBlogPost,
  getPublicBlog,
} from "@/lib/blog/api";
import {
  RESERVED_BLOG_SLUGS,
  blogCanonicalPath,
  isBlogSlugShape,
  isLegacyTokenShape,
  isSubjectSlugShape,
  normalizeBlogRef,
} from "@/lib/blog/slug";
import type {
  BlogPostPublic,
  BlogSubjectFeedResponse,
  PublicBlogDetail,
} from "@/types";

const SITE_URL = "https://rayhanai.com";

// ONE DYNAMIC SEGMENT, THREE VOCABULARIES — the dispatcher of
// `.claude/plans/blog_subjects.md` §3. Next cannot host two dynamic segments at
// one level, so `/blog/[slug]` resolves everything under `/blog`:
//
//   1. `blog_subjects.slug`  (ASCII kebab)  → the subject listing page
//   2. `public_blogs.slug`   (Arabic)       → the blog, its own canonical
//   3. `blog_posts.token`    (32 hex)       → a LEGACY share snapshot
//   4.                                      → notFound()
//
// `app/blog/subjects/` is a LITERAL static segment and always wins over
// `[slug]`, so the full subject index needs no reserved-word logic here — the
// `RESERVED_BLOG_SLUGS` refusal below exists so the reader agrees with the
// publish path's mint-time refusal, not because the segment can reach us.
//
// ⚠ 99 LEGACY SHARE LINKS RIDE THIS ROUTE AND MUST NEVER BREAK (plan D7). They
// were sent over WhatsApp and Telegram; there is no way to reissue them. The
// route file was RENAMED from `[token]` to `[slug]` — the URL space is
// unchanged, `/blog/<32-hex>` still lands here, and case 3 still serves it from
// `blog_posts`. NO REDIRECTS: a 301 to a canonical address would rewrite the
// address bar of a link already in someone's hand, and a `blog_posts` row has
// no slug to redirect TO.
//
// ⚠ ARABIC SLUGS ARRIVE PERCENT-ENCODED. `normalizeBlogRef` is the single
// decode (`lib/blog/slug.ts`); everything below reads its output, and
// `lib/blog/api.ts` performs the single re-encode for the wire.
//
// Server component. `force-dynamic` (not ISR) is inherited from the route this
// replaced: the legacy read bumps `view_count` server-side on every fetch, and
// a revalidation window would silently stop counting reads of the 99 links.

export const dynamic = "force-dynamic";

// Next 15: route `params` is async and must be awaited.
interface PageProps {
  params: Promise<{ slug: string }>;
}

/** What a `/blog/{ref}` reference resolved to, or `null` for a 404. */
type Resolved =
  | { kind: "subject"; feed: BlogSubjectFeedResponse }
  | { kind: "blog"; blog: PublicBlogDetail }
  | { kind: "legacy"; post: BlogPostPublic; token: string }
  | null;

/**
 * Resolve one `/blog/{ref}` reference against the three vocabularies.
 *
 * ⚠ IT IS A FALL-THROUGH CHAIN, NOT AN EXCLUSIVE BRANCH. Every step that misses
 * hands the ref to the next one, so the shape tests below can only ever change
 * how many round trips a URL costs — never which page it lands on.
 *
 * The shape tests are DB guarantees, not conventions (plan §2): migration 154
 * CHECKs a subject slug into ASCII kebab-case and migration 153 CHECKs a blog
 * slug out of it, so a lookup this skips could not have matched. Legacy tokens
 * are exactly 32 lowercase hex — the same shape `blog_service._BARE_TOKEN_RE`
 * mints and matches.
 *
 * ORDER — subjects win (plan D6), with one deliberate deferral: a 32-hex token
 * is *also* ASCII-kebab-shaped, so a strict reading would spend a subject
 * lookup on all 99 live share links before reaching the one table that can
 * answer them. Token-shaped refs therefore try `blog_posts` FIRST and fall back
 * to the subject lookup at the end, which keeps the precedence intact for the
 * (pathological, never-minted) case of a 32-hex subject slug.
 *
 * Each fetcher is `cache()`d per request, so `generateMetadata` and the page
 * body share one round trip.
 */
async function resolveBlogRef(raw: string): Promise<Resolved> {
  const ref = normalizeBlogRef(raw);
  if (!ref) return null;

  // A reserved segment is none of the three and must never be asked about —
  // the `/compliance/{slug}` lesson, where falling through spent a round trip
  // on a live endpoint and rendered a 500 instead of a 404.
  if (RESERVED_BLOG_SLUGS.has(ref)) return null;

  const tokenShaped = isLegacyTokenShape(ref);

  if (isSubjectSlugShape(ref) && !tokenShaped) {
    const feed = await getBlogSubjectFeed(ref);
    if (feed) return { kind: "subject", feed };
  }

  if (isBlogSlugShape(ref)) {
    const blog = await getPublicBlog(ref);
    if (blog) return { kind: "blog", blog };
  }

  if (tokenShaped) {
    const post = await getLegacyBlogPost(ref);
    if (post) return { kind: "legacy", post, token: ref };
    // Deferred from the top: nothing forbids a 32-hex subject slug, so the
    // subject vocabulary still gets its turn before this ref 404s.
    const feed = await getBlogSubjectFeed(ref);
    if (feed) return { kind: "subject", feed };
  }

  return null;
}

/** Truncate to a sensible OG title length without cutting mid-word too hard. */
function truncate(text: string, max = 70): string {
  const clean = text.trim().replace(/\s+/g, " ");
  if (clean.length <= max) return clean;
  return `${clean.slice(0, max - 1).trimEnd()}…`;
}

/** The public heading a legacy post leads with — OG title + Article headline. */
function postHeadline(post: BlogPostPublic): string {
  return post.display_mode === "title"
    ? post.title || post.question_text || "ريحان"
    : post.question_text || post.title || "ريحان";
}

const GENERIC_DESCRIPTION =
  "إجابة قانونية مُنشأة عبر ريحان — المساعد القانوني الذكي.";

const FALLBACK_METADATA: Metadata = {
  title: "ريحان",
  description: "المساعد القانوني الذكي للمحامين السعوديين",
};

/**
 * Meta description for a public blog: the body's opening prose, with the `[n]`
 * citation markers stripped first. `toSnippet` removes markdown syntax and
 * caps the length, but it does not know about citations — and «… المادة 74 [3]»
 * in a search result reads as a typo.
 */
function blogDescription(contentMd: string): string {
  const withoutCitations = contentMd.replace(
    /\[\s*\d+(?:\s*,\s*\d+)*\s*\]/g,
    "",
  );
  return toSnippet(withoutCitations) || GENERIC_DESCRIPTION;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const resolved = await resolveBlogRef(slug);

  // Graceful fallback — the page itself will 404, but metadata must still
  // resolve to a valid object so the route doesn't error.
  if (!resolved) return FALLBACK_METADATA;

  if (resolved.kind === "subject") {
    const { subject } = resolved.feed;
    const title = `${subject.label_ar} — مدونة ريحان`;
    const description =
      subject.description_ar?.trim() ||
      `مقالات وتحليلات قانونية عن ${subject.label_ar} — من مدونة ريحان.`;
    // ASCII slug: `blogCanonicalPath`'s encode is a no-op here, and it is used
    // anyway so every canonical on this route is built one way.
    const canonical = blogCanonicalPath(subject.slug);
    // NO `robots` KEY: a subject listing is an ordinary indexable page, like
    // `/compliance/{entity}`. An EMPTY subject is kept out of the sitemap and
    // off every internal grid by the `>= 1` filter (plan §7) rather than by a
    // directive here.
    return {
      title,
      description,
      alternates: { canonical },
      openGraph: {
        title,
        description,
        siteName: "ريحان",
        type: "website",
        locale: "ar_SA",
        url: canonical,
      },
      twitter: { card: "summary", title, description },
    };
  }

  if (resolved.kind === "blog") {
    const { blog } = resolved;
    const title = truncate(blog.title);
    const description = blogDescription(blog.content_md);
    const canonical = blogCanonicalPath(blog.slug);
    const ogImage = `/og?title=${encodeURIComponent(title)}`;
    return {
      title,
      description,
      alternates: { canonical },
      // ⚠ THE `is_public` DIRECTIVE (plan §7). A retracted blog keeps serving a
      // live 200 — retraction delists, it does not delete — and a live 200 does
      // not deindex anything by itself. `index: false, follow: true` is what
      // takes it out of search while its direct link keeps working.
      robots: blog.is_public ? undefined : { index: false, follow: true },
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

  const { post, token } = resolved;
  // Title-mode (مدونة) posts lead with their editorial title; question-mode
  // posts lead with the question text (the title is often empty there).
  const title = truncate(postHeadline(post));
  // ⚠ THE TOKEN URL *IS* THE CANONICAL. A `blog_posts` row has no slug to point
  // at, and there is no redirect layer — the address in people's hands is the
  // address. (A legacy post that is ever backfilled into `public_blogs` would
  // gain one; that is a content decision, plan §12.4.)
  const canonical = `/blog/${token}`;
  const ogImage = `/og?title=${encodeURIComponent(title)}`;
  return {
    title,
    description: GENERIC_DESCRIPTION,
    alternates: { canonical },
    // ⚠ RETROACTIVELY `noindex`, and deliberately so (plan §7). These 99 links
    // were minted UNLISTED — the unguessable token is what grants access — and
    // were never meant to be indexed. `follow` stays on so the links they carry
    // still pass equity.
    robots: { index: false, follow: true },
    openGraph: {
      title,
      description: GENERIC_DESCRIPTION,
      siteName: "ريحان",
      type: "article",
      locale: "ar_SA",
      url: canonical,
      images: [{ url: ogImage, width: 1200, height: 630, alt: title }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description: GENERIC_DESCRIPTION,
      images: [ogImage],
    },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function BlogSlugPage({ params }: PageProps) {
  const { slug } = await params;
  const resolved = await resolveBlogRef(slug);

  if (!resolved) notFound();

  // ── 1. A SUBJECT LISTING ──────────────────────────────────────────────────
  if (resolved.kind === "subject") {
    const { subject, blogs } = resolved.feed;
    return (
      <BlogPageShell showCta={false}>
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
          <nav className="mb-4 text-xs text-muted-foreground">
            <Link href="/blog" className="transition-colors hover:text-primary">
              المدونة
            </Link>
            <span className="mx-1.5">/</span>
            <Link
              href="/blog/subjects"
              className="transition-colors hover:text-primary"
            >
              المواضيع
            </Link>
          </nav>

          <header className="mb-6">
            <h1 className="text-2xl font-bold tracking-tight text-foreground">
              {subject.label_ar}
            </h1>
            {subject.description_ar && (
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
                {subject.description_ar}
              </p>
            )}
            {/* The FULL qualifying count, not this page's length — Latin
                digits, because a count is app chrome. */}
            <p className="mt-2 text-xs tabular-nums text-muted-foreground">
              {formatCount(subject.blog_count)} مقالة
            </p>
          </header>

          {blogs.length === 0 ? (
            <p className="py-16 text-center text-sm text-muted-foreground">
              لا توجد مقالات في هذا الموضوع بعد.
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

  // ── 2. A PUBLIC BLOG (public_blogs, the current version) ──────────────────
  if (resolved.kind === "blog") {
    const { blog } = resolved;
    const headline = truncate(blog.title);
    const articleSchema = buildArticle({
      title: headline,
      description: blogDescription(blog.content_md),
      url: `${SITE_URL}${blogCanonicalPath(blog.slug)}`,
      // Real content dates, both of them. `updated_at` moves when an SEO
      // rewrite appends a version, which is a genuine freshness signal — never
      // a render-time stamp.
      datePublished: blog.created_at,
      dateModified: blog.updated_at ?? blog.created_at,
      image: `${SITE_URL}/og?title=${encodeURIComponent(headline)}`,
    });

    return (
      <>
        <JsonLd data={articleSchema} />
        {/* The SLUG is the reveal key on this wing — there is no token to pass
            (plan D17). See `BlogArticleView`'s `sourceKey` docs. */}
        <BlogArticleView post={blog} sourceKey={blog.slug} />
      </>
    );
  }

  // ── 3. A LEGACY blog_posts SHARE SNAPSHOT ─────────────────────────────────
  const { post, token } = resolved;
  const headline = truncate(postHeadline(post));
  const articleSchema = buildArticle({
    title: headline,
    description: GENERIC_DESCRIPTION,
    url: `${SITE_URL}/blog/${token}`,
    datePublished: post.created_at,
    dateModified: post.created_at,
    image: `${SITE_URL}/og?title=${encodeURIComponent(headline)}`,
  });

  // Branch on the share template: `title` → editorial blog article;
  // everything else (`question`) → the default السؤال layout. Unchanged from
  // the route this replaced — the 99 links render exactly what they did.
  return (
    <>
      <JsonLd data={articleSchema} />
      {post.display_mode === "title" ? (
        <BlogArticleView post={post} sourceKey={token} />
      ) : (
        <PublicAnswerView post={post} blogToken={token} />
      )}
    </>
  );
}
