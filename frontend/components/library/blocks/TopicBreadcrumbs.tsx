import Link from "next/link";
import { ChevronLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { JsonLd } from "@/components/seo/JsonLd";
import { buildBreadcrumbList } from "@/lib/seo/schema";
import type { TopicBreadcrumbsProps } from "@/types/library";

/**
 * Breadcrumb trail (root → current page) with optional topic chips beneath.
 * Server component. Emits a `BreadcrumbList` JSON-LD node built from the crumbs
 * that carry an `href` (the current page usually omits one). RTL: the trail
 * reads right → left, the `ChevronLeft` separator points toward the next crumb.
 */
export function TopicBreadcrumbs({
  items,
  chips,
  className,
}: TopicBreadcrumbsProps) {
  const linked = items.filter((item) => item.href);
  const jsonLd =
    linked.length > 0
      ? buildBreadcrumbList(
          linked.map((item) => ({ name: item.label, url: item.href! })),
        )
      : null;

  return (
    <nav aria-label="مسار التنقّل" dir="rtl" className={cn("w-full", className)}>
      {jsonLd && <JsonLd data={jsonLd} />}

      <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
        {items.map((item, index) => {
          const isLast = index === items.length - 1;
          return (
            <li key={`${item.label}-${index}`} className="flex items-center gap-x-1.5">
              {item.href && !isLast ? (
                <Link
                  href={item.href}
                  className="transition-colors hover:text-foreground"
                >
                  {item.label}
                </Link>
              ) : (
                <span
                  className={cn(isLast && "font-medium text-foreground")}
                  aria-current={isLast ? "page" : undefined}
                >
                  {item.label}
                </span>
              )}
              {!isLast && (
                <ChevronLeft
                  aria-hidden="true"
                  className="h-3.5 w-3.5 shrink-0 text-text-subtle"
                />
              )}
            </li>
          );
        })}
      </ol>

      {chips && chips.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {chips.map((chip) => (
            <li key={chip.href}>
              <Link
                href={chip.href}
                className="inline-flex items-center rounded-full bg-pill px-2.5 py-0.5 text-xs font-medium text-pill-fg transition-colors hover:bg-accent-soft hover:text-accent-brand"
              >
                {chip.label}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
