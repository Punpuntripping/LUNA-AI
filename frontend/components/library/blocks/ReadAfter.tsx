import Link from "next/link";
import { BookMarked } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  ReferenceKindIcon,
  REFERENCE_KIND_LABEL,
} from "@/components/library/blocks/referenceKind";
import type { ReadAfterProps } from "@/types/library";

/**
 * «اقرأ أيضاً» — an engagement card row of related reading (distinct from the
 * ReferencesMesh cited sources). Each card = title + kind chip. Server
 * component. Responsive grid: 1 col mobile → 2 cols desktop.
 */
export function ReadAfter({
  items,
  title = "اقرأ أيضاً",
  className,
}: ReadAfterProps) {
  if (items.length === 0) return null;

  return (
    <section dir="rtl" className={cn("w-full", className)}>
      <h2 className="mb-3 flex items-center gap-2 text-sm font-bold text-foreground">
        <BookMarked aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
        {title}
      </h2>

      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {items.map((item, index) => (
          <li key={`${item.href}-${index}`}>
            <Link
              href={item.href}
              className="group flex h-full flex-col justify-between gap-3 rounded-xl border border-border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-muted/40"
            >
              <span className="flex items-start gap-2.5">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                  <ReferenceKindIcon kind={item.kind} className="h-4 w-4" />
                </span>
                <span className="text-sm font-semibold leading-relaxed text-foreground group-hover:text-primary">
                  {item.title}
                </span>
              </span>
              <span className="inline-flex w-fit rounded-full bg-pill px-2 py-0.5 text-[11px] font-medium text-pill-fg">
                {REFERENCE_KIND_LABEL[item.kind]}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
