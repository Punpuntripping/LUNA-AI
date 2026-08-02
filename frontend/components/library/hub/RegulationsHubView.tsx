import { LibraryPageShell } from "@/components/library/blocks";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { RegulationCard } from "@/components/library/hub/RegulationCard";
import { HubPagination } from "@/components/library/hub/HubPagination";
import { HubCtaWall } from "@/components/library/hub/HubCtaWall";
import { HubSearchPanel } from "@/components/library/hub/HubSearchPanel";
import { getRegulationsHub, getSectorSlugMap } from "@/lib/library/api";
import type { BreadcrumbItem } from "@/types/library";

/**
 * Shared server-component body for the /regulations hub — page 1 (`/regulations`)
 * and deep pages (`/regulations/page/{n}`) both render this with their page
 * number. Fetches the hub envelope, then renders the CTA wall (anon cap), an
 * empty state (backend down / no results), or the 3×3 card grid + pagination.
 * Does NOT `notFound()` on a null payload — the base hub is prerendered at build
 * and must survive an offline backend gracefully.
 *
 * `verifiedBot` is the §3.7 crawler exemption, passed in by the DEEP-page route
 * only (`app/regulations/page/[n]`). Page 1 never sets it and must never start:
 * page 1 is a statically prerendered segment, and reading the request headers it
 * would take to derive the flag is exactly what would make it dynamic.
 */
export async function RegulationsHubView({
  page,
  verifiedBot,
}: {
  page: number;
  verifiedBot?: boolean;
}) {
  // `getSectorSlugMap` soft-fails to `{}` (see its note) — a missing map costs
  // the sector pills their links, never the hub its render.
  const [data, sectorSlugs] = await Promise.all([
    getRegulationsHub(page, undefined, { verifiedBot }),
    getSectorSlugMap(),
  ]);
  const items = data?.items ?? [];
  const isCap = data?.cap_reached ?? false;
  // This fetch is UNAUTHENTICATED and ISR-cached (PART 9 trap 2), so the cap it
  // reports is always the ANON one. It is only the fallback the client-side
  // authed reveal inside HubCtaWall starts from — never a per-user value.
  const anonMaxPage = data?.max_page ?? data?.max_anon_page ?? 1;

  const crumbs: BreadcrumbItem[] =
    page > 1
      ? [
          { label: "الرئيسية", href: "/" },
          { label: "الأنظمة", href: "/regulations" },
          { label: `صفحة ${page}` },
        ]
      : [{ label: "الرئيسية", href: "/" }, { label: "الأنظمة" }];

  return (
    <LibraryPageShell maxWidth="hub" showCta={!isCap}>
      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            الأنظمة واللوائح السعودية
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            تصفّح الأنظمة واللوائح السعودية مع ملخّصات ومواد ومصادر رسمية موثّقة.
          </p>
        </header>

        {/* The wing's first search box (bm25_navigation_search.md §6.2). The
            panel owns the query and swaps its own results in for the body
            below while a search is live; this view stays a pure server
            component and its fetch above never sees `q` — see the panel's
            header for why a hub search cannot run server-side. */}
        <HubSearchPanel section="regulations" sectorSlugs={sectorSlugs}>
          {isCap ? (
            <HubCtaWall
              section="regulations"
              basePath="/regulations"
              page={page}
              totalPages={data?.total_pages ?? 0}
              anonMaxPage={anonMaxPage}
              sectorSlugs={sectorSlugs}
            />
          ) : items.length === 0 ? (
            <p className="py-16 text-center text-sm text-muted-foreground">
              لا توجد أنظمة لعرضها حالياً.
            </p>
          ) : (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {items.map((item) => (
                  <RegulationCard
                    key={item.slug}
                    item={item}
                    sectorSlugs={sectorSlugs}
                  />
                ))}
              </div>
              {data && (
                <HubPagination
                  basePath="/regulations"
                  currentPage={data.page}
                  totalPages={data.total_pages}
                />
              )}
            </div>
          )}
        </HubSearchPanel>
      </div>
    </LibraryPageShell>
  );
}
