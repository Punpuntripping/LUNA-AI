import type { Metadata } from "next";
import { LibraryPageShell } from "@/components/library/blocks";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { CalculatorCard } from "@/components/calculators/CalculatorCard";
import { CALCULATORS } from "@/lib/calculators/registry";
import type { BreadcrumbItem } from "@/types/library";

// Public /calculators hub. Static — the calculator set is a code registry, so
// there is no data fetch and no ISR revalidation window; prerendered at build.

const HUB_TITLE = "الحاسبات القانونية";
const HUB_DESCRIPTION =
  "حاسبات قانونية مجانية وفق الأنظمة السعودية — مكافأة نهاية الخدمة، مدة الإشعار، وأجر العمل الإضافي، مع شرح الصيغة والأساس النظامي.";

export function generateMetadata(): Metadata {
  const title = `${HUB_TITLE} — احسب حقوقك مجاناً | ريحان`;
  const ogImage = `/og?title=${encodeURIComponent(HUB_TITLE)}`;
  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: "/calculators" },
    openGraph: {
      title,
      description: HUB_DESCRIPTION,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: "/calculators",
      images: [{ url: ogImage, width: 1200, height: 630, alt: HUB_TITLE }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description: HUB_DESCRIPTION,
      images: [ogImage],
    },
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function CalculatorsHubPage() {
  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "الحاسبات" },
  ];

  return (
    <LibraryPageShell maxWidth="hub">
      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            {HUB_TITLE}
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {HUB_DESCRIPTION}
          </p>
        </header>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {CALCULATORS.map((calc) => (
            <CalculatorCard key={calc.slug} calc={calc} />
          ))}
        </div>
      </div>
    </LibraryPageShell>
  );
}
