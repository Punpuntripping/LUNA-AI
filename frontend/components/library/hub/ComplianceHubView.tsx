import { LibraryPageShell } from "@/components/library/blocks";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { ComplianceCard } from "@/components/library/hub/ComplianceCard";
import { EntitySwitcher } from "@/components/library/hub/EntitySwitcher";
import { HubPagination } from "@/components/library/hub/HubPagination";
import { HubCtaWall } from "@/components/library/hub/HubCtaWall";
import { HubSearchPanel } from "@/components/library/hub/HubSearchPanel";
import {
  getComplianceEntities,
  getComplianceHub,
  getSectorSlugMap,
} from "@/lib/library/api";
import {
  entityHeading,
  entityLabel,
  entityNavItems,
  entityPath,
} from "@/lib/library/entities";
import { LIBRARY_TYPE_META, formatCount } from "@/lib/library/sectors";
import { toFilterQuery } from "@/lib/library/hub-query";
import type { BreadcrumbItem } from "@/types/library";

/**
 * Shared server-component body for the /compliance hub — page 1 (`/compliance`),
 * deep pages (`/compliance/page/{n}`) and, since the entity sections shipped,
 * both halves of `/compliance/{entity}` too.
 *
 * WHAT THE GRID LISTS: `service_guides` — 533 guides to the most-used Saudi
 * government services, ordered most-used first. Each one is OUR OWN authored
 * rewrite of the issuing entity's official PDF user-guide, published in full and
 * ungated at `/compliance/{slug}`, with the entity's service page as the only
 * outbound link and the source PDF surfaced nowhere.
 *
 * It is NOT the wing retired on 2026-08-03. That one republished the `services`
 * corpus — الشروط / المستندات / الخطوات, the entity's own procedure text under
 * our chrome. A guide we wrote is ours to publish; a procedure they own is not.
 * Only guided services are listed; there is deliberately no fallback listing of
 * the other ~4,400 services, because «دليل مبسط لأكثر الخدمات استخداماً» IS the
 * offer.
 *
 * ── «الجهة» — A SECTION, AND AN ANON-VISIBLE ONE ────────────────────────────
 * `entitySlug` is one of the 28 Latin entity slugs (`lib/library/entities.ts`,
 * mirroring `shared/library/entities.py`). Like `sector_slug` and the judgments
 * `court` it is a CLOSED, server-owned vocabulary the backend keeps out of its
 * `filtered` flag — which is what lets an anonymous reader see the section's
 * REAL `total_pages` instead of the two-page enumeration-oracle clamp.
 *
 * ⚠ AND UNLIKE THOSE TWO, IT IS NOT A PAID SECTION (plan D1). No `sectionScope`
 * is passed to `HubCtaWall` below, because the backend does not pass
 * `entity_slug` into `section_scope_allowed()`: this wing is 100% published and
 * ungated end to end, every one of its 533 guide URLs is already in the sitemap,
 * and the guides are our own text — so a section slice here accumulates no
 * withheld content and opens no enumeration path that the sitemap has not opened
 * already. What still applies, per URL, is the anon DEPTH cap: page 1 of an
 * entity is anon-readable, page 2 walls exactly as `/compliance/page/2` does.
 *
 * The slug reaches the wire by three paths, all load-bearing (the
 * `JudgmentsHubView` lesson):
 *
 *   1. `browseFilters` → the server-side browse fetch. Miss it and the section
 *      renders the unfiltered corpus.
 *   2. `fetchQuery` → `HubCtaWall`'s CLIENT-SIDE authed reveal, which calls the
 *      wing endpoint by query param and knows nothing about our path shape.
 *      Miss it and a signed-in reader paging past the anon cap silently drops
 *      back to all 533 guides.
 *   3. `basePath` → the pagination LINKS, where the entity lives in the PATH
 *      (`/compliance/{entity}/page/2`).
 *
 * That is also why `linkQuery=""` is explicit: the revealed links must NOT
 * repeat `?entity_slug=` on top of a path that already says it, or one page
 * would have two URLs.
 *
 * `verifiedBot` is the §3.7 crawler exemption, set by the DEEP-page routes only
 * (`app/compliance/page/[n]` and `app/compliance/[slug]/page/[n]`) — page 1 must
 * stay statically prerendered.
 */
