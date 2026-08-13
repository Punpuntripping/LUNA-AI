import Link from "next/link";
import { ChevronLeft, Link2, Scale } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CitedRegulationsProps } from "@/types/library";

/**
 * «الأنظمة المستند إليها» — the judgment→regulation citation mesh, and the
 * single highest-value SEO block on a judgment page: it is what turns 30k
 * isolated rulings into inbound internal links on the /regulations corpus.
 *
 * WHY NOT `ReferencesMesh`: that block renders each row as ONE `<Link>` wrapping
 * the whole row. A citation row here needs TWO independent targets — the نظام
 * (`/regulations/{reg_slug}`) and, when the citation is مادة-level, «المادة {n}»
 * (`/regulations/{reg_slug}/{article_slug}`) — and a third state with NO target
 * at all, for a نظام whose page isn't published yet (plain text, never a dead
 * link). Nested anchors are invalid HTML, so the row could not be expressed
 * inside ReferencesMesh without changing its contract for the wings already
 * using it (ReadAfter shares its `ReferenceItem` type and dereferences `href`
 * unconditionally). The chrome below deliberately matches ReferencesMesh's rows
 * so the two blocks still read as one system.
 *
 * `total` is the document's `cited_total`: the surplus over the rendered items
 * is the gated tail («+{n} … سجّل للعرض»). Server component — links only.
 *
 * Arabic slugs are interpolated RAW into `href` (the browser encodes on
 * navigation) — the same convention as every sibling card/link in the library.
 * Do NOT pre-encode here: a percent-encoded href would double-encode.
 */
export function CitedRegulations({
  items,
  total,
  title = "الأنظمة المستند إليها",
  gateCtaHref = "/login",
  className,
}: CitedRegulationsProps) {
  const gatedCount = Math.max(0, (total ?? items.length) - items.length);
  if (items.length === 0 && gatedCount === 0) return null;

  return (
    <section dir="rtl" className={cn("w-full", className)}>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-bold text-foreground">
        <Link2 aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
        {title}
      </h2>

      <ul className="space-y-2">
        {items.map((item, index) => {
          const hasReg = Boolean(item.reg_slug);
          const hasArticle = Boolean(item.reg_slug && item.article_slug);

          return (
            <li
              key={`${item.reg_slug ?? "unlinked"}-${item.article_slug ?? index}`}
              className={cn(
                "flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2.5 transition-colors",
                hasReg && "hover:border-primary/40",
              )}
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                <Scale aria-hidden="true" className="h-4 w-4" />
              </span>

              {/* The نظام. Linked when it has a published page; otherwise plain
                  text — an unpublished citation still carries legal meaning for
                  the reader, it just has nowhere to point. */}
              {hasReg ? (
                <Link
                  href={`/regulations/${item.reg_slug}`}
                  className="flex-1 text-sm font-medium text-foreground transition-colors hover:text-primary"
                >
                  {item.title}
                </Link>
              ) : (
                <span className="flex-1 text-sm font-medium text-text-secondary">
                  {item.title}
                </span>
              )}

              {/* The مادة deep-link — the actual ranking payload: a judgment
                  citing المادة N is exactly the corroboration that مادة page
                  wants pointing at it. */}
              {hasArticle && (
                <Link
                  href={`/regulations/${item.reg_slug}/${item.article_slug}`}
                  className="shrink-0 rounded-full bg-pill px-2.5 py-0.5 text-xs font-medium text-pill-fg transition-colors hover:bg-accent-soft hover:text-primary"
                >
                  المادة {item.article_no}
                </Link>
              )}
            </li>
          );
        })}

        {gatedCount > 0 && (
          <li>
            <Link
              href={gateCtaHref}
              className="group flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-border px-3 py-2.5 text-sm text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary"
            >
              +{gatedCount} مرجعًا نظاميًا آخر — سجّل للعرض
              <ChevronLeft
                aria-hidden="true"
                className="h-4 w-4 shrink-0 transition-transform group-hover:-translate-x-0.5"
              />
            </Link>
          </li>
        )}
      </ul>
    </section>
  );
}
