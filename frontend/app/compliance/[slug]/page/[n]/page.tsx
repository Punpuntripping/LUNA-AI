import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ComplianceHubView } from "@/components/library/hub/ComplianceHubView";
import { getComplianceHub } from "@/lib/library/api";
import { readVerifiedBotSignal } from "@/lib/library/crawler-signal";
import {
  entityHeading,
  entityLabel,
  entityPagePath,
  isEntitySlug,
} from "@/lib/library/entities";

// Deep pages of ONE entity section: `/compliance/{entity}/page/{n}`.
// `n` must be an integer ≥ 2 — page 1 lives at `/compliance/{entity}`, its own
// canonical; anything else → 404, the same rule `app/compliance/page/[n]`
// applies. Server component; data ISR via `lib/library/api.ts`.
//
// ONLY AN ENTITY HAS DEEP PAGES. `[slug]` is shared with the 533 guide URLs
// (compliance_entity_sections D2), and a guide is a single document — so an
// unknown slug, a reserved word, or a real guide slug all 404 here rather than
// rendering an empty hub. `/compliance/page/{n}` is unaffected: `page` is a
// STATIC segment and resolves ahead of `[slug]`.
//
// ⚠ Deep pages only: `readVerifiedBotSignal()` (plan §3.7) reads request headers
// and can take THIS segment dynamic once `EDGE_SECRET` is set. `/compliance`,
// every `/compliance/{entity}` page 1 and every `/compliance/{guide}` page stay
// statically prerendered + ISR. Full reasoning — including why dynamic is
// required for correctness here and why `force-dynamic` must NOT be added — in
// `app/regulations/page/[n]/page.tsx`.
//
// ⚠ Deep pages are also where this axis meets the anon depth cap, which D1 did
// NOT lift. The entity axis is anon-visible and indexable at page 1; page 2 of
// وزارة العدل walls for an anonymous reader exactly as `/compliance/page/2`
// does, and the verified-crawler waiver is what still gets Googlebot through to
// the cards — which is what makes indexing page 1 honest rather than cloaked.

const WING_TITLE = "دليل الخدمات الحكومية";
const WING_DESCRIPTION =
  "دليل مبسط لأكثر الخدمات الحكومية السعودية استخداماً، وأين تُنجز كل خدمة على موقع الجهة الرسمي.";

interface PageProps {
  params: Promise<{ slug: string; n: string }>;
}

function parsePage(raw: string): number | null {
  const num = Number(raw);
  if (!Number.isInteger(num) || num < 2) return null;
  return num;
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { slug, n } = await params;
  const label = entityLabel(slug);
  const pageNum = parsePage(n);

  if (!label || pageNum === null) {
    // Not a page — the body below calls `notFound()`. Nothing to canonicalise
    // and no `robots` to argue about, so this is just the wing's own title.
    return { title: `${WING_TITLE} | ريحان`, description: WING_DESCRIPTION };
  }

  const heading = entityHeading(label);
  const description = `أدلة الخدمات التي تقدّمها ${label} — صفحة ${n} عبر ريحان.`;

  // The `cap_reached` rule the sibling hubs use: an anon depth-cap page renders
  // a signup wall, so don't index the wall — but let Googlebot follow its links.
  //
  // ⚠ DELIBERATELY UNEXEMPTED — this fetch asks the ANON question and its answer
  // drives `noindex, follow`. Exempting it would hand a crawler an INDEXABLE deep
  // page (index bloat); the body below is exempted instead, which is what buys
  // crawl reach. See `app/regulations/page/[n]/page.tsx`.
  const data = await getComplianceHub(pageNum, { entity_slug: slug });
  const capped = data?.cap_reached ?? false;

  return {
    title: `${heading} — صفحة ${n} | ريحان`,
    description,
    // ASCII slug — nothing to percent-encode (`lib/library/entities.ts`).
    alternates: { canonical: entityPagePath(slug, pageNum) },
    ...(capped ? { robots: { index: false, follow: true } } : {}),
  };
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default async function ComplianceEntityDeepPage({ params }: PageProps) {
  const { slug, n } = await params;
  // A closed vocabulary — anything else is not a page here, and must never
  // reach the backend as a section param (`page` / `entities` / `mine` are
  // refused on both sides).
  if (!isEntitySlug(slug)) notFound();

  const pageNum = parsePage(n);
  if (pageNum === null) notFound();

  const verifiedBot = await readVerifiedBotSignal();
  return (
    <ComplianceHubView
      page={pageNum}
      entitySlug={slug}
      verifiedBot={verifiedBot}
    />
  );
}
