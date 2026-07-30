import Link from "next/link";
import { Clock, Info } from "lucide-react";
import { cn } from "@/lib/utils";
import { LEGAL_ROUTES } from "@/lib/legal";
import type { TrustLineProps } from "@/types/library";

/**
 * Format an ISO date as an Arabic Gregorian long date («٢٢ يوليو ٢٠٢٦»),
 * matching the app-wide `toLocaleDateString("ar-SA")` convention. Falls back to
 * the raw string on an unparseable input so we never render «Invalid Date».
 */
function formatArabicDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("ar-SA", {
    year: "numeric",
    month: "long",
    day: "numeric",
    calendar: "gregory",
  }).format(date);
}

/**
 * The subtle E-E-A-T trust line under a page H1: «آخر تحديث {date}» · issuing
 * entity · AI-disclaimer link. One quiet muted line — server component, links
 * only. Dot separators collapse gracefully when `entity` is absent.
 */
export function TrustLine({
  updatedAt,
  entity,
  disclaimerHref = LEGAL_ROUTES.terms,
  className,
}: TrustLineProps) {
  return (
    <p
      dir="rtl"
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground",
        className,
      )}
    >
      <span className="inline-flex items-center gap-1">
        <Clock aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        آخر تحديث{" "}
        <time dateTime={updatedAt}>{formatArabicDate(updatedAt)}</time>
      </span>

      {entity && (
        <>
          <span aria-hidden="true" className="text-text-subtle">
            ·
          </span>
          <span>{entity}</span>
        </>
      )}

      <span aria-hidden="true" className="text-text-subtle">
        ·
      </span>
      <Link
        href={disclaimerHref}
        className="inline-flex items-center gap-1 underline-offset-2 transition-colors hover:text-foreground hover:underline"
      >
        <Info aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        إخلاء المسؤولية
      </Link>
    </p>
  );
}
