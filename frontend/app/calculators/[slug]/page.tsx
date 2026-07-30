import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Info } from "lucide-react";
import {
  LibraryPageShell,
  TopicBreadcrumbs,
  TrustLine,
  LeadSummary,
  ReferencesMesh,
  FaqBlock,
  AskRayhanWidget,
} from "@/components/library/blocks";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { CalculatorForm } from "@/components/calculators/CalculatorForm";
import { CALCULATORS, getCalculator } from "@/lib/calculators/registry";
import type { BreadcrumbItem, ReferenceItem } from "@/types/library";

interface PageProps {
  params: Promise<{ slug: string }>;
}

/** Static wing — one page per registry entry (Arabic slugs). */
export function generateStaticParams(): { slug: string }[] {
  return CALCULATORS.map((calc) => ({ slug: calc.slug }));
}

/** Next 15 delivers non-ASCII params percent-encoded; decode once to match the
 * registry's Arabic slugs. */
function decodeSlug(slug: string): string {
  try {
    return decodeURIComponent(slug);
  } catch {
    return slug;
  }
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const calc = getCalculator(decodeSlug(slug));

  if (!calc) {
    return {
      title: "ريحان",
      description: "المساعد القانوني الذكي للمحامين السعوديين",
    };
  }

  const title = `حاسبة ${calc.title_ar} — احسبها مجاناً | ريحان`;
  const canonical = `/calculators/${encodeURIComponent(calc.slug)}`;
  const ogImage = `/og?title=${encodeURIComponent(`حاسبة ${calc.title_ar}`)}`;

  return {
    title,
    description: calc.description,
    alternates: { canonical },
    openGraph: {
      title,
      description: calc.description,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: canonical,
      images: [{ url: ogImage, width: 1200, height: 630, alt: title }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description: calc.description,
      images: [ogImage],
    },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function CalculatorPage({ params }: PageProps) {
  const { slug } = await params;
  const calc = getCalculator(decodeSlug(slug));
  if (!calc) notFound();

  const heading = `حاسبة ${calc.title_ar}`;

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "الحاسبات", href: "/calculators" },
    { label: heading },
  ];

  // الأساس النظامي — each cited مادة links to its article page (bidirectional
  // mesh: those مادة pages embed this calculator via CalculatorBlock).
  const legalItems: ReferenceItem[] = calc.legalBasis.flatMap((basis) =>
    basis.articleNos.map((no) => ({
      title: `${basis.label} — المادة ${no}`,
      href: `/regulations/${basis.regSlug}/المادة-${no}`,
      kind: "article" as const,
    })),
  );

  return (
    <LibraryPageShell maxWidth="doc">
      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2.5">
          <h1 className="text-2xl font-bold leading-tight text-foreground sm:text-3xl">
            {heading}
          </h1>
          <TrustLine updatedAt={new Date().toISOString()} />
        </header>

        <LeadSummary text={calc.description} />

        <CalculatorForm slug={calc.slug} />

        {/* Guidance disclaimer — every calculator page carries it. */}
        <p
          dir="rtl"
          className="flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground"
        >
          <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
          نتيجة استرشادية — راجع مختصاً قبل الاعتماد عليها في أي إجراء نظامي.
        </p>

        <section dir="rtl" className="space-y-3">
          <h2 className="text-lg font-bold text-foreground">شرح الصيغة</h2>
          <MarkdownRenderer content={calc.formulaExplanation_md} />
        </section>

        {legalItems.length > 0 && (
          <ReferencesMesh items={legalItems} title="الأساس النظامي" />
        )}

        <FaqBlock items={calc.faq} />
      </div>

      <AskRayhanWidget
        pageType="calculator"
        pageId={calc.slug}
        pageTitle={heading}
      />
    </LibraryPageShell>
  );
}
