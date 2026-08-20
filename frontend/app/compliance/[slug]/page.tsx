import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { LibraryUseBeacon } from "@/components/library/mine/LibraryUseBeacon";
import {
  LibraryPageShell,
  TopicBreadcrumbs,
  TrustLine,
  TocList,
  TocRail,
  TocFloating,
  OfficialSources,
  AskRayhanWidget,
} from "@/components/library/blocks";
import { GuideBody } from "@/components/library/blocks/GuideBody";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildArticle } from "@/lib/seo/schema";
import { getComplianceGuide, toSnippet } from "@/lib/library/api";
import { guideDisplayTitle, guideTocHeadings } from "@/lib/library/guide";
import type {
  BreadcrumbItem,
  LibraryPageType,
  OfficialSourceLink,
  TocEntry,
} from "@/types/library";

// One service guide — «الدليل الشامل بالصور» for a government service.
//
// WHAT IS ON THIS PAGE: Rayhan's own authored rewrite of the issuing entity's
// official PDF user-guide, published IN FULL and UNGATED (no FullContentGate, no
// metered unlock, no truncation — an anonymous reader and Googlebot see the same
// complete body an authenticated one does). That is the whole point of the wing:
// it is the library's SEO surface for «كيف أنجز هذه الخدمة».
//
// WHAT IS NOT ON IT: the source PDF. The guide was built from the entity's
// official user-guide, and that URL is never surfaced anywhere in the UI — the
// payload does not even carry it. The single outbound link is `service_url`, the
// service's own page on the issuing entity's site, so a reader who wants to
// actually perform the service lands where the entity edits the truth.
//
// Server component, ISR via the fetch revalidate window in `lib/library/api.ts`
// (NO force-dynamic).

const SITE_URL = "https://rayhanai.com";

/**
 * Nothing downstream branches on this value: `isCarryablePageType` returns false
 * (the widget renders its generic «افتح محادثة مع ريحان» CTA), and the backend
 * takes `page_type` as a free string whose grounding lookup covers
 * regulation/article/judgment/blog only — exactly how a `circular` page behaves
 * today.
 */
const ASK_PAGE_TYPE: LibraryPageType = "compliance";

interface PageProps {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const doc = await getComplianceGuide(slug);

  if (!doc) {
    return {
      title: "ريحان",
      description: "المساعد القانوني الذكي للمحامين السعوديين",
    };
  }

  // «الدليل الشامل بالصور: …» — the same helper the H1, the cards and the
  // JSON-LD headline read, so the reader never sees two names for one guide.
  const display = guideDisplayTitle(doc.title, doc.image_count);
  const title = `${display} | ريحان`;
  const description =
    toSnippet(doc.summary) || `${display} — خطوات الخدمة عبر ريحان.`;
  const canonical = `/compliance/${encodeURIComponent(doc.slug)}`;
  const ogImage = `/og?title=${encodeURIComponent(display)}`;

