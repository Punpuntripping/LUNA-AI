import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { JudgmentsHubView } from "@/components/library/hub/JudgmentsHubView";
import { readParam, type RawSearchParams } from "@/lib/library/hub-query";
import {
  COURT_ORDER,
  courtHeading,
  courtLabel,
  isCourtSlug,
  normalizeCourtSlug,
} from "@/lib/library/courts";

// Public library — page 1 of ONE court section: `/judgments/courts/{court}`.
// Server component; the DATA is ISR via the fetch revalidate window in
// `lib/library/api.ts` (NO force-dynamic). Reading `searchParams` for the
// درجة المحكمة chips makes the RENDER request-time, exactly as on `/judgments`
// itself; the upstream fetch is still cached per court × filter combination.
//
// ── WHY THE EXTRA `courts` SEGMENT ──────────────────────────────────────────
// `courts` is a STATIC segment, so it resolves ahead of the existing
// `app/judgments/[slug]` document route — the same mechanism that already lets
// `/judgments/page/2` coexist with `/judgments/{judgment-slug}`. Next cannot
// host `app/judgments/[court]/` alongside `app/judgments/[slug]/` (build-time
// error: two different dynamic names at one path level), so the extra segment is
// what makes this URL shape legal. EXISTING JUDGMENT DOCUMENT URLS DO NOT MOVE.
//
// ── THE SLUG IS ARABIC ──────────────────────────────────────────────────────
// Next hands a non-ASCII dynamic param PERCENT-ENCODED, so `params.court` is
// `%D8%A7%D9%84…` and matches nothing until `normalizeCourtSlug()` decodes it.
// Everything downstream receives the DECODED slug: `URLSearchParams` re-encodes
// it once for the backend query param, and `encodeURIComponent` produces the
// canonical URL. Encoding an already-encoded param yields `%25D8…`, which 404s.
//
// ⚠ THE INDEXABLE CARVE-OUT IS WITHDRAWN (2026-08-11) — THIS PAGE IS NOINDEX
// AGAIN, LIKE THE REST OF THE WING. It was the one exception to the PDPL gate:
// page 1 of a court section lists derived titles only, never judgment text, so
// it carried no robots directive and its 12 URLs were listed in the sitemap's
// `courts` section. Then a court became a paid-only SECTION
// (`section_scope_allowed` in `backend/app/api/public_library.py`), which means
// the body an anonymous visitor gets here is the section wall — and since
// Googlebot is anonymous, that is what a crawler would index. A walled page is
// worth no index slot, and serving a crawler the cards no signed-out human can
// reach would be cloaking. So the directive is back and the sitemap section is
// gone (`lib/seo/sitemap.ts`).
//
// TO RESTORE: un-gate the court axis, drop the `robots` keys below, and re-add
// "courts" to SITEMAP_SECTIONS + its route case, in ONE commit. Everything this
// page links to — the judgment documents, deep pagination — stays `noindex,
// nofollow` regardless until the anonymization audit passes; see the flip
// checklist at the top of `app/judgments/page.tsx`.
const NOINDEX_PDPL = { index: false, follow: false } as const;

const WING_TITLE = "الأحكام القضائية السعودية";

interface PageProps {
  params: Promise<{ court: string }>;
  searchParams: Promise<RawSearchParams>;
}

/**
 * The 12 court slugs — the closed vocabulary from `lib/library/courts.ts`
 * (mirror of `shared/library/courts.py`), in corpus-volume/browse order.
 * Mirrors `app/library/[sector]/[type]/page.tsx`, with one difference worth
 * naming: the sector list is FETCHED because the server owns it, while the 12
 * courts are a compile-time constant, so this never needs a live backend and
 * can never soft-fail to an empty list.
 *
 * The segment still renders at request time today because it reads
 * `searchParams` for the chips (same as `/judgments`), so this enumerates the
 * routes rather than baking them. It is what makes the set explicit and is
 * already correct for the day the wing drops its query-string filters.
 */
export function generateStaticParams(): { court: string }[] {
  return COURT_ORDER.map((court) => ({ court }));
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const { court } = await params;
  const slug = normalizeCourtSlug(court);
  const label = courtLabel(slug);
  if (!label) {
    return { title: `${WING_TITLE} | ريحان`, robots: NOINDEX_PDPL };
  }

  const heading = courtHeading(label);
  const title = `${heading} | ريحان`;
  const description = `${label} — أحكامها ووقائعها وأسبابها ومنطوقها، والأنظمة التي استندت إليها، موثّقة عبر ريحان.`;
  const canonical = `/judgments/courts/${encodeURIComponent(slug)}`;
  const ogImage = `/og?title=${encodeURIComponent(heading)}`;

  return {
    title,
    description,
    // Canonical stays the bare section: the درجة المحكمة chips slice the SAME
    // collection, they are never separate documents. `robots` is back on since
    // the section went paid-only — see the header note.
    alternates: { canonical },
    robots: NOINDEX_PDPL,
    openGraph: {
      title,
      description,
      siteName: "ريحان",
      type: "website",
      locale: "ar_SA",
      url: canonical,
      images: [{ url: ogImage, width: 1200, height: 630, alt: heading }],
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
export default async function JudgmentsCourtPage({
  params,
  searchParams,
}: PageProps) {
  const { court } = await params;
  const slug = normalizeCourtSlug(court);
  // A closed vocabulary — anything else is not a page, and must never reach the
  // backend as a query param (`page` / `mine` are refused on both sides).
  if (!isCourtSlug(slug)) notFound();

  const query = await searchParams;
  return (
    <JudgmentsHubView
      page={1}
      filters={{
        court: slug,
        court_level: readParam(query, "court_level"),
        domain: readParam(query, "domain"),
        q: readParam(query, "q"),
      }}
    />
  );
}
