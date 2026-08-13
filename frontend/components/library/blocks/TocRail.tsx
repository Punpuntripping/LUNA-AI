"use client";

import Link from "next/link";
import { ListTree, ChevronLeft, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { parseTocLabel } from "@/lib/library/toc";
import { useTocScrollspy } from "@/hooks/use-toc-scrollspy";
import type { TocRailProps } from "@/types/library";

/**
 * «محتويات النظام» — the premium desktop reference-book rail (RTL grid column 2,
 * i.e. the physical LEFT side). A contained card with a fixed header (title +
 * count badge), an internally-scrolling list with top/bottom fade masks, and
 * compact article rows: the word on the start side, the مادة number as a
 * page-number chip on the end side.
 *
 * SCROLLSPY: entries whose href is a same-page `#sec-{id}` anchor (the gated
 * chunk-fallback TOC) get an IntersectionObserver-driven active state. Entries
 * that link out to مادة pages (the common article-index case) never need a spy —
 * they simply navigate. Client component; degrades to a plain rail when no
 * `#sec-` targets exist in the DOM.
 *
 * The spy and the missing-anchor click fallback live in `useTocScrollspy` —
 * shared verbatim with the phone `TocFloating` widget so both surfaces mark the
 * same مادة active and both land a gated row on the signup gate.
 */
export function TocRail({
  entries,
  title = "محتويات النظام",
  badge,
  className,
}: TocRailProps) {
  const { activeId, handleAnchorClick } = useTocScrollspy(entries);

  return (
    <div
      dir="rtl"
      className={cn(
        "flex max-h-[calc(100dvh-8rem)] flex-col overflow-hidden rounded-xl border border-border bg-card shadow-xs",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border px-3.5 py-3">
        <span className="flex items-center gap-2 text-sm font-bold text-foreground">
          <ListTree
            aria-hidden="true"
            className="h-4 w-4 shrink-0 text-primary"
          />
          {title}
        </span>
        {badge && (
          <span className="shrink-0 rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-primary">
            {badge}
          </span>
        )}
      </div>

      <div className="relative min-h-0 flex-1">
        {/* Fade masks — content dissolves into the panel edges as it scrolls. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 top-0 z-10 h-5 bg-gradient-to-b from-card to-transparent"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-5 bg-gradient-to-t from-card to-transparent"
        />

        {/* The list is its OWN viewport-bounded scroll container (explicit
            max-h, not h-full): flex-1 + max-h on the ancestors never actually
            constrained it (Chrome max-height/flex quirk), so the list silently
            grew to full content height and the rail could not be scrolled
            independently of the page. 12rem ≈ sticky offset + rail header. */}
        <ul className="scrollbar-thin max-h-[calc(100dvh-12rem)] space-y-0.5 overflow-y-auto overscroll-contain px-2 py-2.5">
          {entries.map((entry) => {
            const { chip, text } = parseTocLabel(entry.label);
            const locked = entry.locked || !entry.href;
            const isActive =
              !!activeId && entry.href === `#${activeId}`;

            if (locked) {
              return (
                <li key={entry.id}>
                  <span className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-text-muted">
                    <span className="flex-1 truncate">{text}</span>
                    <Lock
                      aria-hidden="true"
                      className="h-3 w-3 shrink-0 text-text-subtle"
                    />
                    {/* Keep the مادة number visible on locked rows — the lock
                        alone made every row an identical unnumbered «المادة». */}
                    {chip && (
                      <span className="shrink-0 rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-xs font-semibold tabular-nums text-text-subtle">
                        {chip}
                      </span>
                    )}
                  </span>
                </li>
              );
            }

            return (
              <li key={entry.id}>
                <Link
                  href={entry.href!}
                  onClick={(event) => handleAnchorClick(event, entry.href!)}
                  aria-current={isActive ? "location" : undefined}
                  className={cn(
                    "group flex items-center gap-2 rounded-lg px-2.5 py-1.5 transition-colors",
                    isActive
                      ? "bg-accent-soft"
                      : "hover:bg-accent-soft",
                  )}
                >
                  <span
                    className={cn(
                      "flex-1 truncate text-sm transition-colors",
                      isActive
                        ? "font-semibold text-primary"
                        : "text-text-secondary group-hover:text-primary",
                    )}
                  >
                    {text}
                  </span>
                  {chip ? (
                    <span
                      className={cn(
                        "shrink-0 rounded-md px-1.5 py-0.5 font-mono text-xs font-semibold tabular-nums transition-colors",
                        isActive
                          ? "bg-primary text-primary-foreground"
                          : "bg-surface-2 text-text-muted group-hover:bg-accent-soft group-hover:text-primary",
                      )}
                    >
                      {chip}
                    </span>
                  ) : (
                    <ChevronLeft
                      aria-hidden="true"
                      className={cn(
                        "h-3.5 w-3.5 shrink-0 transition-colors",
                        isActive
                          ? "text-primary"
                          : "text-text-subtle group-hover:text-primary",
                      )}
                    />
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
