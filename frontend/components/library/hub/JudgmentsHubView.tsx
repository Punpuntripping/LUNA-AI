import { LibraryPageShell } from "@/components/library/blocks";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { JudgmentCard } from "@/components/library/hub/JudgmentCard";
import { JudgmentsFilterBar } from "@/components/library/hub/JudgmentsFilterBar";
import { CourtSwitcher } from "@/components/library/hub/CourtSwitcher";
import { HubPagination } from "@/components/library/hub/HubPagination";
import { HubCtaWall } from "@/components/library/hub/HubCtaWall";
import {
  getJudgmentCourts,
  getJudgmentsHub,
  getSectorSlugMap,
  type JudgmentsFilters,
} from "@/lib/library/api";
import {
  courtHeading,
  courtLabel,
  courtNavItems,
  courtPath,
} from "@/lib/library/courts";
import { formatCount } from "@/lib/library/sectors";
import { toFilterQuery } from "@/lib/library/hub-query";
import type { BreadcrumbItem } from "@/types/library";

const WING_TITLE = "الأحكام القضائية السعودية";
const WING_DESCRIPTION =
  "تصفّح الأحكام الصادرة عن المحاكم السعودية — وقائعها وأسبابها ومنطوقها، والأنظمة التي استندت إليها.";

