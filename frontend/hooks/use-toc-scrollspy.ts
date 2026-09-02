"use client";

import { useCallback, useEffect, useState } from "react";
import type { TocEntry } from "@/types/library";

/**
 * The signup gate every library document page renders when part of the text is
 * withheld. It is the click fallback for a TOC row whose section is NOT in the
 * DOM (anon reader, gated مادة): the row still goes somewhere meaningful
 * instead of dying on a missing anchor.
 */
export const TOC_GATE_ANCHOR_ID = "library-doc-gate";

/**
 * Only same-page anchors of this shape participate in the spy. Entries that
 * link OUT to a مادة page (`/regulations/{slug}/{article}`) simply navigate and
 * never need an active state.
 */
const SPY_HREF_PREFIX = "#sec-";

/**
 * Desktop rail default, kept verbatim from `TocRail`: ignore the top 96px
 * (sticky header + breathing room) and the bottom 60% of the viewport, so the
 * "current" section is the one nearest the top of the reading area rather than
 * whatever happens to be visible.
 */
const DEFAULT_ROOT_MARGIN = "-96px 0px -60% 0px";

export interface UseTocScrollspyOptions {
  /**
   * IntersectionObserver `rootMargin`. Phones carry a 60px header, not the
   * desktop rail's 96px allowance — `TocFloating` passes its own value.
   */
  rootMargin?: string;
  /**
   * Which same-page hrefs take part in the spy. Defaults to `#sec-`, the shape
   * every library document page emits — so the corpus wings are unaffected.
   *
   * The مدونة wing passes plain `"#"`: its anchors are bare heading slugs from
   * `slugifyHeading`, not `sec-` ids. Without this the rail rendered but no row
   * ever lit up — a silent regression against `BlogTableOfContents`, the
   * component the blog TOC swap replaced, which had its own working
   * IntersectionObserver. `sec-`-prefixing blog ids instead would have been the
   * other fix, and would have broken every `#slug` link already copied out of a
   * published article.
   */
  spyPrefix?: string;
}

export interface TocScrollspy {
  /** `id` of the section currently being read, or null (no `#sec-` targets). */
  activeId: string | null;
  /** Scroll to `href`'s target, falling back to the signup gate. */
  jumpTo: (href: string) => void;
  /** `onClick` for an anchor/`<Link>` row — same-page hrefs only. */
  handleAnchorClick: (
    event: React.MouseEvent<HTMLAnchorElement>,
    href: string,
  ) => void;
  /** Does this row's section actually exist in the DOM right now? */
  hasTarget: (href: string) => boolean;
}

/**
 * Scrollspy + click behaviour shared by the library's two TOC surfaces: the
 * desktop `TocRail` and the phone `TocFloating` widget. Extracted from TocRail
 * unchanged — same observer options, same missing-anchor fallback — so the two
 * always agree on which مادة the reader is in.
 *
 * The DOM is read lazily (`hasTarget`, and the observer's own lookup) on
 * purpose: `FullContentGate` swaps the FULL section list in after mount for a
 * signed-in reader, so any snapshot taken at mount would be stale.
 */
export function useTocScrollspy(
  entries: TocEntry[],
  options?: UseTocScrollspyOptions,
): TocScrollspy {
  const rootMargin = options?.rootMargin ?? DEFAULT_ROOT_MARGIN;
  const spyPrefix = options?.spyPrefix ?? SPY_HREF_PREFIX;
  const [activeId, setActiveId] = useState<string | null>(null);

  // Every anchor row is clickable. When the target section exists (the free
  // visible sections, or the FULL document a signed-in reader's browser swapped
  // in) → smooth-scroll to it. When it doesn't (anon reader, gated section) →
  // land on the signup gate (#library-doc-gate) so the click always goes
  // somewhere meaningful.
  const jumpTo = useCallback((href: string): void => {
    if (!href.startsWith("#")) return;
    const target =
      document.getElementById(href.slice(1)) ??
      document.getElementById(TOC_GATE_ANCHOR_ID);
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const handleAnchorClick = useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>, href: string): void => {
      if (!href.startsWith("#")) return;
      event.preventDefault();
      jumpTo(href);
    },
    [jumpTo],
  );

  const hasTarget = useCallback((href: string): boolean => {
    if (!href.startsWith("#")) return true; // an outbound link always resolves
    return Boolean(document.getElementById(href.slice(1)));
  }, []);

  useEffect(() => {
    const spyIds = entries
      .filter((e) => e.href?.startsWith(spyPrefix))
      .map((e) => e.href!.slice(1));
    if (spyIds.length === 0) return;

    const targets = spyIds
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => Boolean(el));
    if (targets.length === 0) return;

    const observer = new IntersectionObserver(
      (observed) => {
        const inView = observed
          .filter((o) => o.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (inView[0]) setActiveId(inView[0].target.id);
      },
      { rootMargin, threshold: 0 },
    );
    targets.forEach((t) => observer.observe(t));
    return () => observer.disconnect();
  }, [entries, rootMargin, spyPrefix]);

  return { activeId, jumpTo, handleAnchorClick, hasTarget };
}
