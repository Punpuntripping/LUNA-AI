import Link from "next/link";
import { ChevronLeft, Link2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  ReferenceKindIcon,
  REFERENCE_KIND_LABEL,
} from "@/components/library/blocks/referenceKind";
import type { ReferencesMeshProps } from "@/types/library";

/**
 * «استند إلى / مراجع» — the cross-reference mesh (cited مواد، judgments applying
 * a نظام، related circulars). Each row shows the kind icon + a short kind chip +
 * an internal link. An optional gated tail («+{n} مراجع أخرى — سجّل للعرض»)
 * links to the signup CTA. Server component.
 */
export function ReferencesMesh({
  items,
  title = "استند إلى",
  gatedCount,
  gateCtaHref = "/login",
  className,
}: ReferencesMeshProps) {
  if (items.length === 0 && !gatedCount) return null;

  return (
    <section dir="rtl" className={cn("w-full", className)}>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-bold text-foreground">
        <Link2 aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
        {title}
      </h2>

      <ul className="space-y-2">
        {items.map((item, index) => (
          <li key={`${item.href}-${index}`}>
            <Link
              href={item.href}
              className="group flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-2.5 transition-colors hover:border-primary/40 hover:bg-muted/40"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                <ReferenceKindIcon kind={item.kind} className="h-4 w-4" />
              </span>
              <span className="flex-1 text-sm font-medium text-foreground group-hover:text-primary">
                {item.title}
              </span>
              <span className="shrink-0 rounded-full bg-pill px-2 py-0.5 text-[11px] font-medium text-pill-fg">
                {REFERENCE_KIND_LABEL[item.kind]}
              </span>
            </Link>
          </li>
        ))}

        {gatedCount && gatedCount > 0 && (
          <li>
            <Link
              href={gateCtaHref}
              className="group flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-border px-3 py-2.5 text-sm text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary"
            >
              +{gatedCount} مراجع أخرى — سجّل للعرض
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
