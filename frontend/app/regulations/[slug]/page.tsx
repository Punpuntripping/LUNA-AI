import type { Metadata } from "next";
import { LibraryUseBeacon } from "@/components/library/mine/LibraryUseBeacon";
import { notFound } from "next/navigation";
import { AlertTriangle } from "lucide-react";
import {
  LibraryPageShell,
  TopicBreadcrumbs,
  TrustLine,
  MetadataCard,
  StatusBadge,
  LeadSummary,
  TocList,
  TocRail,
  ArticleBody,
  GateBanner,
  OfficialSources,
  AskRayhanWidget,
} from "@/components/library/blocks";
import { FullContentGate } from "@/components/library/FullContentGate";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildLegislation, buildPaywallFragment } from "@/lib/seo/schema";
import {
  getRegulationDoc,
  toDocStatus,
  toSnippet,
  findMetadataValue,
} from "@/lib/library/api";
import type {
  BreadcrumbItem,
  MetadataItem,
  TocEntry,
  OfficialSourceLink,
  GateInfo,
} from "@/types/library";

const SITE_URL = "https://rayhanai.com";

// (dedupe + single-CTA gate wiring — iter2 SEO doc-page polish)

interface PageProps {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const doc = await getRegulationDoc(slug);

  if (!doc) {
    return {
      title: "ريحان",
      description: "المساعد القانوني الذكي للمحامين السعوديين",
    };
  }

