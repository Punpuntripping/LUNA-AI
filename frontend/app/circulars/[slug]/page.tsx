import type { Metadata } from "next";
import { LibraryUseBeacon } from "@/components/library/mine/LibraryUseBeacon";
import { notFound } from "next/navigation";
import {
  LibraryPageShell,
  TopicBreadcrumbs,
  TrustLine,
  MetadataCard,
  ArticleBody,
  OfficialSources,
  AskRayhanWidget,
} from "@/components/library/blocks";
import { FullContentGate } from "@/components/library/FullContentGate";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildArticle, buildPaywallFragment } from "@/lib/seo/schema";
import { getCircularDoc, toSnippet } from "@/lib/library/api";
import type {
  BreadcrumbItem,
  MetadataItem,
  OfficialSourceLink,
  GateInfo,
} from "@/types/library";

const SITE_URL = "https://rayhanai.com";

interface PageProps {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const doc = await getCircularDoc(slug);

  if (!doc) {
    return {
      title: "ريحان",
      description: "المساعد القانوني الذكي للمحامين السعوديين",
    };
  }

  const title = `${doc.title} | ريحان`;
  const description =
    toSnippet(doc.text) ||
    `${doc.title} — نص التعميم وجهته المصدرة عبر ريحان.`;
  const canonical = `/circulars/${encodeURIComponent(doc.slug)}`;
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
export default async function CircularDocPage({ params }: PageProps) {
  const { slug } = await params;
  const doc = await getCircularDoc(slug);
  if (!doc) notFound();

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "التعاميم", href: "/circulars" },
    { label: doc.title },
  ];

  // `source_label` is an internal provenance token — NEVER surfaced. The
  // metadata card carries only the payload's own label/value rows (الجهة، المرجع).
  const metadataItems: MetadataItem[] = doc.metadata.map((row) => ({
    label: row.label,
    value: row.value,
  }));

  const officialSources: OfficialSourceLink[] = doc.official_sources.map(
    (source) => ({ label: source.title, href: source.href }),
  );

  const gate: GateInfo | undefined = doc.is_truncated
    ? {
        isTruncated: true,
        hiddenPlaceholderLines: doc.hidden_placeholder_lines,
      }
    : undefined;

  const now = new Date().toISOString();
  const articleNode = {
    ...buildArticle({
      title: doc.title,
      description:
        toSnippet(doc.text) || `${doc.title} — نص التعميم وجهته المصدرة.`,
      url: `${SITE_URL}/circulars/${encodeURIComponent(doc.slug)}`,
      datePublished: now,
      dateModified: now,
    }),
    ...(doc.gate_effective === "gated"
      ? buildPaywallFragment(".gated-body")
      : {}),
  };

  return (
    <LibraryPageShell maxWidth="doc">
      <LibraryUseBeacon contentType="circular" slug={doc.slug} gate={doc.gate_effective === "gated" || doc.is_truncated ? "gated" : "open"} />
      <JsonLd data={articleNode} />

      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2.5">
          <h1 className="text-2xl font-bold leading-tight text-foreground sm:text-3xl">
            {doc.title}
          </h1>
          <TrustLine updatedAt={now} entity={doc.entity_name ?? undefined} />
        </header>

        {metadataItems.length > 0 && <MetadataCard items={metadataItems} />}

        {/* Body — anon gets the gate-truncated نص التعميم; a signed-in reader's
            browser swaps in the full text via the authed endpoint. */}
        {/* A short (<=800-char) تعميم is `gate_effective: 'open'` and already
            complete — no reveal action, no unlock spendable on nothing. */}
        <FullContentGate
          contentType="circular"
          kind="text"
          fullKey={doc.slug}
          gated={doc.gate_effective === "gated" || doc.is_truncated}
        >
          <ArticleBody
            visibleText={doc.text}
            gate={gate}
            plain
            dedupeHeading={doc.title}
          />
        </FullContentGate>

        {officialSources.length > 0 && (
          <OfficialSources sources={officialSources} />
        )}
      </div>

      <AskRayhanWidget
        pageType="circular"
        pageId={doc.slug}
        pageTitle={doc.title}
      />
    </LibraryPageShell>
  );
}
