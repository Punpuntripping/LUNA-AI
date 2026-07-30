import { LibraryPageShell } from "@/components/library/blocks";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { JudgmentCard } from "@/components/library/hub/JudgmentCard";
import { JudgmentsFilterBar } from "@/components/library/hub/JudgmentsFilterBar";
import { HubPagination } from "@/components/library/hub/HubPagination";
import { HubCtaWall } from "@/components/library/hub/HubCtaWall";
import { getJudgmentsHub, type JudgmentsFilters } from "@/lib/library/api";
import { toFilterQuery } from "@/lib/library/hub-query";
import type { BreadcrumbItem } from "@/types/library";

/**
 * Shared server-component body for the /judgments hub — page 1 (`/judgments`)
 * and deep pages (`/judgments/page/{n}`). Mirrors CircularsHubView: CTA wall on
 * the anon depth cap, empty state on a null payload (no notFound — a hub must
 * survive an offline backend), else the 3×3 card grid + pagination.
 *
 * Adds the filter row the other wings don't have (درجة المحكمة chips + search).
 * Filters live entirely in the URL, so this stays a plain server component; the
 * active set is threaded into both the fetch AND the pagination links so paging
 * never silently drops a filter.
 *
 * CONTRACT NOTE: `cap_reached` is optional on the judgments envelope (see
 * `JudgmentHubResponse`). `?? false` = no cap until the backend emits one.
 */
export async function JudgmentsHubView({
  page,
  filters,
}: {
  page: number;
  filters: JudgmentsFilters;
}) {
  const data = await getJudgmentsHub(page, filters);
  const items = data?.items ?? [];
  const isCap = data?.cap_reached ?? false;
  // Unauthenticated + ISR-cached ⇒ always the ANON cap (PART 9 trap 2). The
  // caller's real cap is resolved client-side inside HubCtaWall.
  const anonMaxPage = data?.max_page ?? data?.max_anon_page ?? 1;
  const hasFilters = Boolean(filters.court_level || filters.domain || filters.q);
  const query = toFilterQuery({
    court_level: filters.court_level,
    domain: filters.domain,
    q: filters.q,
  });

  const crumbs: BreadcrumbItem[] =
    page > 1
      ? [
          { label: "الرئيسية", href: "/" },
          { label: "الأحكام القضائية", href: "/judgments" },
          { label: `صفحة ${page}` },
        ]
      : [{ label: "الرئيسية", href: "/" }, { label: "الأحكام القضائية" }];

  return (
    <LibraryPageShell maxWidth="hub" showCta={!isCap}>
      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            الأحكام القضائية السعودية
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            تصفّح الأحكام الصادرة عن المحاكم السعودية — وقائعها وأسبابها
            ومنطوقها، والأنظمة التي استندت إليها.
          </p>
        </header>

        <JudgmentsFilterBar filters={filters} />

        {isCap ? (
          // Filters ride the same query string into the authed reveal, so a
          // capped page never silently drops the reader's active filter set.
          <HubCtaWall
            section="judgments"
            basePath="/judgments"
            page={page}
            totalPages={data?.total_pages ?? 0}
            anonMaxPage={anonMaxPage}
            query={query}
          />
        ) : items.length === 0 ? (
          <p className="py-16 text-center text-sm text-muted-foreground">
            {hasFilters
              ? "لا توجد أحكام مطابقة لبحثك — جرّب تعديل الفلاتر."
              : "لا توجد أحكام لعرضها حالياً."}
          </p>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((item) => (
                <JudgmentCard key={item.slug} item={item} />
              ))}
            </div>
            {data && (
              <HubPagination
                basePath="/judgments"
                currentPage={data.page}
                totalPages={data.total_pages}
                query={query}
              />
            )}
          </>
        )}
      </div>
    </LibraryPageShell>
  );
}
