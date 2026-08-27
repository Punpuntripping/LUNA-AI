import { Fragment, type ReactNode } from "react";
import Link from "next/link";
import { Clock, Info } from "lucide-react";
import { cn } from "@/lib/utils";
import { LEGAL_ROUTES } from "@/lib/legal";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";
import type { TrustLineProps } from "@/types/library";

/**
 * Format an ISO date as an Arabic Gregorian long date («22 يوليو 2026»),
 * matching the app-wide `AR_DATE_LOCALE` convention. Falls back to the raw
 * string on an unparseable input so we never render «Invalid Date».
 */
function formatArabicDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(AR_DATE_LOCALE, {
    year: "numeric",
    month: "long",
    day: "numeric",
    calendar: "gregory",
  }).format(date);
}

/**
 * The subtle E-E-A-T trust line under a page H1: «آخر تحديث {date}» · issuing
 * entity · AI-disclaimer link. One quiet muted line — server component, links
 * only.
 *
 * Parts are assembled into a list and joined with «·» rather than each carrying
 * its own leading separator, so the dots collapse correctly whichever parts are
 * absent. `updatedAt` and `entity` are both optional; the disclaimer link is the
 * only part always present.
 */
export function TrustLine({
  updatedAt,
  entity,
  disclaimerHref = LEGAL_ROUTES.terms,
  className,
}: TrustLineProps) {
  const parts: Array<{ key: string; node: ReactNode }> = [];

  if (updatedAt) {
    parts.push({
      key: "updated",
      node: (
        <span className="inline-flex items-center gap-1">
          <Clock aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          آخر تحديث{" "}
          <time dateTime={updatedAt}>{formatArabicDate(updatedAt)}</time>
        </span>
      ),
    });
  }

  if (entity) {
    parts.push({ key: "entity", node: <span>{entity}</span> });
  }

  parts.push({
    key: "disclaimer",
    node: (
      <Link
        href={disclaimerHref}
        className="inline-flex items-center gap-1 underline-offset-2 transition-colors hover:text-foreground hover:underline"
      >
        <Info aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        إخلاء المسؤولية
      </Link>
    ),
  });

  return (
    <p
      dir="rtl"
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground",
        className,
      )}
    >
      {parts.map((part, index) => (
        <Fragment key={part.key}>
          {index > 0 && (
            <span aria-hidden="true" className="text-text-subtle">
              ·
            </span>
          )}
          {part.node}
        </Fragment>
      ))}
    </p>
  );
}
