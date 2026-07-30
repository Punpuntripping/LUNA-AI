import type { Metadata } from "next";
import { LibraryUseBeacon } from "@/components/library/mine/LibraryUseBeacon";
import { notFound } from "next/navigation";
import { CheckCircle2, FileText } from "lucide-react";
import {
  LibraryPageShell,
  TopicBreadcrumbs,
  TrustLine,
  MetadataCard,
  LeadSummary,
  TocList,
  MediaBlock,
  OfficialSources,
  AskRayhanWidget,
} from "@/components/library/blocks";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildHowTo, buildVideoObject } from "@/lib/seo/schema";
import { getComplianceDoc, toSnippet } from "@/lib/library/api";
import type {
  BreadcrumbItem,
  MetadataItem,
  TocEntry,
  OfficialSourceLink,
} from "@/types/library";

const SITE_URL = "https://rayhanai.com";

interface PageProps {
  params: Promise<{ slug: string }>;
}

/** Pull an 11-char YouTube id out of a watch/short/embed URL (or bare id). */
function extractYouTubeId(input: string): string | null {
  const trimmed = input.trim();
  if (/^[\w-]{11}$/.test(trimmed)) return trimmed;
  try {
    const url = new URL(trimmed);
    const host = url.hostname.replace(/^www\./, "");
    if (host === "youtu.be") {
      const id = url.pathname.slice(1).split("/")[0];
      return /^[\w-]{11}$/.test(id) ? id : null;
    }
    if (host.endsWith("youtube.com")) {
      const fromQuery = url.searchParams.get("v");
      if (fromQuery && /^[\w-]{11}$/.test(fromQuery)) return fromQuery;
      const match = url.pathname.match(/\/(?:embed|shorts|v)\/([\w-]{11})/);
      if (match) return match[1];
    }
  } catch {
    return null;
  }
  return null;
}

/** A titled bullet list («المتطلبات» / «المستندات المطلوبة»). */
function BulletSection({
  title,
  items,
  Icon,
}: {
  title: string;
  items: string[];
  Icon: typeof CheckCircle2;
}) {
  if (items.length === 0) return null;
  return (
    <section dir="rtl" className="space-y-3">
      <h2 className="flex items-center gap-2 text-sm font-bold text-foreground">
        <Icon aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
        {title}
      </h2>
      <ul className="space-y-2">
        {items.map((item, index) => (
          <li
            key={index}
            className="flex items-start gap-2.5 text-sm leading-relaxed text-text-secondary"
          >
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const doc = await getComplianceDoc(slug);

  if (!doc) {
    return {
      title: "ريحان",
      description: "المساعد القانوني الذكي للمحامين السعوديين",
    };
  }

  const title = `${doc.title} — الشروط والخطوات والمستندات | ريحان`;
  const description =
    toSnippet(doc.intro_description) ||
    `${doc.title} — الشروط والمستندات المطلوبة وخطوات التنفيذ عبر ريحان.`;
  const canonical = `/compliance/${encodeURIComponent(doc.slug)}`;
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
export default async function ComplianceDocPage({ params }: PageProps) {
  const { slug } = await params;
  const doc = await getComplianceDoc(slug);
  if (!doc) notFound();

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "خدمات الامتثال", href: "/compliance" },
    { label: doc.title },
  ];

  const metadataItems: MetadataItem[] = [];
  if (doc.provider_name) {
    metadataItems.push({ label: "الجهة", value: doc.provider_name });
  }
  if (doc.sectors.length > 0) {
    metadataItems.push({ label: "القطاعات", value: doc.sectors.join("، ") });
  }

  const stepEntries: TocEntry[] = doc.steps.map((step, index) => ({
    id: `step-${index}`,
    label: step,
  }));

  const officialSources: OfficialSourceLink[] = [];
  if (doc.official_url) {
    officialSources.push({
      label: "التقديم عبر الموقع الرسمي",
      href: doc.official_url,
    });
  }
  if (doc.pdf_link) {
    officialSources.push({ label: "دليل PDF", href: doc.pdf_link });
  }

  // JSON-LD: HowTo (steps) + VideoObject when an explainer video is present.
  const howTo = buildHowTo({
    name: doc.title,
    description: doc.intro_description || undefined,
    steps: doc.steps.map((step) => ({ name: step })),
  });
  const videoId = doc.youtube_url
    ? extractYouTubeId(doc.youtube_url)
    : null;
  const jsonLd =
    videoId && doc.youtube_url
      ? [
          howTo,
          buildVideoObject({
            name: doc.title,
            description:
              toSnippet(doc.intro_description) || doc.title,
            thumbnailUrl: `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,
            uploadDate: new Date().toISOString(),
            contentUrl: doc.youtube_url,
            embedUrl: `https://www.youtube-nocookie.com/embed/${videoId}`,
          }),
        ]
      : howTo;

  return (
    <LibraryPageShell maxWidth="doc">
      <LibraryUseBeacon contentType="service" slug={doc.slug} gate="open" />
      <JsonLd data={jsonLd} />

      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold leading-tight text-foreground sm:text-3xl">
              {doc.title}
            </h1>
            {doc.provider_name && (
              <span className="inline-flex items-center rounded-full bg-pill px-2.5 py-1 text-xs font-medium text-pill-fg">
                {doc.provider_name}
              </span>
            )}
          </div>
          <TrustLine
            updatedAt={new Date().toISOString()}
            entity={doc.provider_name || undefined}
          />
        </header>

        {metadataItems.length > 0 && <MetadataCard items={metadataItems} />}

        {(doc.intro_title || doc.intro_description) && (
          <section className="space-y-3">
            {doc.intro_title && (
              <h2 className="border-s-[3px] border-primary/50 ps-3 text-lg font-bold leading-snug text-foreground">
                {doc.intro_title}
              </h2>
            )}
            {doc.intro_description && (
              <LeadSummary
                text={doc.intro_description}
                dedupeHeading={doc.intro_title}
              />
            )}
          </section>
        )}

        <BulletSection
          title="المتطلبات"
          items={doc.requirements}
          Icon={CheckCircle2}
        />

        <BulletSection
          title="المستندات المطلوبة"
          items={doc.required_documents}
          Icon={FileText}
        />

        {stepEntries.length > 0 && (
          <TocList
            entries={stepEntries}
            title="الخطوات"
            variant="steps"
            collapsible={false}
          />
        )}

        {doc.youtube_url && (
          <MediaBlock youtubeUrl={doc.youtube_url} title={doc.title} />
        )}

        {officialSources.length > 0 && (
          <OfficialSources sources={officialSources} />
        )}
      </div>

      <AskRayhanWidget
        pageType="compliance"
        pageId={doc.slug}
        pageTitle={doc.title}
      />
    </LibraryPageShell>
  );
}
