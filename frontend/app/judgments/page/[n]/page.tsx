import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { JudgmentsHubView } from "@/components/library/hub/JudgmentsHubView";
import { readParam, type RawSearchParams } from "@/lib/library/hub-query";

// Public library hub — deep pages of /judgments at /judgments/page/{n}. `n` must
// be an integer >= 2 (page 1 lives at /judgments, its own canonical); else → 404.
// Server component; data ISR via `lib/library/api.ts` (NO force-dynamic).
//
// ⚠ NOINDEX — PDPL GATE. `robots: { index: false, follow: false }` on the whole
// /judgments wing until the PDPL anonymization audit passes (judgment text may
// still carry party-identifying details). See the flip checklist at the top of
// `app/judgments/page.tsx`. NOTE the pre-existing `cap_reached` noindex rule the
// sibling hubs use (`{ index: false, follow: true }` on an anon depth-cap wall)
// is deliberately NOT applied here — it would be strictly weaker than the PDPL
// gate. Restore it as the `capped` branch when the gate is lifted.
const NOINDEX_PDPL = { index: false, follow: false } as const;

const HUB_TITLE = "الأحكام القضائية السعودية";
const HUB_DESCRIPTION =
  "أحكام المحاكم السعودية — وقائعها وأسبابها ومنطوقها والأنظمة المستند إليها، موثّقة عبر ريحان.";

interface PageProps {
  params: Promise<{ n: string }>;
  searchParams: Promise<RawSearchParams>;
}

function parsePage(raw: string): number | null {
  const num = Number(raw);
  if (!Number.isInteger(num) || num < 2) return null;
  return num;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { n } = await params;
  const title = `${HUB_TITLE} — صفحة ${n} | ريحان`;
  return {
    title,
    description: HUB_DESCRIPTION,
    alternates: { canonical: `/judgments/page/${n}` },
    robots: NOINDEX_PDPL,
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function JudgmentsHubDeepPage({
  params,
  searchParams,
}: PageProps) {
  const { n } = await params;
  const pageNum = parsePage(n);
  if (pageNum === null) notFound();

  const query = await searchParams;
  return (
    <JudgmentsHubView
      page={pageNum}
      filters={{
        court_level: readParam(query, "court_level"),
        domain: readParam(query, "domain"),
        q: readParam(query, "q"),
      }}
    />
  );
}
