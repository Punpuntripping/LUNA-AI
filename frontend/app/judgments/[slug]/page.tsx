import type { Metadata } from "next";
import { LibraryUseBeacon } from "@/components/library/mine/LibraryUseBeacon";
import { notFound } from "next/navigation";
import { Gavel } from "lucide-react";
import {
  LibraryPageShell,
  TopicBreadcrumbs,
  TrustLine,
  MetadataCard,
  CourtLevelBadge,
  JudgmentSummaryButton,
  JudgmentSummaryPanel,
  LeadSummary,
  TocList,
  TocRail,
  ArticleBody,
  GateBanner,
  CitedRegulations,
  OfficialSources,
  AskRayhanWidget,
} from "@/components/library/blocks";
import { FullContentGate } from "@/components/library/FullContentGate";
import { LibraryRevealProvider } from "@/components/library/LibraryReveal";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildArticle, buildPaywallFragment } from "@/lib/seo/schema";
import { getJudgmentDoc, toSnippet } from "@/lib/library/api";
import type {
  BreadcrumbItem,
  TopicChip,
  MetadataItem,
  TocEntry,
  OfficialSourceLink,
  GateInfo,
} from "@/types/library";

const SITE_URL = "https://rayhanai.com";

// ⚠ PDPL GATE — NOW PER-RULING, NOT PER-WING (2026-08-11).
//
// This page used to be unconditionally `noindex, nofollow`, because judgment
// text may still name a party and a crawl cannot be taken back. That gate is
// still here; it just moved from "every judgment" to "every judgment that has
// not been cleared", carried by `doc.indexable` (`seo_item_meta.indexable`,
// migration 130). 3,000 of the 10,000 published rulings are cleared: chosen by
// `scripts/build_judgment_slugs.py --indexable` from a pool that EXCLUDES every
// ruling whose text still carries an identity marker.
//
// ⚠ THE SITEMAP READS THE SAME FLAG. `/sitemaps/judgments` lists exactly the
// rows where `indexable` is true, and this file marks exactly those rows
// indexable. Do not re-derive either side from anything else — a sitemap that
// lists a URL its page marks `noindex` is the "Submitted URL marked noindex"
// error in Search Console, and that is what happens the moment these two rules
// stop being the same rule.
//
// UNKNOWN → NOINDEX. A missing doc (404 metadata) and a payload from a backend
// too old to send the field both fall here, and both must read as "not
// cleared". The gate fails closed or it is not a gate.
const NOINDEX_PDPL = { index: false, follow: false } as const;

// `follow: true` on a cleared ruling — its citation list is the internal-linking
// mesh into /regulations, and that is the reason this wing exists for SEO.
// `nofollow` would index the page while discarding the links that make it worth
// indexing.
const INDEXABLE = { index: true, follow: true } as const;

interface PageProps {
  params: Promise<{ slug: string }>;
}

/**
 * ISO-8601 timestamp for a `YYYY-MM-DD…` Gregorian date, else null. Guarded by
 * an explicit shape test rather than handing anything to `new Date()`: a Hijri
 * string like `1445/03/12` PARSES as a (nonsense) Gregorian date, so a bare
 * `new Date(raw)` would silently publish the year 1445 as `datePublished`.
 */
