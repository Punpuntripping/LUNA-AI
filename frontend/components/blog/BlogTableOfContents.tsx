"use client";

import { useEffect, useState, type MouseEvent } from "react";
import { cn } from "@/lib/utils";
import type { TocHeading } from "@/lib/markdown/headings";

interface BlogTableOfContentsProps {
  headings: TocHeading[];
  className?: string;
}

/**
 * Inline-start indent per heading depth (RTL: padding-inline-start). Depth 1
 * sits flush; deeper levels step further in. Depths beyond 4 share the deepest
 * indent so a stray ``#####`` heading doesn't run off the rail.
 */
function indentClass(depth: number): string {
  switch (depth) {
    case 1:
      return "ps-0";
    case 2:
      return "ps-4";
    case 3:
      return "ps-8";
    default:
      return "ps-12";
  }
}

/**
 * Table of contents for a مدونة (title-mode) article.
 *
 * Renders a «محتويات» heading + a nav list of anchor links built from the
 * article's markdown headings (``extractHeadings``). The slugs already equal
 * the ids ``MarkdownRenderer`` emits under ``headingAnchors``, so each
 * ``href="#slug"`` resolves to a real heading element.
 *
 * Behaviour:
 *   • Click → smooth-scroll to the heading + update the URL hash (no native
 *     jump that would fight the smooth scroll).
 *   • Scrollspy → an ``IntersectionObserver`` over the heading elements tracks
 *     the topmost visible heading; its link is highlighted (``text-primary``).
 *
 * SSR-safe: all DOM access happens inside ``useEffect`` / event handlers.
 * The PARENT decides whether to render this at all (it's omitted when an
 * article has fewer than two headings).
 */
export function BlogTableOfContents({
  headings,
  className,
}: BlogTableOfContentsProps) {
  const [activeSlug, setActiveSlug] = useState<string | null>(null);

  useEffect(() => {
    if (headings.length === 0) return;

    const elements = headings
      .map((h) => document.getElementById(h.slug))
      .filter((el): el is HTMLElement => el !== null);
    if (elements.length === 0) return;

    // Track which heading ids are currently intersecting; the active one is
    // the FIRST in document order so the rail follows the reader downward.
    const visible = new Set<string>();

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            visible.add(entry.target.id);
          } else {
            visible.delete(entry.target.id);
          }
        }
        const topmost = headings.find((h) => visible.has(h.slug));
        if (topmost) {
          setActiveSlug(topmost.slug);
        }
      },
      // Bias the "active" band to the upper portion of the viewport so a
      // heading counts as current once it reaches the top, not the middle.
      { rootMargin: "-80px 0px -70% 0px", threshold: 0 },
    );

    elements.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [headings]);

  if (headings.length === 0) return null;

  const handleClick = (event: MouseEvent<HTMLAnchorElement>, slug: string) => {
    event.preventDefault();
    const el = document.getElementById(slug);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth" });
    setActiveSlug(slug);
    // Update the hash without triggering the browser's native jump (which
    // would cancel the smooth scroll above).
    window.history.replaceState(null, "", `#${slug}`);
  };

  return (
    <nav className={cn("text-sm", className)} aria-label="محتويات">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        محتويات
      </p>
      <ul className="flex flex-col gap-1.5 border-s border-border/60 ps-3">
        {headings.map((h, i) => {
          const isActive = activeSlug === h.slug;
          return (
            <li key={`${h.slug}-${i}`} className={indentClass(h.depth)}>
              <a
                href={`#${h.slug}`}
                onClick={(event) => handleClick(event, h.slug)}
                className={cn(
                  "block py-0.5 leading-snug transition-colors hover:text-foreground",
                  isActive
                    ? "font-semibold text-primary"
                    : "text-muted-foreground",
                )}
              >
                {h.text}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