  // NO `robots` KEY, and that is the point of the whole wing: these pages are
  // the indexable half of /compliance. The hub's deep pages still go `noindex`
  // when the anon depth cap walls them — a guide page has no wall to hide.
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
      images: [{ url: ogImage, width: 1200, height: 630, alt: display }],
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
export default async function ComplianceGuidePage({ params }: PageProps) {
  const { slug } = await params;
  const doc = await getComplianceGuide(slug);
  if (!doc) notFound();

  const display = guideDisplayTitle(doc.title, doc.image_count);

  // The last crumb stays the PLAIN corpus title: it already sits under a
  // «دليل الخدمات» crumb, so repeating «الدليل الشامل بالصور» there reads as a
  // stutter — and a crumb is a location, not a headline.
  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "دليل الخدمات", href: "/compliance" },
    { label: doc.title },
  ];

  // ONE link, and only ever this one. `source_pdf_url` is not in the payload by
  // design — see the header note.
  const officialSources: OfficialSourceLink[] = doc.service_url
    ? [
        {
          label: "صفحة الخدمة على موقع الجهة الرسمي",
          href: doc.service_url,
        },
      ]
    : [];

  // «محتويات الدليل» — built from the body's own `##` headings, over EXACTLY the
  // text `GuideBody` renders (hole lines gone, duplicated title/abstract
  // stripped). The ids come from `slugifyHeading` inside `MarkdownRenderer`'s
  // `headingAnchors` mode and the hrefs come from the same slugger, which is the
  // only reason these anchors resolve.
  //
  // ⚠ FEWER THAN 2 HEADINGS ⇒ NO TOC AT ALL, and the body takes the full width.
  // A one-row index is furniture: it costs a sticky column and tells the reader
  // nothing they cannot see.
  const tocEntries: TocEntry[] = guideTocHeadings(
    doc.guide_md,
    doc.title,
    doc.summary,
  ).map((heading) => ({
    id: heading.slug,
    label: heading.text,
    href: `#${heading.slug}`,
    level: heading.depth,
  }));
  const showToc = tocEntries.length >= 2;

  const now = new Date().toISOString();
  // Article, with NO paywall fragment: `buildPaywallFragment` describes content
  // withheld behind a gate, and there is none here. Claiming one on an open page
  // is a structured-data lie Google checks against the rendered body.
  // HowTo schema is deliberately out of scope for v1.
  const articleNode = buildArticle({
    title: display,
    description: toSnippet(doc.summary) || `${display} — خطوات الخدمة عبر ريحان.`,
    url: `${SITE_URL}/compliance/${encodeURIComponent(doc.slug)}`,
    datePublished: now,
    dateModified: now,
  });

  // `hub`, NOT `doc` — and the two-column grid below is the whole reason.
  // `doc` is max-w-3xl (768px), which is the right measure for a page whose body
  // is the ONLY column (circulars, forms and calculators all sit there
  // correctly). This page spends 17rem + gap-10 of its width on a sticky rail,
  // so under `doc` the guide itself rendered at ~456px — 40% narrower than the
  // same body on /regulations/{slug}, which reaches its full 768px because that
  // shell is `hub` (max-w-6xl) and the rail comes out of the SURPLUS. The rail
  // is a desktop affordance; it must not be paid for by the reading column.
  return (
    <LibraryPageShell maxWidth="hub">
      {/* Ungated by design ⇒ `gate="open"`: opening a guide shelves it in مكتبتي
          for a signed-in reader, and costs nothing. */}
      <LibraryUseBeacon contentType="compliance" slug={doc.slug} gate="open" />
      <JsonLd data={articleNode} />

      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2.5">
          <h1 className="text-2xl font-bold leading-tight text-foreground sm:text-3xl">
            {display}
          </h1>
          <TrustLine updatedAt={now} entity={doc.provider_name ?? undefined} />
        </header>

        {/* Two-column reading layout, the same shape /regulations/{slug} uses.
            The page is dir="rtl", so grid column 1 (the guide) starts on the
            RIGHT and the rail — column 2 — lands on the LEFT, sticky beside the
            scrolling body. Without a TOC there is no grid at all — the reading
            column is simply centred at its own width. */}
        <div
          className={
            showToc
              ? "lg:grid lg:grid-cols-[minmax(0,1fr)_17rem] lg:items-start lg:gap-10"
              : // No rail ⇒ no surplus to absorb: centre the reading column
                // rather than let it hug the RTL edge of a max-w-6xl main.
                "lg:mx-auto lg:max-w-3xl"
          }
        >
          <div className="min-w-0 space-y-6 lg:max-w-3xl">
            {/* Inside the column, exactly where /regulations/{slug} puts its
                LeadSummary. Above the grid it would set a 1152px measure. */}
            {doc.summary && (
              <p className="text-base leading-relaxed text-text-secondary">
                {doc.summary}
              </p>
            )}

            {showToc && (
              <div className="lg:hidden">
                {/* Collapsed on mobile for the reason the regulation wing
                    learned: an expanded index puts the whole TOC between the
                    reader and the first step. The floating pill takes over once
                    this has scrolled away. */}
                <TocList
                  entries={tocEntries}
                  title="محتويات الدليل"
                  defaultOpen={false}
                />
                <TocFloating entries={tocEntries} title="محتويات الدليل" />
              </div>
            )}

            {/* `dedupeHeading` gets the PLAIN corpus title, because that is what
                the body's own `# …` line says — the «بالصور» rewrite is a
                display concern and never reaches `guide_md`. `dedupeLead` kills
                the body's opening paragraph, which is the summary verbatim in
                168 of 169 guides and would otherwise print twice. */}
            <GuideBody
              guideMd={doc.guide_md}
              images={doc.images}
              dedupeHeading={doc.title}
              dedupeLead={doc.summary}
            />

            {officialSources.length > 0 && (
              <OfficialSources sources={officialSources} />
            )}
          </div>

          {showToc && (
            <aside className="hidden lg:sticky lg:top-24 lg:block">
              <TocRail entries={tocEntries} title="محتويات الدليل" />
            </aside>
          )}
        </div>
      </div>

      <AskRayhanWidget
        pageType={ASK_PAGE_TYPE}
        pageId={doc.slug}
        pageTitle={display}
      />
    </LibraryPageShell>
  );
}
