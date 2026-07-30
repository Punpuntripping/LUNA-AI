import { LibraryPageShell } from "@/components/library/blocks";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { CircularCard } from "@/components/library/hub/CircularCard";
import { HubPagination } from "@/components/library/hub/HubPagination";
import { HubCtaWall } from "@/components/library/hub/HubCtaWall";
import { getCircularsHub } from "@/lib/library/api";
import type { BreadcrumbItem } from "@/types/library";

/**
 * Shared server-component body for the /circulars hub — page 1 (`/circulars`)
 * and deep pages (`/circulars/page/{n}`). Mirrors RegulationsHubView: CTA wall on
 * the anon cap, empty state on a null payload (no notFound — the base hub is
 * build-prerendered and must survive an offline backend), else the 3×3 card grid
 * + pagination.
 *
 * `verifiedBot` is the §3.7 crawler exemption, set by the DEEP-page route only
 * (`app/circulars/page/[n]`) — page 1 must stay statically prerendered.
 */
export async function CircularsHubView({
  page,
  verifiedBot,
}: {
  page: number;
  verifiedBot?: boolean;
}) {
  const data = await getCircularsHub(page, undefined, { verifiedBot });
  const items = data?.items ?? [];
  const isCap = data?.cap_reached ?? false;
  // Unauthenticated + ISR-cached ⇒ always the ANON cap (PART 9 trap 2). The
  // caller's real cap is resolved client-side inside HubCtaWall.
  const anonMaxPage = data?.max_page ?? data?.max_anon_page ?? 1;

  const crumbs: BreadcrumbItem[] =
    page > 1
      ? [
          { label: "الرئيسية", href: "/" },
          { label: "التعاميم", href: "/circulars" },
          { label: `صفحة ${page}` },
        ]
      : [{ label: "الرئيسية", href: "/" }, { label: "التعاميم" }];

  return (
    <LibraryPageShell maxWidth="hub" showCta={!isCap}>
      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            التعاميم التنظيمية السعودية
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            تصفّح التعاميم التنظيمية الصادرة عن الجهات السعودية — نصوصها وجهاتها
            المصدرة.
          </p>
        </header>

        {isCap ? (
          <HubCtaWall
            section="circulars"
            basePath="/circulars"
            page={page}
            totalPages={data?.total_pages ?? 0}
            anonMaxPage={anonMaxPage}
          />
        ) : items.length === 0 ? (
          <p className="py-16 text-center text-sm text-muted-foreground">
            لا توجد تعاميم لعرضها حالياً.
          </p>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((item) => (
                <CircularCard key={item.slug} item={item} />
              ))}
            </div>
            {data && (
              <HubPagination
                basePath="/circulars"
                currentPage={data.page}
                totalPages={data.total_pages}
              />
            )}
          </>
        )}
      </div>
    </LibraryPageShell>
  );
}