/**
 * Shared server-component body for the /judgments hub — page 1 (`/judgments`),
 * deep pages (`/judgments/page/{n}`) and, since the court sections shipped, both
 * halves of `/judgments/courts/{slug}` too. Mirrors CircularsHubView: CTA wall
 * on the anon depth cap, empty state on a null payload (no notFound — a hub must
 * survive an offline backend), else the 3×3 card grid + pagination.
 *
 * Adds the filter row the other wings don't have (درجة المحكمة chips + search).
 * The CHIP filters live entirely in the URL, so this stays a plain server
 * component; the active set is threaded into both the fetch AND the pagination
 * links so paging never silently drops a filter.
 *
 * ── «الجهة القضائية» — A SECTION, NOT A FILTER ──────────────────────────────
 * `filters.court` is one of the 12 Arabic court slugs (`lib/library/courts.ts`,
 * mirroring `shared/library/courts.py`). It is a CLOSED, server-owned vocabulary
 * and the backend keeps it out of its `filtered` flag, exactly as it does
 * `sector_slug` — which is what lets an anonymous reader page past the two-page
 * enumeration-oracle clamp inside a section.
 *
 * It reaches the wire by THREE paths, and all three are load-bearing:
 *
 *   1. `browseFilters` → the server-side browse fetch. Miss it and the section
 *      renders the unfiltered corpus.
 *   2. `fetchQuery` → `HubCtaWall`'s CLIENT-SIDE authed reveal. That call hits
 *      the wing endpoint (`/public/library/judgments?…`), which knows nothing
 *      about our path shape — so without `court` here a signed-in reader who
 *      pages past the anon cap silently drops back to the whole corpus. This is
 *      the single most likely bug in this wing.
 *   3. `basePath` → the pagination LINKS, where the court lives in the PATH
 *      (`/judgments/courts/{slug}/page/2`).
 *
 * That is also why `linkQuery` exists: the links must NOT repeat `?court=` on
 * top of a path that already says it (it would mint a second URL for one page —
 * the same reasoning `SectorTypeListView` records for `sector_slug`), while the
 * authed fetch cannot work without it.
 *
 * ⚠ `q` IS DELIBERATELY ABSENT FROM EVERYTHING BELOW (D9). This fetch is
 * anonymous and ISR-cached under a shared key, and the backend now ignores `q`
 * for an anonymous caller — so passing it could only mint one cache entry per
 * query string a visitor (or a crawler) ever typed, in exchange for an
 * identical unfiltered page. The live search runs client-side inside
 * `JudgmentsFilterBar` → `HubSearchPanel` with the reader's own bearer. The
 * route still READS `?q=` into `filters` and that is fine: the bar seeds the
 * box from the URL itself, so a shared search link still shows what was
 * searched for.
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
  // Already decoded and validated by the route (`normalizeCourtSlug` +
  // `isCourtSlug`); an unknown slug 404s there and never reaches the backend.
  const court = filters.court?.trim() ?? "";
  const activeLabel = court ? courtLabel(court) : null;

  // `legal_domains[]` IS the sector vocabulary, so the domain chips are sector
  // pills. `getSectorSlugMap` soft-fails to `{}` — no map, no links, still a
  // rendered hub. `getJudgmentCourts` soft-fails to `[]` the same way, and
  // `courtNavItems` then falls back to the local mirror's 12 links without
  // counts.
  const browseFilters: JudgmentsFilters = {
    court,
    court_level: filters.court_level,
    domain: filters.domain,
    sector_slug: filters.sector_slug,
  };
  const [data, sectorSlugs, courtRows] = await Promise.all([
    getJudgmentsHub(page, browseFilters),
    getSectorSlugMap(),
    getJudgmentCourts(),
  ]);
  const items = data?.items ?? [];
  const isCap = data?.cap_reached ?? false;
  // Unauthenticated + ISR-cached ⇒ always the ANON cap (PART 9 trap 2). The
  // caller's real cap is resolved client-side inside HubCtaWall.
  const anonMaxPage = data?.max_page ?? data?.max_anon_page ?? 1;
  const hasFilters = Boolean(filters.court_level || filters.domain);

  const courts = courtNavItems(courtRows);
  const activeCount = courtRows.find((row) => row.slug === court)?.count ?? null;

  const basePath = court ? courtPath(court) : "/judgments";
  // ⚠ TWO QUERIES, ON PURPOSE — see the «THREE paths» note above. `fetchQuery`
  // carries the court (the authed reveal calls the wing endpoint by query
  // param); `linkQuery` does not (the pagination path already says it).
  const fetchQuery = toFilterQuery({
    court,
    court_level: filters.court_level,
    domain: filters.domain,
  });
  const linkQuery = toFilterQuery({
    court_level: filters.court_level,
    domain: filters.domain,
  });

  const heading = activeLabel ? courtHeading(activeLabel) : WING_TITLE;
  const description = activeLabel
    ? `الأحكام الصادرة عن ${activeLabel} — وقائعها وأسبابها ومنطوقها، والأنظمة التي استندت إليها.`
    : WING_DESCRIPTION;

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    ...(activeLabel
      ? [
          { label: "الأحكام القضائية", href: "/judgments" },
          ...(page > 1
            ? [{ label: activeLabel, href: basePath }, { label: `صفحة ${page}` }]
            : [{ label: activeLabel }]),
        ]
      : page > 1
        ? [
            { label: "الأحكام القضائية", href: "/judgments" },
            { label: `صفحة ${page}` },
          ]
        : [{ label: "الأحكام القضائية" }]),
  ];

  return (
    <LibraryPageShell maxWidth="hub" showCta={!isCap}>
      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            {heading}
          </h1>
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            {description}
            {activeCount !== null &&
              activeCount > 0 &&
              ` — ${formatCount(activeCount)} حكم في هذه الجهة.`}
          </p>
        </header>

        <CourtSwitcher courts={courts} activeSlug={court || undefined} />

        <JudgmentsFilterBar
          filters={filters}
          sectorSlugs={sectorSlugs}
          basePath={basePath}
        >
          {isCap ? (
            // Filters ride the same query string into the authed reveal, so a
            // capped page never silently drops the reader's active filter set —
            // court included.
            <HubCtaWall
              section="judgments"
              basePath={basePath}
              page={page}
              totalPages={data?.total_pages ?? 0}
              anonMaxPage={anonMaxPage}
              query={fetchQuery}
              linkQuery={linkQuery}
              sectorSlugs={sectorSlugs}
            />
          ) : items.length === 0 ? (
            <p className="py-16 text-center text-sm text-muted-foreground">
              {hasFilters
                ? "لا توجد أحكام مطابقة لبحثك — جرّب تعديل الفلاتر."
                : activeLabel
                  ? // A REAL SCREEN, not a theoretical one: المحكمة العمالية has
                    // 35 judgments corpus-wide and several sections are thin.
                    `لا توجد أحكام متاحة في ${activeLabel} حالياً — تصفّح جهة قضائية أخرى من القائمة أعلاه.`
                  : "لا توجد أحكام لعرضها حالياً."}
            </p>
          ) : (
            <div className="space-y-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {items.map((item) => (
                  <JudgmentCard
                    key={item.slug}
                    item={item}
                    sectorSlugs={sectorSlugs}
                  />
                ))}
              </div>
              {data && (
                <HubPagination
                  basePath={basePath}
                  currentPage={data.page}
                  totalPages={data.total_pages}
                  query={linkQuery}
                />
              )}
            </div>
          )}
        </JudgmentsFilterBar>
      </div>
    </LibraryPageShell>
  );
}
