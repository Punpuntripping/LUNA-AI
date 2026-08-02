import { notFound } from "next/navigation";
import { SitePageShell } from "@/components/site/SitePageShell";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { HubPagination } from "@/components/library/hub/HubPagination";
import { HubCtaWall } from "@/components/library/hub/HubCtaWall";
import { LibraryTypeChips } from "@/components/library/sectors/LibraryTypeChips";
import { SectorSwitcher } from "@/components/library/sectors/SectorSwitcher";
import { SectorTypeCards } from "@/components/library/sectors/SectorTypeCards";
import {
  getSectorDetail,
  getSectorSlugMap,
  getSectorTypeHub,
  getSectors,
} from "@/lib/library/api";
import {
  LIBRARY_TYPE_META,
  formatCount,
  isReservedSectorSlug,
  sectorPath,
  sectorTypePath,
  sectorTypeHeading,
  type LibraryType,
} from "@/lib/library/sectors";
import { toFilterQuery } from "@/lib/library/hub-query";
import type { BreadcrumbItem } from "@/types/library";

/**
 * Shared server-component body for one sector×type list — page 1
 * (`/library/{sector}/{type}`) and deep pages (`…/page/{n}`) both render this
 * with their page number. Follows the `/regulations` + `/regulations/page/[n]`
 * template exactly: CTA wall on the anon depth cap, empty state on a null
 * payload, else the 3×3 grid + pagination.
 *
 * A SECTOR IS A SECTION, NOT A FILTER (D8/§5). That is a backend decision, but
 * it is what makes this page honest: an anonymous reader sees 9 items, a CTA
 * wall, and the TRUE total page count — not the two-page stub every other
 * filtered view gets.
 *
 * `verifiedBot` is the §3.7 crawler exemption, passed in by the DEEP-page route
 * ONLY. Page 1 never sets it and must never start: it is a statically
 * prerendered segment, and reading the request headers the flag is derived from
 * is exactly what would make it dynamic.
 */
export async function SectorTypeListView({
  slug,
  type,
  page,
  verifiedBot,
}: {
  slug: string;
  type: LibraryType;
  page: number;
  verifiedBot?: boolean;
}) {
  // Reserved segments never reach the backend (T2).
  if (isReservedSectorSlug(slug)) notFound();

  const [detail, data, sectors, sectorSlugs] = await Promise.all([
    getSectorDetail(slug),
    getSectorTypeHub(type, slug, page, { verifiedBot }),
    getSectors(),
    getSectorSlugMap(),
  ]);

  // Unknown sector → 404. `fetchJson` returns null only on a real 404/400; a
  // transient failure throws, so a live page can never 404 on a backend blip.
  if (!detail) notFound();

  // D9 — an EMPTY sector×type combination is not a page. Three of the 152 are
  // (تعاميم/tourism-entertainment, أحكام/research-innovation,
  // أحكام/foreign-affairs); their tab is not rendered anywhere, so nothing
  // links here, and a hand-typed URL gets the same answer a crawler would.
  const total = detail.counts[type] ?? 0;
  if (total === 0) notFound();

  const meta = LIBRARY_TYPE_META[type];
  const items = data?.items ?? [];
  const isCap = data?.cap_reached ?? false;
  // Unauthenticated + ISR-cached ⇒ always the ANON cap (PART 9 trap 2). The
  // caller's real cap is resolved client-side inside HubCtaWall.
  const anonMaxPage = data?.max_page ?? data?.max_anon_page ?? 1;
  const basePath = sectorTypePath(detail.slug, type);
  const heading = sectorTypeHeading(type, detail.name_ar);

  // The sector rides the AUTHED reveal fetch as a query param (that is the
  // wing endpoint's contract) but NOT the pagination links — the path already
  // says which sector this is, and appending `?sector_slug=…` would mint a
  // second URL for one page. Hence `linkQuery=""`.
  const fetchQuery = toFilterQuery({ sector_slug: detail.slug });

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "المكتبة القانونية", href: "/library" },
    { label: detail.name_ar, href: sectorPath(detail.slug) },
    ...(page > 1
      ? [
          { label: meta.longLabel, href: basePath },
          { label: `صفحة ${page}` },
        ]
      : [{ label: meta.longLabel }]),
  ];

  return (
    <SitePageShell>
      <main
        dir="rtl"
        className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-10"
      >
        <div className="space-y-6">
          <TopicBreadcrumbs items={crumbs} />

          <header className="space-y-2">
            <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
              {heading}
            </h1>
            <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
              {meta.description}
              {total > 0 && ` — ${formatCount(total)} عنصر في هذا القطاع.`}
            </p>
          </header>

          <LibraryTypeChips
            counts={detail.counts}
            active={type}
            sectorSlug={detail.slug}
          />

          <SectorSwitcher sectors={sectors} activeSlug={detail.slug} />

          {isCap ? (
            <HubCtaWall
              section={type}
              basePath={basePath}
              page={page}
              totalPages={data?.total_pages ?? 0}
              anonMaxPage={anonMaxPage}
              query={fetchQuery}
              linkQuery=""
              sectorSlugs={sectorSlugs}
            />
          ) : items.length === 0 ? (
            <p className="py-16 text-center text-sm text-muted-foreground">
              {meta.empty}
            </p>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <SectorTypeCards
                  type={type}
                  items={items}
                  sectorSlugs={sectorSlugs}
                />
              </div>
              {data && (
                <HubPagination
                  basePath={basePath}
                  currentPage={data.page}
                  totalPages={data.total_pages}
                />
              )}
            </>
          )}
        </div>
      </main>
    </SitePageShell>
  );
}
