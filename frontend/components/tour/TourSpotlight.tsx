"use client";

import type { TourRect } from "@/hooks/use-tour-anchor";

interface TourSpotlightProps {
  /** Viewport-space rect of the anchor (already settled by `useTourAnchor`). */
  rect: TourRect;
  /** Breathing room between the anchor and the dim panels. */
  padding?: number;
}

const DEFAULT_PADDING = 8;

/**
 * The dimming layer — **four divs, not an SVG mask**.
 *
 * A mask (or a giant `box-shadow: 0 0 0 100vmax`) covers the target with a
 * transparent-but-present element, so every click on the very thing the step
 * is asking the user to press has to be re-plumbed with `pointer-events`
 * juggling. That is the classic "the tour ate my click" bug. Four panels
 * framing the rect leave the target genuinely uncovered: the browser hit-tests
 * straight through to the real button, and the tour needs no special handling
 * for the click it is waiting for.
 *
 * Everything here is `pointer-events: none` regardless — the panels are paint,
 * not interaction. The user keeps full use of the app during the tour, which
 * is what makes the tour impossible to get trapped inside.
 *
 * Rendered inside the tour root at `z-[80]`, i.e. above the mobile workspace
 * overlay (`z-[60]`) and above portalled Radix layers (`z-[70]`), so Act 3 can
 * frame a node inside the «عرض المصدر» dialog.
 */
export function TourSpotlight({ rect, padding = DEFAULT_PADDING }: TourSpotlightProps) {
  const top = Math.max(0, rect.top - padding);
  const left = Math.max(0, rect.left - padding);
  const right = rect.left + rect.width + padding;
  const bottom = rect.top + rect.height + padding;
  const bandHeight = Math.max(0, bottom - top);

  // No backdrop-filter: four full-width fixed panels re-laid-out every frame
  // while the anchor scrolls is exactly the workload a blur makes expensive on
  // a phone. Flat alpha costs nothing.
  //
  // The panels intentionally run edge to edge, under the notch and the home
  // indicator included — dimming must cover the whole painted surface
  // (`viewportFit: "cover"`). Safe-area insets belong to the CARD, which is the
  // only thing that must stay readable and tappable (§7.3).
  const panel = "pointer-events-none fixed bg-black/45";

  return (
    <div aria-hidden="true">
      {/* above */}
      <div className={panel} style={{ top: 0, left: 0, right: 0, height: top }} />
      {/* below */}
      <div className={panel} style={{ top: bottom, left: 0, right: 0, bottom: 0 }} />
      {/* physical left of the anchor band */}
      <div className={panel} style={{ top, left: 0, width: left, height: bandHeight }} />
      {/* physical right of the anchor band */}
      <div className={panel} style={{ top, left: right, right: 0, height: bandHeight }} />

      {/* Ring around the hole — pure decoration, and pointer-events-none like
          everything else, so it can safely overlap the target's own edges. */}
      <div
        className="pointer-events-none fixed rounded-xl ring-2 ring-primary/70 ring-offset-0 transition-opacity"
        style={{
          top,
          left,
          width: Math.max(0, right - left),
          height: bandHeight,
        }}
      />
    </div>
  );
}
