import type { Metadata } from "next";
import { LibraryUseBeacon } from "@/components/library/mine/LibraryUseBeacon";
import { notFound } from "next/navigation";
import { AlertTriangle, CalendarClock, FileDown, Scale } from "lucide-react";
import {
  LibraryPageShell,
  TopicBreadcrumbs,
  TrustLine,
  ArticleBody,
  OpenInRayhanCta,
  AskRayhanWidget,
} from "@/components/library/blocks";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { FullContentGate } from "@/components/library/FullContentGate";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildArticle, buildPaywallFragment } from "@/lib/seo/schema";
import { getFormDetail, toSnippet } from "@/lib/library/api";
import type { BreadcrumbItem, GateInfo } from "@/types/library";

const SITE_URL = "https://rayhanai.com";

interface PageProps {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const detail = await getFormDetail(slug);

  if (!detail) {
    return {
      title: "ريحان",
      description: "المساعد القانوني الذكي للمحامين السعوديين",
    };
  }

  const title = `${detail.title} — نموذج جاهز | ريحان`;
  const description =
    toSnippet(detail.use_case_md || detail.intro_md || "") ||
    `${detail.title} — نموذج قانوني جاهز: متى تستخدمه وأساسه النظامي عبر ريحان.`;
  const canonical = `/forms/${encodeURIComponent(detail.slug)}`;
  const ogImage = `/og?title=${encodeURIComponent(detail.title)}`;

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
      images: [{ url: ogImage, width: 1200, height: 630, alt: detail.title }],
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
export default async function FormDetailPage({ params }: PageProps) {
  const { slug } = await params;
  const detail = await getFormDetail(slug);
  if (!detail) notFound();

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "النماذج", href: "/forms" },
    { label: detail.title },
  ];

  const gate: GateInfo | undefined = detail.body_preview.is_truncated
    ? {
        isTruncated: true,
        hiddenPlaceholderLines: detail.body_preview.hidden_placeholder_lines,
      }
    : undefined;

  const now = new Date().toISOString();
  const articleNode = {
    ...buildArticle({
      title: `${detail.title} — نموذج جاهز`,
      description:
        toSnippet(detail.use_case_md || detail.intro_md || "") ||
        `${detail.title} — نموذج قانوني جاهز عبر ريحان.`,
      url: `${SITE_URL}/forms/${encodeURIComponent(detail.slug)}`,
      datePublished: now,
      dateModified: now,
    }),
    // The template body is the gated part — mark the paywall when truncated.
    ...(detail.body_preview.is_truncated
      ? buildPaywallFragment(".gated-body")
      : {}),
  };

  return (
    <LibraryPageShell maxWidth="doc">
      <LibraryUseBeacon contentType="form" slug={detail.slug} gate={detail.body_preview.is_truncated ? "gated" : "open"} />
      <JsonLd data={articleNode} />

      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold leading-tight text-foreground sm:text-3xl">
              {detail.title}
            </h1>
            {detail.category && (
              <span className="inline-flex items-center rounded-full bg-pill px-2.5 py-1 text-xs font-medium text-pill-fg">
                {detail.category}
              </span>
            )}
          </div>
          <TrustLine updatedAt={now} />
        </header>

        {/* Prominent liability disclaimer — «راجع مختصاً» on every form page. */}
        <div
          dir="rtl"
          role="note"
          className="flex items-start gap-3 rounded-xl border-2 border-warning bg-warning/15 px-4 py-3 text-sm font-semibold text-warning-foreground"
        >
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />
          <span>
            نموذج استرشادي — راجع مختصاً قبل الاعتماد عليه. لا يُغني هذا النموذج
            عن الاستشارة القانونية.
          </span>
        </div>

        {/* متى تستخدم هذا النموذج — free SEO layer. */}
        {detail.use_case_md && (
          <section className="space-y-3">
            <h2 className="flex items-center gap-2 text-lg font-bold text-foreground">
              <CalendarClock aria-hidden="true" className="h-5 w-5 shrink-0 text-primary" />
              متى تستخدم هذا النموذج
            </h2>
            <div className="text-sm leading-relaxed text-foreground">
              <MarkdownRenderer content={detail.use_case_md} />
            </div>
          </section>
        )}

        {/* شرح — free. */}
        {detail.intro_md && (
          <section className="text-sm leading-relaxed text-foreground">
            <MarkdownRenderer content={detail.intro_md} />
          </section>
        )}

        {/* Template body preview → gate. A signed-in reader's browser swaps in
            the full template body via the authed endpoint. */}
        <section className="space-y-3">
          <h2 className="text-lg font-bold text-foreground">صيغة النموذج</h2>
          <FullContentGate
            contentType="form"
            kind="body_md"
            fullKey={detail.slug}
            gated={detail.body_preview.is_truncated}
          >
            <ArticleBody visibleText={detail.body_preview.text} gate={gate} />
          </FullContentGate>
        </section>

        {/* الأساس النظامي — display labels only (no links in v1). */}
        {detail.legal_basis.length > 0 && (
          <section dir="rtl" className="space-y-3">
            <h2 className="flex items-center gap-2 text-sm font-bold text-foreground">
              <Scale aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
              الأساس النظامي
            </h2>
            <ul className="flex flex-wrap gap-2">
              {detail.legal_basis.map((entry, index) => (
                <li
                  key={`${entry.label}-${index}`}
                  className="rounded-full bg-pill px-3 py-1 text-xs font-medium text-pill-fg"
                >
                  {entry.label}
                </li>
              ))}
            </ul>
          </section>
        )}

        {detail.has_docx && (
          <p
            dir="rtl"
            className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
          >
            <FileDown aria-hidden="true" className="h-4 w-4 shrink-0" />
            يتوفّر تنزيل النموذج بصيغة Word بعد فتحه في ريحان.
          </p>
        )}

        <OpenInRayhanCta slug={detail.slug} title={detail.title} />
      </div>

      <AskRayhanWidget
        pageType="form"
        pageId={detail.slug}
        pageTitle={detail.title}
      />
    </LibraryPageShell>
  );
}
