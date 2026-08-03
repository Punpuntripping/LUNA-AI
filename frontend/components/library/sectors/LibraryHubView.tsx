import { LibraryBig } from "lucide-react";
import { ShelfLink } from "@/components/library/sectors/ShelfLink";
import { SitePageShell } from "@/components/site/SitePageShell";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { LibraryTypeChips } from "@/components/library/sectors/LibraryTypeChips";
import { SectorBrowseGrid } from "@/components/library/sectors/SectorBrowseGrid";
import { SectorPreviewStrip } from "@/components/library/sectors/SectorPreviewStrip";
import { LibrarySearchPanel } from "@/components/library/search/LibrarySearchPanel";
import {
  getCircularsHub,
  getJudgmentsHub,
  getLibraryCounts,
  getRegulationsHub,
  getSectorSlugMap,
  getSectors,
} from "@/lib/library/api";
import { LIBRARY_TYPE_META } from "@/lib/library/sectors";
import type { BreadcrumbItem } from "@/types/library";

/**
 * `/library` — «المكتبة القانونية», the unified public hub and the public
 * mirror of «مكتبتي» (§8.1). Replaces the `ComingSoonHub` placeholder, and
 * drops its `robots: noindex` (D1): this is a nav hub like every other one.
 *
 * ⚠ SERVER COMPONENT, NO `searchParams`, NO `headers()`, NO `force-dynamic`.
 * The route must come out of `next build` STATIC (§12.8) — reading either of
 * those APIs opts the segment out of static generation, and the hub is the
 * anon-serving surface for the whole library.
 *
 * ⚠ FOUR PREVIEW STRIPS, NOT A MERGED PAGINATED FEED — and NOT a
 * `/library/page/{n}`. The plan's §4 sketched one, but §9 then established that
 * the four corpora share no sortable column (الأنظمة have `clean_title` and a
 * 29%-populated `start_date`; الأحكام have a date but derived titles; التعاميم
 * have NO date column at all), so a merged cross-corpus feed has no defensible
 * ordering. And a per-type `/library/{type}` route would collide with
 * `/library/{sector}` — two dynamic patterns at one path level. So each tab
 * chip points at the wing hub that ALREADY owns unfiltered deep pagination and
 * is already indexed. Full pagination lives on the sector×type routes only.
 *
 * ⚠ AND IT IS ALSO THE CROSS-WING SEARCH PAGE (bm25_navigation_search.md D5).
 * Two plans claim this route; they COEXIST rather than replace one another. D5
 * was written against the `ComingSoonHub` placeholder this file already
 * replaced, and D9 makes search registered-only — so a `/library` that were
 * nothing but a search box would be an empty page for every anonymous visitor
 * and every crawler, on the one route carrying the sole crawlable path into the
 * 38 sector pages. Search is therefore the TOP AFFORDANCE ABOVE the browsable
 * hub: `LibrarySearchPanel` renders the box, and swaps this body out only while
 * a search is actually running. Everything inside it is unchanged, still
 * server-rendered, and still in the SSR HTML a crawler reads.
 */
export async function LibraryHubView() {
  // One await for the lot: the hub-page-1 fetches share their Data Cache
  // entries with the wing hubs themselves (same URL, same init), so this costs
  // the backend nothing the library was not already paying.
  const [countsPayload, sectors, sectorSlugs, regulations, judgments, circulars] =
    await Promise.all([
      getLibraryCounts(),
      getSectors(),
      getSectorSlugMap(),
      getRegulationsHub(1),
      getJudgmentsHub(1),
      getCircularsHub(1),
    ]);

  // Null counts = the backend has not shipped `/public/library` yet (or is
  // down). Chips still render, just without their numbers — never a 5xx and
  // never an error boundary on an indexable page.
  const counts = countsPayload?.counts;

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "المكتبة القانونية" },
  ];

  return (
    <SitePageShell>
      <main
        dir="rtl"
        className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-10"
      >
        <div className="space-y-8">
          <div className="space-y-5">
            <TopicBreadcrumbs items={crumbs} />

            <header className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
                  <LibraryBig
                    aria-hidden="true"
                    className="h-6 w-6 shrink-0 text-primary"
                  />
                  المكتبة القانونية السعودية
                </h1>

                {/* /library ⇄ مكتبتي. Client-only and authed-only BY DESIGN —
                    see `ShelfLink`: the shelf must never enter this page's
                    crawl skeleton, and a signed-out reader must not be handed a
                    link that can only 401. */}
                <ShelfLink />
              </div>
              <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
                الأنظمة واللوائح، والأحكام القضائية، والتعاميم التنظيمية — مصدر
                رسمي واحد لكل وثيقة، مرتّبة حسب القطاع.
              </p>
            </header>
          </div>

          {/* Search first, browse underneath — and the browsable hub is passed
              IN as children so this stays a server component. `LibraryTypeChips`
              moved inside it deliberately: while a cross-wing search is live the
              panel shows its own أنظمة/أحكام/تعاميم scope chips, and two chip
              rows naming the same three wings — one navigating, one filtering —
              is a UI that has to be read twice to be understood. */}
          <LibrarySearchPanel sectorSlugs={sectorSlugs}>
            <div className="space-y-8">
              <LibraryTypeChips counts={counts ?? {}} />

              {/* The crawl skeleton for the sector axis — 38 server-rendered
                  links. Placed high on purpose: it is the page's real
                  navigation, not a footer afterthought. */}
              {sectors.length > 0 && (
                <section className="space-y-3">
                  <div className="space-y-1">
                    <h2 className="text-lg font-bold tracking-tight text-foreground">
                      تصفّح حسب القطاع
                    </h2>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      اختر القطاع الذي يعنيك لترى أنظمته وأحكامه وتعاميمه في
                      مكان واحد.
                    </p>
                  </div>
                  <SectorBrowseGrid sectors={sectors} />
                </section>
              )}

              <SectorPreviewStrip
                type="regulations"
                items={regulations?.items ?? []}
                count={counts?.regulations ?? 0}
                href={LIBRARY_TYPE_META.regulations.wingPath}
                sectorSlugs={sectorSlugs}
              />
              <SectorPreviewStrip
                type="judgments"
                items={judgments?.items ?? []}
                count={counts?.judgments ?? 0}
                href={LIBRARY_TYPE_META.judgments.wingPath}
                sectorSlugs={sectorSlugs}
              />
              <SectorPreviewStrip
                type="circulars"
                items={circulars?.items ?? []}
                count={counts?.circulars ?? 0}
                href={LIBRARY_TYPE_META.circulars.wingPath}
                sectorSlugs={sectorSlugs}
              />
            </div>
          </LibrarySearchPanel>
        </div>
      </main>
    </SitePageShell>
  );
}
