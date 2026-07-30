import { FileText } from "lucide-react";
import { LibraryPageShell } from "@/components/library/blocks";
import { TopicBreadcrumbs } from "@/components/library/blocks/TopicBreadcrumbs";
import { FormCard } from "@/components/library/hub/FormCard";
import { HubPagination } from "@/components/library/hub/HubPagination";
import { HubCtaWall } from "@/components/library/hub/HubCtaWall";
import { getFormsHub } from "@/lib/library/api";
import type { BreadcrumbItem } from "@/types/library";

/**
 * Shared server-component body for the /forms hub — page 1 (`/forms`) and deep
 * pages (`/forms/page/{n}`). PUBLISHED forms only — the hub is EMPTY until a
 * human reviewer approves + publishes a drafted form, so the empty state is a
 * first-class «قريباً» surface (not an error). Otherwise mirrors the other hubs:
 * CTA wall on the anon cap, else the 3×3 card grid + pagination.
 *
 * `verifiedBot` is the §3.7 crawler exemption, set by the DEEP-page route only
 * (`app/forms/page/[n]`) — page 1 must stay statically prerendered.
 */
export async function FormsHubView({
  page,
  verifiedBot,
}: {
  page: number;
  verifiedBot?: boolean;
}) {
  const data = await getFormsHub(page, undefined, { verifiedBot });
  const items = data?.items ?? [];
  const isCap = data?.cap_reached ?? false;
  // Unauthenticated + ISR-cached ⇒ always the ANON cap (PART 9 trap 2). The
  // caller's real cap is resolved client-side inside HubCtaWall.
  const anonMaxPage = data?.max_page ?? data?.max_anon_page ?? 1;

  const crumbs: BreadcrumbItem[] =
    page > 1
      ? [
          { label: "الرئيسية", href: "/" },
          { label: "النماذج", href: "/forms" },
          { label: `صفحة ${page}` },
        ]
      : [{ label: "الرئيسية", href: "/" }, { label: "النماذج" }];

  return (
    <LibraryPageShell maxWidth="hub" showCta={!isCap}>
      <div className="space-y-6">
        <TopicBreadcrumbs items={crumbs} />

        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground sm:text-3xl">
            النماذج القانونية الجاهزة
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            صيغ ونماذج قانونية جاهزة — متى تُستخدم، وأساسها النظامي، وفتحها مباشرة
            في ريحان لتعديلها.
          </p>
        </header>

        {isCap ? (
          <HubCtaWall
            section="forms"
            basePath="/forms"
            page={page}
            totalPages={data?.total_pages ?? 0}
            anonMaxPage={anonMaxPage}
          />
        ) : items.length === 0 ? (
          <div
            dir="rtl"
            className="mx-auto max-w-lg rounded-2xl border border-dashed border-border bg-card/60 px-6 py-14 text-center"
          >
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
              <FileText aria-hidden="true" className="h-7 w-7" />
            </div>
            <h2 className="text-lg font-bold text-foreground">
              قريباً — نماذج قانونية جاهزة
            </h2>
            <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
              نعمل على إعداد مجموعة من الصيغ والنماذج القانونية المراجَعة. عد
              قريباً للاطّلاع عليها وفتحها مباشرة في ريحان.
            </p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((item) => (
                <FormCard key={item.slug} item={item} />
              ))}
            </div>
            {data && (
              <HubPagination
                basePath="/forms"
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
