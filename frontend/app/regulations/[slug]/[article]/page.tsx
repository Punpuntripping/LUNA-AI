import type { Metadata } from "next";
import { LibraryUseBeacon } from "@/components/library/mine/LibraryUseBeacon";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  AlertTriangle,
  BookText,
  ChevronLeft,
  ChevronRight,
  Sparkles,
} from "lucide-react";
import {
  LibraryPageShell,
  TopicBreadcrumbs,
  TrustLine,
  StatusBadge,
  ArticleBody,
  GateBanner,
  CalculatorBlock,
  AskRayhanWidget,
} from "@/components/library/blocks";
import { FullContentGate } from "@/components/library/FullContentGate";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildArticle, buildPaywallFragment } from "@/lib/seo/schema";
import { getRegulationArticle, toDocStatus, toSnippet } from "@/lib/library/api";
import { getCalculatorsForArticle } from "@/lib/calculators/registry";
import type { BreadcrumbItem, GateInfo } from "@/types/library";

const SITE_URL = "https://rayhanai.com";

interface PageProps {
  params: Promise<{ slug: string; article: string }>;
}

/** The canonical site-relative path for a مادة (Arabic slugs, encoded once). */
function articlePath(regSlug: string, articleSlug: string): string {
  return `/regulations/${encodeURIComponent(regSlug)}/${encodeURIComponent(
    articleSlug,
  )}`;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug, article } = await params;
  const doc = await getRegulationArticle(slug, article);

  if (!doc) {
    return {
      title: "ريحان",
      description: "المساعد القانوني الذكي للمحامين السعوديين",
    };
  }

  const heading = `${doc.article_label} من ${doc.regulation.title}`;
  const title = `${heading} — نصها وشرحها | ريحان`;
  const description =
    toSnippet(doc.text, 150) ||
    `${heading} — نصّها النظامي وشرحها والمواد المرتبطة عبر ريحان.`;
  const canonical = articlePath(doc.regulation.slug, doc.slug);
  const ogImage = `/og?title=${encodeURIComponent(heading)}`;

  return {
    title,
    description,
    alternates: { canonical },
    openGraph: {
      title,
      description,
      siteName: "ريحان",
      type: "article",
      locale: "ar_SA",
      url: canonical,
      images: [{ url: ogImage, width: 1200, height: 630, alt: heading }],
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
export default async function RegulationArticlePage({ params }: PageProps) {
  const { slug, article } = await params;
  const doc = await getRegulationArticle(slug, article);
  if (!doc) notFound();

  const status = toDocStatus(doc.regulation.status);
  const isDraft = doc.regulation.status === "draft";
  const heading = `${doc.article_label} من ${doc.regulation.title}`;

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "الأنظمة", href: "/regulations" },
    {
      label: doc.regulation.title,
      href: `/regulations/${encodeURIComponent(doc.regulation.slug)}`,
    },
    { label: doc.article_label },
  ];

  const gate: GateInfo | undefined = doc.is_truncated
    ? {
        isTruncated: true,
        hiddenPlaceholderLines: doc.hidden_placeholder_lines,
        ctaHref: "/login",
      }
    : undefined;

  // Bidirectional mesh: calculators whose legal basis cites this exact مادة.
  const calculators = getCalculatorsForArticle(
    doc.regulation.slug,
    doc.article_no,
  );

  // Inline «اسأل ريحان» trigger — same page-context contract the popup grounding
  // (ask_service `_ground_article`) resolves by article slug.
  const askParams = new URLSearchParams({
    intent: "ask_rayhan",
    page_type: "article",
    page_id: doc.slug,
    page_title: heading,
  });
  const askHref = `/login?${askParams.toString()}`;

  const canonicalUrl = `${SITE_URL}${articlePath(
    doc.regulation.slug,
    doc.slug,
  )}`;
  const now = new Date().toISOString();
  const articleNode = {
    ...buildArticle({
      title: `${heading} — نصها وشرحها`,
      description:
        toSnippet(doc.text, 150) || `${heading} — نصّها النظامي وشرحها.`,
      url: canonicalUrl,
      datePublished: now,
      dateModified: now,
    }),
    ...(doc.gate === "gated" ? buildPaywallFragment(".gated-body") : {}),
  };

  return (
    <LibraryPageShell maxWidth="doc">
      <LibraryUseBeacon contentType="article" slug={doc.slug} parentSlug={doc.regulation.slug} gate={doc.gate === "gated" || doc.is_truncated || Boolean(doc.sharh?.has_sharh) ? "gated" : "open"} />
      <JsonLd data={articleNode} />

      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header>
          <div className="flex items-start gap-4">
            {/* Large accent numeral — the reference-book article number. */}
            <span
              aria-hidden="true"
              className="hidden h-16 w-16 shrink-0 flex-col items-center justify-center rounded-2xl bg-accent-soft font-bold text-primary ring-1 ring-primary/15 sm:flex"
            >
              <span className="text-[10px] font-semibold tracking-wide text-primary/70">
                مادة
              </span>
              <span className="font-mono text-2xl leading-none tabular-nums">
                {doc.article_no}
              </span>
            </span>

            <div className="min-w-0 space-y-2">
              <Link
                href={`/regulations/${encodeURIComponent(doc.regulation.slug)}`}
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-primary underline-offset-4 transition-colors hover:underline"
              >
                <BookText aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
                {doc.regulation.title}
              </Link>
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-2xl font-bold leading-tight text-foreground sm:text-3xl">
                  {doc.article_label}
                </h1>
                {status && <StatusBadge status={status} />}
              </div>
              <TrustLine updatedAt={now} />
            </div>
          </div>
        </header>

        {isDraft && (
          <div
            dir="rtl"
            role="alert"
            className="flex items-start gap-3 rounded-xl border-2 border-warning bg-warning/20 px-4 py-3 text-sm font-semibold text-warning-foreground"
          >
            <AlertTriangle
              aria-hidden="true"
              className="mt-0.5 h-5 w-5 shrink-0"
            />
            <span>مشروع نظام — ليس نظاماً نافذاً بعد.</span>
          </div>
        )}

        {/* Fallback-body note: the text is the whole owning chunk, not the
            isolated مادة. */}
        {doc.is_fallback_body && doc.context_title && (
          <p
            dir="rtl"
            className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
          >
            هذا النص من «{doc.context_title}» — القسم الذي يضم هذه المادة.
          </p>
        )}

        {/* Body + شرح — anon gets the gate-truncated body and the شرح TEASER
            (first ~2 lines + gated bars); a signed-in reader's browser swaps in
            the full body + full شرح via the authed endpoint (FullContentGate
            kind="article" → { text, sharh_md }). */}
        <FullContentGate
          contentType="article"
          kind="article"
          fullKey={`${doc.regulation.slug}/${doc.slug}`}
          // A مادة is worth revealing when its body is truncated OR a شرح
          // exists behind the teaser. An open, untruncated مادة with no شرح is
          // already complete — no reveal action there.
          gated={
            doc.gate === "gated" ||
            doc.is_truncated ||
            Boolean(doc.sharh?.has_sharh)
          }
        >
          <div className="space-y-6">
            <section id="article-body" className="scroll-mt-24">
              <ArticleBody visibleText={doc.text} gate={gate} plain />
            </section>

            {doc.sharh?.has_sharh ? (
              /* Real cached شرح — teaser free, remainder gated (gate #3). */
              <section
                dir="rtl"
                className="space-y-4 rounded-2xl border border-primary/20 bg-gradient-to-b from-primary/5 to-card p-5 shadow-xs sm:p-6"
              >
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary ring-1 ring-primary/15">
                    <Sparkles aria-hidden="true" className="h-5 w-5" />
                  </span>
                  <div className="min-w-0">
                    <h2 className="text-base font-bold leading-tight text-foreground">
                      شرح المادة بالذكاء الاصطناعي
                    </h2>
                    <p className="text-[11px] text-muted-foreground">
                      شرح مبسّط موثّق من ريحان
                    </p>
                  </div>
                </div>
                <p className="text-[15px] leading-[1.9] text-foreground">
                  {doc.sharh.teaser}
                </p>
                <GateBanner
                  hiddenPlaceholderLines={Math.min(
                    Math.max(doc.sharh.hidden_placeholder_lines, 2),
                    8,
                  )}
                  ctaHref="/login"
                  ctaLabel="سجّل مجانًا لعرض الشرح كاملًا"
                />
              </section>
            ) : (
              /* No cached شرح for this مادة yet — the «قريباً» shell + an inline
                 اسأل ريحان trigger. */
              <section
                dir="rtl"
                className="rounded-2xl border border-dashed border-primary/30 bg-gradient-to-b from-primary/5 to-card p-5 shadow-xs sm:p-6"
              >
                <div className="flex items-center gap-2.5">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary ring-1 ring-primary/15">
                    <Sparkles aria-hidden="true" className="h-5 w-5" />
                  </span>
                  <div className="min-w-0">
                    <h2 className="text-base font-bold leading-tight text-foreground">
                      شرح المادة بالذكاء الاصطناعي
                    </h2>
                    <p className="text-[11px] font-semibold text-primary/80">
                      قريباً
                    </p>
                  </div>
                </div>
                <p className="mt-3 text-sm leading-relaxed text-text-secondary">
                  سيتوفّر شرح تفصيلي مبسّط لهذه المادة بالذكاء الاصطناعي قريباً.
                  حتى ذلك الحين، اسأل ريحان عنها الآن واحصل على إجابة موثّقة.
                </p>
                <Link
                  href={askHref}
                  className="mt-4 inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary-hover"
                >
                  <Sparkles aria-hidden="true" className="h-4 w-4 shrink-0" />
                  اسأل ريحان عن هذه المادة
                </Link>
              </section>
            )}
          </div>
        </FullContentGate>

        {/* Embedded calculators (bidirectional mesh). */}
        {calculators.map((calc) => (
          <CalculatorBlock key={calc.slug} slug={calc.slug} />
        ))}

        {/* Prev / next مادة — RTL: السابقة on the right, التالية on the left. */}
        {(doc.prev || doc.next) && (
          <nav
            dir="rtl"
            aria-label="التنقّل بين المواد"
            className="grid grid-cols-1 items-stretch gap-3 border-t border-border pt-6 sm:grid-cols-2"
          >
            {doc.prev ? (
              <Link
                href={articlePath(doc.regulation.slug, doc.prev.slug)}
                className="group flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3.5 shadow-xs transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-2 text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
                  <ChevronRight aria-hidden="true" className="h-4 w-4" />
                </span>
                <span className="min-w-0">
                  <span className="block text-[11px] font-medium text-muted-foreground">
                    المادة السابقة
                  </span>
                  <span className="block truncate text-sm font-bold text-foreground group-hover:text-primary">
                    {doc.prev.article_label}
                  </span>
                </span>
              </Link>
            ) : (
              <span className="hidden sm:block" />
            )}

            {doc.next ? (
              <Link
                href={articlePath(doc.regulation.slug, doc.next.slug)}
                className="group flex items-center justify-end gap-3 rounded-xl border border-border bg-card px-4 py-3.5 text-left shadow-xs transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
              >
                <span className="min-w-0">
                  <span className="block text-[11px] font-medium text-muted-foreground">
                    المادة التالية
                  </span>
                  <span className="block truncate text-sm font-bold text-foreground group-hover:text-primary">
                    {doc.next.article_label}
                  </span>
                </span>
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-2 text-muted-foreground transition-colors group-hover:bg-primary/10 group-hover:text-primary">
                  <ChevronLeft aria-hidden="true" className="h-4 w-4" />
                </span>
              </Link>
            ) : (
              <span className="hidden sm:block" />
            )}
          </nav>
        )}
      </div>

      <AskRayhanWidget
        pageType="article"
        pageId={`${doc.regulation.slug}/${doc.slug}`}
        pageTitle={heading}
      />
    </LibraryPageShell>
  );
}