  const title = `${doc.title} — ملخصه ومواده | ريحان`;
  const description =
    toSnippet(doc.summary_md) ||
    `${doc.title} — الملخص والمواد والمصادر الرسمية عبر ريحان.`;
  const canonical = `/regulations/${encodeURIComponent(doc.slug)}`;
  const ogImage = `/og?title=${encodeURIComponent(doc.title)}`;

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
      images: [{ url: ogImage, width: 1200, height: 630, alt: doc.title }],
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
export default async function RegulationDocPage({ params }: PageProps) {
  const { slug } = await params;
  const doc = await getRegulationDoc(slug);
  if (!doc) notFound();

  const status = toDocStatus(doc.status);
  const entity = findMetadataValue(doc.metadata, "الجهة");

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "الأنظمة", href: "/regulations" },
    { label: doc.title },
  ];

  const metadataItems: MetadataItem[] = doc.metadata.map((row) => ({
    label: row.label,
    value: row.value,
  }));

  // ── Phase 3 TOC upgrade ──────────────────────────────────────────────────
  // PREFERRED: once the seo_articles index is populated for this regulation,
  // the TOC becomes a real link map into the مادة pages — every entry a live
  // /regulations/{slug}/{article-slug} link, and NEVER locked (article pages are
  // the free ranking layer; TOC links that lead to them carry no gate).
  //
  // FALLBACK (index empty): the original chunk-based TOC — visible chunks anchor
  // to their rendered section (#sec-{id}); the rest render as locked, non-link
  // rows whose text lives behind the signup gate.
  // `?? []` guards against a transitional/older doc payload that predates the
  // additive `article_index` field (e.g. a stale ISR/fetch-cache entry during a
  // rollout) — such a payload simply falls back to the chunk-based TOC below.
  const articleIndex = doc.article_index ?? [];
  const publishedSlugs = new Set(articleIndex.map((a) => a.slug));
  // Articles-first payloads render their sections as `art-{no}` ids; chunk-only
  // regulations keep chunk uuids. Detect which shape this doc uses.
  const articlesFirst = doc.visible_sections.some((s) =>
    s.id.startsWith("art-"),
  );
  // EVERY TOC row is clickable — the rail's whole job is jumping into any مادة:
  //   * a مادة with its own PUBLISHED page → link out to it;
  //   * anything else → same-page anchor to its section. The section exists for
  //     signed-in readers (FullContentGate swaps in the full document); for anon
  //     visitors the anchor target is absent and TocRail's click handler lands
  //     them on the signup gate (#library-doc-gate) instead.
  const tocEntries: TocEntry[] = articlesFirst
    ? [...doc.toc]
        .sort((a, b) => a.position - b.position)
        .map((entry) =>
          publishedSlugs.has(entry.id)
            ? {
                id: entry.id,
                label: entry.title,
                href: `/regulations/${doc.slug}/${entry.id}`,
              }
            : {
                id: entry.id,
                label: entry.title,
                href: `#sec-art-${entry.position}`,
              },
        )
    : [...doc.toc]
        .sort((a, b) => a.position - b.position)
        .map((entry) => ({
          id: entry.id,
          label: entry.title,
          href: `#sec-${entry.id}`,
        }));

  // Count pill for the TOC header: مادة when the live article index drives it,
  // otherwise the chunk-fallback section count.
  const tocBadge =
    articleIndex.length > 0
      ? `${articleIndex.length} مادة`
      : tocEntries.length > 0
        ? `${tocEntries.length} قسمًا`
        : undefined;

  const officialSources: OfficialSourceLink[] = doc.official_sources.map(
    (source) => ({ label: source.title, href: source.href }),
  );

  // Legislation node; the paywall fragment (targets the server-truncated
  // `.gated-body` regions) is merged in ONLY when the document is gated.
  const legislation = {
    ...buildLegislation({
      name: doc.title,
      url: `${SITE_URL}/regulations/${encodeURIComponent(doc.slug)}`,
    }),
    ...(doc.gate === "gated" ? buildPaywallFragment(".gated-body") : {}),
  };

  return (
    <LibraryPageShell maxWidth="hub">
      <LibraryUseBeacon contentType="regulation" slug={doc.slug} gate={doc.gate === "gated" || doc.hidden_section_count > 0 || doc.visible_sections.some((s) => s.is_truncated) ? "gated" : "open"} />
      <JsonLd data={legislation} />

      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold leading-tight text-foreground sm:text-3xl">
              {doc.title}
            </h1>
            {status && <StatusBadge status={status} />}
          </div>
          <TrustLine updatedAt={new Date().toISOString()} entity={entity} />
        </header>

        {doc.draft_notice && (
          <div
            dir="rtl"
            role="alert"
            className="flex items-start gap-3 rounded-xl border-2 border-warning bg-warning/20 px-4 py-3 text-sm font-semibold text-warning-foreground"
          >
            <AlertTriangle
              aria-hidden="true"
              className="mt-0.5 h-5 w-5 shrink-0"
            />
            <span>
              {doc.status_raw === "under_consultation"
                ? "مشروع نظام تحت الاستطلاع — ليس نظاماً نافذاً بعد."
                : "مشروع نظام — انتهت فترة الاستطلاع، وليس نظاماً نافذاً."}
            </span>
          </div>
        )}

        {/* Two-column reading layout on desktop. The page is dir="rtl", so grid
            column 1 (the content) starts on the RIGHT and the TOC rail — column
            2 — lands on the LEFT, sticky beside the scrolling text. On mobile
            the rail is hidden and the TOC renders inline before the sections. */}
        <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_17rem] lg:items-start lg:gap-10">
          <div className="min-w-0 space-y-6 lg:max-w-3xl">
            <MetadataCard items={metadataItems} />

            {doc.summary_md && <LeadSummary text={doc.summary_md} />}

            {tocEntries.length > 0 && (
              <div className="lg:hidden">
                {/* Collapsed on mobile: an expanded مواد index put the whole
                    TOC between the reader and the first article. */}
                <TocList
                  entries={tocEntries}
                  badge={tocBadge}
                  defaultOpen={false}
                />
              </div>
            )}

            {/* Sections — anon gets the first few (gate-truncated) + a signup
                gate; a signed-in reader's browser swaps in the full section
                list via the authed endpoint. Additive: any failure keeps the
                anon render. */}
            <FullContentGate
              contentType="regulation"
              kind="sections"
              fullKey={doc.slug}
              // Is anything actually behind the gate? If not, no reveal action
              // is offered — an unlock must never be spendable on a document
              // that already renders in full.
              gated={
                doc.gate === "gated" ||
                doc.hidden_section_count > 0 ||
                doc.visible_sections.some((section) => section.is_truncated)
              }
              hiddenSections={doc.hidden_section_count}
            >
              {doc.visible_sections.length > 0 && (
                <div className="space-y-8">
                  {doc.visible_sections.map((section) => {
                    const gate: GateInfo | undefined = section.is_truncated
                      ? {
                          isTruncated: true,
                          hiddenPlaceholderLines:
                            section.hidden_placeholder_lines,
                          ctaHref: "/login",
                        }
                      : undefined;
                    return (
                      <section
                        key={section.id}
                        id={`sec-${section.id}`}
                        className="scroll-mt-24 space-y-3.5"
                      >
                        <h2 className="border-s-[3px] border-primary/50 ps-3 text-lg font-bold leading-snug text-foreground">
                          {/* A merged fallback run covers several مواد but renders
                              once. Their TOC rows still target `#sec-art-{n}`, so
                              each swallowed مادة gets an empty inline anchor —
                              nested INSIDE the heading so it takes no space and
                              never trips the parent's `space-y` rhythm. */}
                          {section.also_ids?.map((id) => (
                            <span
                              key={id}
                              id={`sec-${id}`}
                              aria-hidden="true"
                              className="scroll-mt-24"
                            />
                          ))}
                          {section.title}
                        </h2>
                        <ArticleBody
                          visibleText={section.text}
                          gate={gate}
                          plain
                          dedupeHeading={section.title}
                          // When the document has a trailing hidden-section CTA
                          // card, every per-section gate renders bars-only so
                          // that single card is the one conversion surface (no
                          // stacked back-to-back cards at the truncation tail).
                          gateBarsOnly={doc.hidden_section_count > 0}
                        />
                      </section>
                    );
                  })}
                </div>
              )}

              {doc.hidden_section_count > 0 && (
                /* id = the TocRail click-fallback target: an anon click on a
                   مادة whose section isn't rendered lands here (the gate). */
                <div id="library-doc-gate" className="scroll-mt-24">
                  <GateBanner
                    hiddenPlaceholderLines={Math.min(doc.hidden_section_count, 6)}
                    ctaHref="/login"
                    ctaLabel={`${doc.hidden_section_count} قسمًا إضافيًا بانتظارك — سجّل مجانًا لعرض النظام كاملًا`}
                  />
                </div>
              )}
            </FullContentGate>

            {officialSources.length > 0 && (
              <OfficialSources sources={officialSources} />
            )}
          </div>

          {tocEntries.length > 0 && (
            <aside
              aria-label="محتويات النظام"
              className="hidden lg:sticky lg:top-24 lg:block"
            >
              <TocRail entries={tocEntries} badge={tocBadge} />
            </aside>
          )}
        </div>
      </div>

      <AskRayhanWidget
        pageType="regulation"
        pageId={doc.slug}
        pageTitle={doc.title}
      />
    </LibraryPageShell>
  );
}
