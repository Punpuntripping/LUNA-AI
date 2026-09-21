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

    // The `rootMargin` bottom inset, resolved against the viewport, IS the
    // reading line: `-60%` puts it at 40% of the screen height. Read back off
    // the string the caller passed so the threshold and the observer below can
    // never drift apart.
    const readingLine = (): number => {
      const parts = rootMargin.trim().split(/\s+/);
      const bottom = parts[2] ?? parts[0] ?? "0px";
      const m = /^(-?\d+(?:\.\d+)?)(px|%)$/.exec(bottom);
      const viewport = window.innerHeight;
      if (!m) return viewport;
      const value = Number(m[1]);
      return viewport + (m[2] === "%" ? (value / 100) * viewport : value);
    };

    // ⚠ ONE RULE, NOT TWO: the active row is the LAST target whose top edge has
    // risen above the reading line.
    //
    // It used to be "the topmost target currently INTERSECTING the band", which
    // is only a true answer when the targets tile the page. A library document
    // anchors its rows to `<section>` elements that span their text, so
    // something always overlaps the band. The مدونة anchors its rows to the
    // `<h2>` itself — a 53px line with a thousand px of prose after it — so for
    // most of the scroll NOTHING intersects, the observer had nothing to report,
    // and `activeId` simply kept whatever it had last seen. Measured on the live
    // labour-claim article: the pill read «الخلاصة», the LAST of five headings,
    // while the viewport sat between headings one and two.
    //
    // The replacement rule needs no special case for either shape, and it is
    // computed from LIVE rects, so it cannot be stale.
    //
    // `cursor` is where the last answer was found. Targets are in document
    // order, so their tops increase monotonically and the two walks below
    // converge from whichever side the reader moved — O(1) for an ordinary
    // scroll delta, O(distance) for a jump. That is what keeps this affordable
    // on a 700-مادة نظام, where a naive full scan measured 1.7ms per frame.
    let cursor = 0;
    const pick = () => {
      const line = readingLine();
      let i = Math.min(cursor, targets.length - 1);
      while (i > 0 && targets[i]!.getBoundingClientRect().top >= line) i--;
      while (
        i + 1 < targets.length &&
        targets[i + 1]!.getBoundingClientRect().top < line
      ) {
        i++;
      }
      cursor = i;
      // Above the first heading, nothing is being read yet — the rail shows no
      // active row and the phone pill keeps its «المحتويات» label.
      const first = targets[0]!.getBoundingClientRect().top;
      setActiveId(first >= line ? null : targets[i]!.id);
    };

    let frame = 0;
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        pick();
      });
    };

    // ⚠ SCROLL IS THE PRIMARY TRIGGER, and the observer alone could not be.
    // IntersectionObserver fires on a CHANGE of intersection state, so a jump —
    // `scrollTo`, a restored position, a hash landing, a fast flick — can move a
    // target from above the band to below it within one frame without ever
    // intersecting: false → false, no change, no callback. Reproduced while
    // building this: jumping from the foot of an article back to y=2500 left the
    // pill on heading three while the reader was at heading two.
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule, { passive: true });

    // The observer is kept as a SECOND trigger for the things scroll does not
    // report: the first paint (no scroll event fires on load) and a layout shift
    // under the reader — `FullContentGate` swapping the full section list in
    // after mount moves every anchor below it.
    const observer = new IntersectionObserver(schedule, {
      rootMargin,
      threshold: 0,
    });
    targets.forEach((t) => observer.observe(t));

    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [entries, rootMargin, spyPrefix]);

  return { activeId, jumpTo, handleAnchorClick, hasTarget };
}