function toIsoTimestamp(raw: string | null): string | null {
  if (!raw || !/^\d{4}-\d{2}-\d{2}/.test(raw)) return null;
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const doc = await getJudgmentDoc(slug);

  if (!doc) {
    return {
      title: "ريحان",
      description: "المساعد القانوني الذكي للمحامين السعوديين",
      robots: NOINDEX_PDPL,
    };
  }

  // `title` is the composed listing title (subject + court + year); `subject`
  // alone is the H1.
  const title = `${doc.title} | ريحان`;
  const description =
    (doc.summary_md ? toSnippet(doc.summary_md) : "") ||
    `${doc.subject} — حكم صادر عن ${doc.court}: الوقائع والأسباب والمنطوق والأنظمة المستند إليها عبر ريحان.`;
  const canonical = `/judgments/${encodeURIComponent(doc.slug)}`;
  const ogImage = `/og?title=${encodeURIComponent(doc.subject)}`;

  return {
    title,
    description,
    alternates: { canonical },
    robots: doc.indexable ? INDEXABLE : NOINDEX_PDPL,
    openGraph: {
      title,
      description,
      siteName: "ريحان",
      type: "article",
      locale: "ar_SA",
      url: canonical,
      images: [{ url: ogImage, width: 1200, height: 630, alt: doc.subject }],
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
export default async function JudgmentDocPage({ params }: PageProps) {
  const { slug } = await params;
  const doc = await getJudgmentDoc(slug);
  if (!doc) notFound();

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "الأحكام القضائية", href: "/judgments" },
    { label: doc.subject },
  ];

  // Domain chips double as mesh links back into the filtered hub — the only
  // place a reader can pivot from "this ruling" to "every ruling like it".
  const domainChips: TopicChip[] = doc.domains.map((domain) => ({
    label: domain,
    href: `/judgments?domain=${encodeURIComponent(domain)}`,
  }));

  const metadataItems: MetadataItem[] = doc.metadata.map((row) => ({
    label: row.label,
    value: row.value,
  }));

  // TOC — derived from the RENDERED sections (a judgment payload has no
  // separate `toc` field the way a regulation does). Entirely hidden sections
  // aren't in `sections` at all, so they get one trailing row that lands on the
  // signup gate: an anon reader still SEES that more of the ruling exists.
  const tocEntries: TocEntry[] = doc.sections.map((section) => ({
    id: section.id,
    label: section.title,
    href: `#sec-${section.id}`,
  }));
  if (doc.hidden_section_count > 0) {
    tocEntries.push({
      id: "__gated",
      label: `${doc.hidden_section_count} قسمًا محجوبًا`,
      href: "#library-doc-gate",
    });
  }
  const tocBadge =
    doc.sections.length > 0 ? `${doc.sections.length} قسمًا` : undefined;

  const officialSources: OfficialSourceLink[] = doc.official_sources.map(
    (source) => ({ label: source.title, href: source.href }),
  );

  const now = new Date().toISOString();
  // The trust line's date is the judgment's OWN Gregorian date when the payload
  // carries a parseable one — a stable content date beats a "today" that churns
  // on every ISR revalidation. Falls back to now when the date is absent or is
  // in a non-ISO shape.
  const judgmentIso = toIsoTimestamp(doc.date_gregorian);

  // Article — NOT Legislation: a court ruling is not legislation, and
  // schema.org has no court-decision type the existing helpers model. Article
  // carries headline/description/datePublished/publisher cleanly and is what
  // the sibling /circulars doc page already emits. The paywall fragment
  // (targeting the server-truncated `.gated-body` regions) merges in only when
  // the document is actually gated.
  const articleNode = {
    ...buildArticle({
      title: doc.title,
      description:
        (doc.summary_md ? toSnippet(doc.summary_md) : "") ||
        `${doc.subject} — حكم صادر عن ${doc.court}.`,
      url: `${SITE_URL}/judgments/${encodeURIComponent(doc.slug)}`,
      datePublished: judgmentIso ?? now,
      dateModified: now,
    }),
    ...(doc.gate_effective === "gated"
      ? buildPaywallFragment(".gated-body")
      : {}),
  };

  // Is any of the RULING behind the gate. Read by the use beacon, the shared
  // reveal and the body gate — deriving it three times is how the three drift.
  // NOT the same question as «is there anything to unlock on this page»: «ملخص
  // ريحان» is gated on every ruling that has one, including a short ruling whose
  // text ships whole.
  const bodyGated =
    doc.gate_effective === "gated" ||
    doc.hidden_section_count > 0 ||
    doc.sections.some((section) => section.is_truncated);

  return (
    <LibraryPageShell maxWidth="hub">
      <LibraryUseBeacon
        contentType="judgment"
        slug={doc.slug}
        gate={bodyGated ? "gated" : "open"}
      />
      <JsonLd data={articleNode} />

      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} chips={domainChips} />

        <header className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold leading-tight text-foreground sm:text-3xl">
              {doc.subject}
            </h1>
            <CourtLevelBadge
              level={doc.court_level}
              label={doc.court_level_label}
            />
            {doc.appeal_result && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2.5 py-0.5 text-xs font-medium text-text-secondary">
                <Gavel aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
                {doc.appeal_result}
              </span>
            )}
          </div>
          <TrustLine
            updatedAt={judgmentIso ?? now}
            entity={doc.court || undefined}
          />
        </header>

        {/* Two-column reading layout on desktop — identical to the regulation
            doc page. The page is dir="rtl", so grid column 1 (the content)
            starts on the RIGHT and the TOC rail — column 2 — lands on the LEFT,
            sticky beside the scrolling text. On mobile the rail is hidden and
            the TOC renders inline before the sections. */}
        <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_17rem] lg:items-start lg:gap-10">
          {/* ONE unlock for this ruling. The provider renders no DOM — it only
              hands the «ملخص ريحان» button in the metadata card and the body's
              `FullContentGate` the SAME reveal state, because one
              `/library/full/judgment/{slug}` response carries both the summary
              and the full sections. Without it the two surfaces would each open
              (and each charge for) their own copy of one ruling. */}
          <LibraryRevealProvider
            contentType="judgment"
            fullKey={doc.slug}
            gated={bodyGated || doc.has_summary}
          >
            <div className="min-w-0 space-y-6 lg:max-w-3xl">
              {metadataItems.length > 0 && (
                <MetadataCard
                  items={metadataItems}
                  footer={
                    <JudgmentSummaryButton
                      hasSummary={doc.has_summary}
                      // A ruling that ships whole renders no body gate, so this
                      // button is then the page's ONLY metered action and has to
                      // carry the allowance chip itself.
                      showBalance={!bodyGated}
                    />
                  }
                />
              )}

              {/* The revealed «ملخص ريحان» — renders nothing until the button
                  above (or the body CTA below) spends the unlock. */}
              <JudgmentSummaryPanel />

              {/* ALWAYS FREE — the AI-written lead is the ranking layer and is
                  never gated, whatever `gate_effective` says about the body. */}
              {doc.summary_md && <LeadSummary text={doc.summary_md} />}

              {tocEntries.length > 0 && (
                <div className="lg:hidden">
                  <TocList
                    entries={tocEntries}
                    title="محتويات الحكم"
                    badge={tocBadge}
                    defaultOpen={false}
                  />
                </div>
              )}

              {/* The ruling itself. Anon readers get the free sections (الوقائع،
                  المنطوق، منطوق حكم الاستئناف) plus gate-truncated previews of
                  the rest; a signed-in reader's browser swaps in the full section
                  list via the authed endpoint — `kind="sections"` consumes the
                  exact same `{ sections: [...] }` envelope regulations use.
                  Additive: any failure leaves the anon render untouched. */}
              <FullContentGate
                contentType="judgment"
                kind="sections"
                fullKey={doc.slug}
                // No reveal action on a ruling that already renders in full — an
                // unlock must never be spendable on nothing. This is the BODY's
                // question only: a ruling that ships whole still offers «ملخص
                // ريحان» in the card above, which is gated on its own.
                gated={bodyGated}
                hiddenSections={doc.hidden_section_count}
              >
                {doc.sections.length > 0 && (
                  <div className="space-y-8">
                    {doc.sections.map((section) => {
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
                            {section.title}
                          </h2>
                          <ArticleBody
                            visibleText={section.text}
                            gate={gate}
                            plain
                            dedupeHeading={section.title}
                            // When the document also has a trailing hidden-section
                            // CTA card, every per-section gate renders bars-only
                            // so that single card is the ONE conversion surface
                            // (no stacked cards at the truncation tail).
                            gateBarsOnly={doc.hidden_section_count > 0}
                          />
                        </section>
                      );
                    })}
                  </div>
                )}

                {doc.hidden_section_count > 0 && (
                  /* id = the TocRail click-fallback target: an anon click on a
                     section that isn't rendered lands here (the gate). */
                  <div id="library-doc-gate" className="scroll-mt-24">
                    <GateBanner
                      hiddenPlaceholderLines={Math.min(
                        doc.hidden_section_count,
                        6,
                      )}
                      ctaHref="/login"
                      ctaLabel={`${doc.hidden_section_count} قسمًا إضافيًا من الحكم بانتظارك — سجّل مجانًا لعرضه كاملًا`}
                    />
                  </div>
                )}
              </FullContentGate>

              {/* The citation mesh — every ruling is an inbound link into the
                  /regulations corpus, and the reason this wing exists for SEO. */}
              <CitedRegulations
                items={doc.cited_regulations}
                total={doc.cited_total}
              />

              {officialSources.length > 0 && (
                <OfficialSources sources={officialSources} />
              )}
            </div>
          </LibraryRevealProvider>

          {tocEntries.length > 0 && (
            <aside
              aria-label="محتويات الحكم"
              className="hidden lg:sticky lg:top-24 lg:block"
            >
              {/* TocRail owns its own internal scroll (an explicit max-h on the
                  <ul>, NOT an h-full/flex-1 chain — that silently fails to
                  constrain in Chrome). Used as-is; do not re-wrap it in a height
                  container. A judgment has <=11 sections so the rail is short,
                  which is fine. */}
              <TocRail
                entries={tocEntries}
                title="محتويات الحكم"
                badge={tocBadge}
              />
            </aside>
          )}
        </div>
      </div>

      <AskRayhanWidget
        pageType="judgment"
        pageId={doc.slug}
        pageTitle={doc.subject}
      />
    </LibraryPageShell>
  );
}
