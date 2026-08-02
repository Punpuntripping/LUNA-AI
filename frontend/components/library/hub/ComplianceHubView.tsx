import { LibraryPageShell } from "@/components/library/blocks";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { ComplianceCard } from "@/components/library/hub/ComplianceCard";
import { HubPagination } from "@/components/library/hub/HubPagination";
import { HubCtaWall } from "@/components/library/hub/HubCtaWall";
import { HubSearchPanel } from "@/components/library/hub/HubSearchPanel";
import { getComplianceHub, getSectorSlugMap } from "@/lib/library/api";
import type { BreadcrumbItem } from "@/types/library";

/**
 * Shared server-component body for the /compliance hub — page 1 (`/compliance`)
 * and deep pages (`/compliance/page/{n}`). Mirrors RegulationsHubView: CTA wall
 * on the anon cap, empty state on a null payload (no notFound — the base hub is
 * build-prerendered), else the 3×3 card grid + pagination.
 *
 * `verifiedBot` is the §3.7 crawler exemption, set by the DEEP-page route only
 * (`app/compliance/page/[n]`) — page 1 must stay statically prerendered.
 */
export async function ComplianceHubView({
  page,
  verifiedBot,
}: {
  page: number;
  verifiedBot?: boolean;
}) {
  // `getSectorSlugMap` soft-fails to `{}` — a missing map costs the sector
  // pills their links, never the hub its render.
  const [data, sectorSlugs] = await Promise.all([
    getComplianceHub(page, undefined, { verifiedBot }),
    getSectorSlugMap(),
  ]);
  const items = data?.items ?? [];
  const isCap = data?.cap_reached ?? false;
  // Unauthenticated + ISR-cached ⇒ always the ANON cap (PART 9 trap 2). The
  // caller's real cap is resolved client-side inside HubCtaWall.
  const anonMaxPage = data?.max_page ?? data?.max_anon_page ?? 1;

  const crumbs: BreadcrumbItem[] =
    page > 1
      ? [
          { label: "الرئيسية", href: "/" },
          { label: "خدمات الامتثال", href: "/compliance" },
          { label: `صفحة ${page}` },
        ]
      : [{ label: "الرئيسية", href: "/" }, { label: "خدمات الامتثال" }];

  return (
    <LibraryPageShell maxWidth="hub" showCta={!isCap}>
      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            خدمات الامتثال الحكومية
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            أدلّة الخدمات الحكومية — الشروط والمستندات المطلوبة وخطوات التنفيذ.
          </p>
        </header>

        {/* The wing's first search box (bm25_navigation_search.md §6.2). D4:
            «/services» IS this wing — the `services` table backs it, and the
            search corpus is named `service` for that reason. */}
        <HubSearchPanel section="compliance" sectorSlugs={sectorSlugs}>
          {isCap ? (
            <HubCtaWall
              section="compliance"
              basePath="/compliance"
              page={page}
              totalPages={data?.total_pages ?? 0}
              anonMaxPage={anonMaxPage}
              sectorSlugs={sectorSlugs}
            />
          ) : items.length === 0 ? (
            <p className="py-16 text-center text-sm text-muted-foreground">
              لا توجد خدمات لعرضها حالياً.
            </p>
          ) : (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {items.map((item) => (
                  <ComplianceCard
                    key={item.slug}
                    item={item}
                    sectorSlugs={sectorSlugs}
                  />
                ))}
              </div>
              {data && (
                <HubPagination
                  basePath="/compliance"
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
