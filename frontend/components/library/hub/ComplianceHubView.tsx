import { LibraryPageShell } from "@/components/library/blocks";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { ComplianceCard } from "@/components/library/hub/ComplianceCard";
import { HubPagination } from "@/components/library/hub/HubPagination";
import { HubCtaWall } from "@/components/library/hub/HubCtaWall";
import { getComplianceHub, getSectorSlugMap } from "@/lib/library/api";
import { LIBRARY_TYPE_META } from "@/lib/library/sectors";
import type { BreadcrumbItem } from "@/types/library";

/**
 * Shared server-component body for the /compliance hub — page 1 (`/compliance`)
 * and deep pages (`/compliance/page/{n}`).
 *
 * ⚠ THIS WING IS LIVE AND EMPTY. It is backed by `compliance_table` («دليل مبسط
 * لأكثر الخدمات استخداماً»), which does not exist yet, so the backend answers a
 * zero-item page and this renders the empty state every time. That is the
 * intended production state — the route, the cap wall and the pagination are
 * wired now so nothing has to be rediscovered when the table lands.
 *
 * It is NOT the wing retired on 2026-08-03. That one republished the `services`
 * corpus (الشروط / المستندات / الخطوات); this one is our own short guide plus a
 * link out to the issuing entity.
 *
 * NO SEARCH PANEL, deliberately: `HubSearchPanel` runs BM25 over `search_index`,
 * and `compliance_table` has no rows there — a live-looking box that can only
 * ever return nothing is worse than no box. Add it back with the corpus.
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
          { label: "دليل الخدمات", href: "/compliance" },
          { label: `صفحة ${page}` },
        ]
      : [{ label: "الرئيسية", href: "/" }, { label: "دليل الخدمات" }];

  return (
    <LibraryPageShell maxWidth="hub" showCta={!isCap}>
      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            {LIBRARY_TYPE_META.compliance.longLabel}
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {LIBRARY_TYPE_META.compliance.description}
          </p>
        </header>

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
            {LIBRARY_TYPE_META.compliance.empty}
          </p>
        ) : (
          <div className="space-y-6">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((item) => (
                <ComplianceCard key={item.slug} item={item} />
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
      </div>
    </LibraryPageShell>
  );
}