export async function ComplianceHubView({
  page,
  entitySlug,
  verifiedBot,
}: {
  page: number;
  /** One of the 28 entity slugs. Absent ⇒ the unfiltered /compliance hub. */
  entitySlug?: string;
  verifiedBot?: boolean;
}) {
  // Already validated by the route (`isEntitySlug`); an unknown slug never gets
  // here — it either resolved as a guide or 404'd. So a label is guaranteed for
  // any non-empty `entitySlug`, and the `?? null` is belt-and-braces.
  const entity = entitySlug?.trim() ?? "";
  const activeLabel = entity ? entityLabel(entity) : null;

  // `getSectorSlugMap` soft-fails to `{}` — a missing map costs the sector
  // pills their links, never the hub its render. `getComplianceEntities` soft-
  // fails to `[]` the same way, and `entityNavItems` then falls back to the
  // local mirror's 28 links without counts.
  const [data, sectorSlugs, entityRows] = await Promise.all([
    getComplianceHub(page, entity ? { entity_slug: entity } : undefined, {
      verifiedBot,
    }),
    getSectorSlugMap(),
    getComplianceEntities(),
  ]);
  const items = data?.items ?? [];
  const isCap = data?.cap_reached ?? false;
  // Unauthenticated + ISR-cached ⇒ always the ANON cap (PART 9 trap 2). The
  // caller's real cap is resolved client-side inside HubCtaWall.
  const anonMaxPage = data?.max_page ?? data?.max_anon_page ?? 1;

  const entities = entityNavItems(entityRows);
  const activeCount =
    entityRows.find((row) => row.slug === entity)?.count ?? null;

  const basePath = entity ? entityPath(entity) : "/compliance";
  // See the «three paths» note: the authed reveal needs the entity as a QUERY
  // param; the links must not carry it, because the path already does.
  const fetchQuery = toFilterQuery({ entity_slug: entity });

  const heading = activeLabel
    ? entityHeading(activeLabel)
    : LIBRARY_TYPE_META.compliance.longLabel;
  const description = activeLabel
    ? `أدلة الخدمات التي تقدّمها ${activeLabel} — خطوات كل خدمة وأين تُنجز على موقع الجهة الرسمي.`
    : LIBRARY_TYPE_META.compliance.description;

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    ...(activeLabel
      ? [
          { label: "دليل الخدمات", href: "/compliance" },
          ...(page > 1
            ? [{ label: activeLabel, href: basePath }, { label: `صفحة ${page}` }]
            : [{ label: activeLabel }]),
        ]
      : page > 1
        ? [
            { label: "دليل الخدمات", href: "/compliance" },
            { label: `صفحة ${page}` },
          ]
        : [{ label: "دليل الخدمات" }]),
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
              ` — ${formatCount(activeCount)} دليل خدمة من هذه الجهة.`}
          </p>
        </header>

        {/* The second browse axis, in the SSR HTML on every page of this wing:
            the unfiltered hub, its deep pages, and every entity section. */}
        <EntitySwitcher entities={entities} activeSlug={entity || undefined} />

        {/* THE SEARCH BOX IS BACK (2026-08-23). It was deliberately absent while
            the guides were not in `search_index` — a live-looking box that can
            only ever return nothing is worse than no box — and the old comment
            here said to add it back WITH the corpus. The `compliance` corpus
            shipped in this same change, so the box now runs BM25 over the guide
            bodies. See `HubSearchPanel` for why the query runs client-side and
            why `q` never reaches the ISR-cached fetch above.

            `filters` carries the entity so a search INSIDE a section stays
            inside it — searching within an active section is what a reader
            expects from a control rendered under that section's own H1. */}
        <HubSearchPanel
          section="compliance"
          sectorSlugs={sectorSlugs}
          filters={entity ? { entity_slug: entity } : undefined}
        >
          {isCap ? (
            // No `sectionScope`: the entity axis is not a paid section (D1), so
            // this is the ordinary DEPTH wall — «سجّل مجاناً» / «باقتك الحالية»
            // is exactly what happened, and a paid reader reaches the cards on
            // this very path.
            <HubCtaWall
              section="compliance"
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
              {activeLabel
                ? // Reachable in principle only while the corpus is mid-ingest:
                  // all 28 sections carry at least one guide today.
                  `لا توجد أدلة خدمات من ${activeLabel} حالياً — تصفّح جهة أخرى من القائمة أعلاه.`
                : LIBRARY_TYPE_META.compliance.empty}
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
                  basePath={basePath}
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
