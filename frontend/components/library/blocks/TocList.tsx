import Link from "next/link";
import { ListTree, ChevronLeft, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { parseTocLabel } from "@/lib/library/toc";
import type { TocListProps } from "@/types/library";

/**
 * «محتويات النظام» — an anchor/href list of a document's فصول/مواد. Server
 * component: uses a native `<details>` so it's collapsible with zero client JS.
 * `level` indents nested entries via `ps-` (RTL start padding). The desktop
 * reading rail uses the richer, scroll-spied `TocRail`; this stays the
 * mobile-inline surface.
 *
 * MOBILE: document TOCs run long — sampled الأنظمة reach ~700 entries — so the
 * list is height-capped and scrolls inside its own box, and document pages pass
 * `defaultOpen={false}`. Expanded and uncapped, the TOC pushed the actual
 * article text hundreds of rows down the page on a phone. Links stay in the DOM
 * either way (a closed `<details>` is still crawlable), and the desktop
 * `TocRail` renders the same entries visibly.
 *
 * The `steps` variant (a numbered ordered list, for the compliance «الخطوات»
 * block) went with the compliance wing on 2026-08-03 — it had exactly one
 * caller, `/compliance/{slug}`.
 */
export function TocList({
  entries,
  title = "محتويات النظام",
  collapsible = true,
  defaultOpen = true,
  badge,
  className,
}: TocListProps) {
  const list = (
    <ul className="scrollbar-thin max-h-[60svh] space-y-0.5 overflow-y-auto overscroll-contain">
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
      open={defaultOpen}
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
