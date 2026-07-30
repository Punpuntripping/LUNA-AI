import Link from "next/link";
import { ListTree, ChevronLeft, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { parseTocLabel } from "@/lib/library/toc";
import type { TocListProps } from "@/types/library";

/**
 * «محتويات النظام» — an anchor/href list of a document's فصول/مواد, or a
 * numbered steps list (`variant="steps"`, for compliance «الخطوات»). Server
 * component: uses a native `<details open>` so it's collapsible on mobile with
 * zero client JS (defaults open on every viewport). `level` indents nested
 * entries via `ps-` (RTL start padding). The desktop reading rail uses the
 * richer, scroll-spied `TocRail`; this stays the mobile-inline + steps surface.
 */
export function TocList({
  entries,
  title = "محتويات النظام",
  variant = "anchors",
  collapsible = true,
  badge,
  className,
}: TocListProps) {
  const isSteps = variant === "steps";

  const list = isSteps ? (
    <ol className="space-y-2.5">
      {entries.map((entry, index) => (
        <li key={entry.id} className="flex items-start gap-3">
          <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold tabular-nums text-primary">
            {index + 1}
          </span>
          {entry.href && !entry.locked ? (
            <Link
              href={entry.href}
              className="pt-0.5 text-sm leading-relaxed text-foreground underline-offset-2 transition-colors hover:text-primary hover:underline"
            >
              {entry.label}
            </Link>
          ) : (
            <span className="pt-0.5 text-sm leading-relaxed text-text-secondary">
              {entry.label}
            </span>
          )}
        </li>
      ))}
    </ol>
  ) : (
    <ul className="space-y-0.5">
      {entries.map((entry) => {
        const locked = entry.locked || !entry.href;
        const { chip, text } = parseTocLabel(entry.label);
        return (
          <li
            key={entry.id}
            style={{ paddingInlineStart: `${((entry.level ?? 1) - 1) * 12}px` }}
          >
            {locked ? (
              <span className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-text-muted">
                <span className="flex-1 truncate">{text}</span>
                <Lock
                  aria-hidden="true"
                  className="h-3 w-3 shrink-0 text-text-subtle"
                />
                {/* Keep the مادة number on locked rows (matches TocRail). */}
                {chip && (
                  <span className="shrink-0 rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] font-semibold tabular-nums text-text-subtle">
                    {chip}
                  </span>
                )}
              </span>
            ) : (
              <Link
                href={entry.href!}
                className="group flex items-center gap-2 rounded-lg px-2.5 py-1.5 transition-colors hover:bg-accent-soft"
              >
                <span className="flex-1 truncate text-sm text-text-secondary transition-colors group-hover:text-primary">
                  {text}
                </span>
                {chip ? (
                  <span className="shrink-0 rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] font-semibold tabular-nums text-text-muted transition-colors group-hover:bg-accent-soft group-hover:text-primary">
                    {chip}
                  </span>
                ) : (
                  <ChevronLeft
                    aria-hidden="true"
                    className="h-3.5 w-3.5 shrink-0 text-text-subtle transition-colors group-hover:text-primary"
                  />
                )}
              </Link>
            )}
          </li>
        );
      })}
    </ul>
  );

  const heading = (
    <span className="flex items-center gap-2 text-sm font-bold text-foreground">
      <ListTree aria-hidden="true" className="h-4 w-4 shrink-0 text-primary" />
      {title}
    </span>
  );

  const badgePill = badge ? (
    <span className="shrink-0 rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-semibold text-primary">
      {badge}
    </span>
  ) : null;

  if (!collapsible) {
    return (
      <section
        dir="rtl"
        className={cn(
          "rounded-xl border border-border bg-card p-4 shadow-xs sm:p-5",
          className,
        )}
      >
        <div className="mb-3 flex items-center justify-between gap-3">
          {heading}
          {badgePill}
        </div>
        {list}
      </section>
    );
  }

  return (
    <details
      open
      dir="rtl"
      className={cn(
        "group rounded-xl border border-border bg-card p-4 shadow-xs sm:p-5",
        className,
      )}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
        <span className="flex items-center gap-2">
          {heading}
          {badgePill}
        </span>
        <ChevronLeft
          aria-hidden="true"
          className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:-rotate-90"
        />
      </summary>
      <div className="mt-3">{list}</div>
    </details>
  );
}
