import { notFound } from "next/navigation";
import { SitePageShell } from "@/components/site/SitePageShell";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { LibraryTypeChips } from "@/components/library/sectors/LibraryTypeChips";
import { SectorPreviewStrip } from "@/components/library/sectors/SectorPreviewStrip";
import { SectorSwitcher } from "@/components/library/sectors/SectorSwitcher";
import { getSectorDetail, getSectorSlugMap, getSectors } from "@/lib/library/api";
import {
  LIBRARY_TYPES,
  formatCount,
  isReservedSectorSlug,
  sectorHeading,
  sectorTypePath,
} from "@/lib/library/sectors";
import type { BreadcrumbItem } from "@/types/library";

/**
 * `/library/{sector}` — one sector's overview: real counts plus a ≤3-item strip
 * of each of the four types (§8.3).
 *
 * ⚠ THE DISPLAY NAME IS ALWAYS `name_ar` (D6). The Latin slug is a URL segment
 * and nothing else — it must never surface as heading, title or chip text. The
 * slug being Latin (D4/D5) is what keeps `/library/labor-employment/regulations`
 * from wedging one Arabic segment between two Latin ones; the Arabic SEO weight
 * lives in the H1 and the `<title>`, which is where it belongs.
 *
 * `null` detail ⇒ unknown slug ⇒ 404. `fetchJson` only returns `null` on a real
 * 404/400 (a transient failure THROWS), so this can never 404 a live page
 * because the backend hiccuped.
 */
export async function SectorOverviewView({ slug }: { slug: string }) {
  // `mine` / `page` are reserved (T2). Next already resolves the static
  // `app/library/mine` segment ahead of this dynamic one, so this is the second
  // lock on that door — and it costs one Set lookup, before any fetch.
  if (isReservedSectorSlug(slug)) notFound();

  const [detail, sectors, sectorSlugs] = await Promise.all([
    getSectorDetail(slug),
    getSectors(),
    getSectorSlugMap(),
  ]);

  if (!detail) notFound();

  const crumbs: BreadcrumbItem[] = [
    { label: "الرئيسية", href: "/" },
    { label: "المكتبة القانونية", href: "/library" },
    { label: detail.name_ar },
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
              <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
                {sectorHeading(detail.name_ar)}
              </h1>
              <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
                {formatCount(detail.counts.total)} مصدر قانوني في قطاع{" "}
                {detail.name_ar} — أنظمته ولوائحه، وأحكامه القضائية، وخدماته
                الحكومية، وتعاميمه التنظيمية.
              </p>
            </header>

            <LibraryTypeChips counts={detail.counts} sectorSlug={detail.slug} />

            <SectorSwitcher sectors={sectors} activeSlug={detail.slug} />
          </div>

          {LIBRARY_TYPES.map((type) => (
            <SectorPreviewStrip
              key={type}
              type={type}
              items={detail.preview[type] ?? []}
              count={detail.counts[type] ?? 0}
              href={sectorTypePath(detail.slug, type)}
              sectorSlugs={sectorSlugs}
            />
          ))}
        </div>
      </main>
    </SitePageShell>
  );
}
