"use client";

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { TourRect } from "@/hooks/use-tour-anchor";
import { TOUR_UI, type TourStep } from "./tour-content";

interface TourCardProps {
  step: TourStep;
  /** 1-based, for the «3 / 13» progress line. */
  stepNumber: number;
  totalSteps: number;
  /** Anchor rect to sit beside, or null when the anchor is nowhere on screen. */
  rect: TourRect | null;
  /** Anchor rect has stopped moving — the card holds its fade-in until then. */
  settled: boolean;
  isMobile: boolean;
  isLast: boolean;
  /** Show the forward button (always on manual steps, after the stall on click steps). */
  showNext: boolean;
  onNext: () => void;
  onSkip: () => void;
}

/** Gap between the anchor and the card. */
const GAP = 12;
/** Minimum distance from the viewport edge (before safe-area insets). */
const MARGIN = 12;

interface CardSize {
  width: number;
  height: number;
}

interface CardPosition {
  top: number;
  left: number;
}

/**
 * Desktop prefers a side placement (the anchor keeps its vertical context);
 * mobile always flips above/below, because there is no horizontal room to
 * speak of at 390px and a side card would cover the target it points at (§7.3).
 */
function placeCard(
  rect: TourRect,
  size: CardSize,
  viewport: { width: number; height: number },
  isMobile: boolean,
): CardPosition {
  const clamp = (value: number, min: number, max: number) =>
    Math.min(Math.max(value, min), Math.max(min, max));

  const anchorRight = rect.left + rect.width;
  const anchorBottom = rect.top + rect.height;

  if (!isMobile) {
    const roomStart = rect.left - GAP - MARGIN;
    const roomEnd = viewport.width - anchorRight - GAP - MARGIN;
    if (roomStart >= size.width || roomEnd >= size.width) {
      const left =
        roomStart >= size.width
          ? rect.left - GAP - size.width
          : anchorRight + GAP;
      const top = clamp(
        rect.top + rect.height / 2 - size.height / 2,
        MARGIN,
        viewport.height - size.height - MARGIN,
      );
      return { top, left };
    }
  }

  const roomBelow = viewport.height - anchorBottom - GAP - MARGIN;
  const roomAbove = rect.top - GAP - MARGIN;
  let top: number;
  if (roomBelow >= size.height) {
    top = anchorBottom + GAP;
  } else if (roomAbove >= size.height) {
    top = rect.top - GAP - size.height;
  } else {
    // Neither side fits (tall anchor on a short viewport). Pin to whichever
    // edge leaves more of the anchor visible rather than covering it dead
    // centre; the card is capped at 70dvh so it can never fill the screen.
    top =
      roomBelow >= roomAbove
        ? viewport.height - size.height - MARGIN
        : MARGIN;
  }

  const left = clamp(
    rect.left + rect.width / 2 - size.width / 2,
    MARGIN,
    viewport.width - size.width - MARGIN,
  );
  return { top, left };
}

/**
 * Final clamp expressed in CSS, not JS: `env(safe-area-inset-*)` is unreadable
 * from script, and the page paints under the notch (`viewportFit: "cover"`), so
 * a purely numeric position can land the card under the status bar or the home
 * indicator on a phone (§7.3).
 *
 * `clamp()` with a max below its min collapses to the min per spec, so the
 * degenerate case pins the card just below the notch instead of misbehaving.
 */
function safeAxis(
  value: number,
  size: number,
  startInset: string,
  endInset: string,
  extent: string,
): string {
  const min = `calc(env(${startInset}, 0px) + ${MARGIN}px)`;
  const max = `calc(${extent} - env(${endInset}, 0px) - ${size + MARGIN}px)`;
  return `clamp(${min}, ${value}px, ${max})`;
}

/**
 * The coach mark itself: the only pointer-interactive node the tour renders.
 *
 * ⚠ RADIX. While the «عرض المصدر» dialog is open, Radix's DismissableLayer sets
 * `pointer-events: none` on `<body>` and re-enables it only on its own layer.
 * The tour root is a sibling of that layer, so without an explicit
 * `pointer-events: auto` HERE every button on this card is dead — and on a
 * phone that reads as a frozen app with no tappable way out. `pointer-events-auto`
 * is load-bearing, not styling.
 *
 * ⚠ RADIX, part two. The same layer dismisses itself on any `pointerdown` that
 * lands outside it — which is exactly what pressing «التالي» during Act 3 is.
 * A capture-phase listener on this node stops that event before it reaches the
 * document-level handler, so reading the dialog's own copy does not close the
 * dialog out from under the step that is explaining it.
 */
export function TourCard({
  step,
  stepNumber,
  totalSteps,
  rect,
  settled,
  isMobile,
  isLast,
  showNext,
  onNext,
  onSkip,
}: TourCardProps) {
  const cardRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<CardSize | null>(null);
  const [viewport, setViewport] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });

  // Radix's outside-pointerdown dismissal, neutralised at the source. Native +
  // capture on purpose: React's synthetic handlers run too late (and, for a
  // portal, on a container Radix's document listener does not respect).
  useEffect(() => {
    const node = cardRef.current;
    if (!node) return;
    const stop = (event: Event) => event.stopPropagation();
    const types = ["pointerdown", "mousedown", "touchstart"] as const;
    types.forEach((type) => node.addEventListener(type, stop, true));
    return () => {
      types.forEach((type) => node.removeEventListener(type, stop, true));
    };
  }, []);

  useEffect(() => {
    const readViewport = () =>
      setViewport({ width: window.innerWidth, height: window.innerHeight });
    readViewport();
    window.addEventListener("resize", readViewport);
    return () => window.removeEventListener("resize", readViewport);
  }, []);

  // Measured before paint, so the very first frame of a step is positioned
  // against this step's real card height rather than the previous step's.
  useLayoutEffect(() => {
    const node = cardRef.current;
    if (!node) return;
    const measure = () => {
      const box = node.getBoundingClientRect();
      setSize((prev) =>
        prev && Math.abs(prev.width - box.width) < 0.5 &&
        Math.abs(prev.height - box.height) < 0.5
          ? prev
          : { width: box.width, height: box.height },
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [step.key]);

  // Settling can never be the ONLY thing gating the card: an anchor inside
  // something that never stops moving (a spinner, a marquee, an animated
  // skeleton) would otherwise hold the card invisible forever, which is a
  // silent, unrecoverable tour. After this budget the card shows regardless.
  const [settleTimedOut, setSettleTimedOut] = useState(false);
  useEffect(() => {
    setSettleTimedOut(false);
    const timerId = window.setTimeout(() => setSettleTimedOut(true), 1_200);
    return () => window.clearTimeout(timerId);
  }, [step.key]);

  const hasAnchor = rect !== null;
  const canPlace = hasAnchor && size !== null && viewport.width > 0;

  let style: CSSProperties;
  if (canPlace && rect && size) {
    const { top, left } = placeCard(rect, size, viewport, isMobile);
    style = {
      top: safeAxis(top, size.height, "safe-area-inset-top", "safe-area-inset-bottom", "100dvh"),
      left: safeAxis(left, size.width, "safe-area-inset-left", "safe-area-inset-right", "100vw"),
    };
  } else if (hasAnchor) {
    // Measured on the next frame; keep it off-screen rather than flashing at 0,0.
    style = { top: 0, left: 0, visibility: "hidden" };
  } else {
    // No anchor at all — the card becomes a plain centred panel and the step
    // still reads and still advances. Never a spotlight on a 0×0 rect.
    style = { top: "50%", left: "50%", transform: "translate(-50%, -50%)" };
  }

  // Fade in only once the anchor has stopped moving, so the card doesn't chase
  // a smooth scroll across the screen.
  const visible = !hasAnchor || ((settled || settleTimedOut) && canPlace);

  return (
    <div
      ref={cardRef}
      dir="rtl"
      role="dialog"
      aria-modal="false"
      aria-label={TOUR_UI.ariaLabel}
      data-tour-card=""
      style={style}
      className={cn(
        "fixed w-[min(20rem,calc(100vw-1.5rem))] max-h-[70dvh]",
        "overflow-y-auto rounded-xl border border-border bg-popover p-4",
        "text-popover-foreground shadow-lg transition-opacity duration-200",
        // ⚠ pointer-events-auto is load-bearing (see the Radix note above) —
        // and paired with visibility so an invisible card can never take a
        // click meant for the app underneath it.
        visible ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-muted-foreground">
          {step.act}
        </span>
        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted-foreground">
          {TOUR_UI.progress(stepNumber, totalSteps)}
        </span>
      </div>

      <h3 className="mt-1.5 text-sm font-semibold leading-snug text-foreground">
        {step.title}
      </h3>
      <p className="mt-1 text-[13px] leading-relaxed text-muted-foreground">
        {step.body}
      </p>

      {step.cta && (
        <p className="mt-2 text-[13px] font-medium leading-relaxed text-primary">
          {step.cta}
        </p>
      )}
      {step.note && (
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          {step.note}
        </p>
      )}
      {!hasAnchor && (
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          {TOUR_UI.anchorMissing}
        </p>
      )}

      <div className="mt-3 flex items-center justify-between gap-2">
        {/* Always visible, always reachable — the tour's guaranteed exit. */}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 px-2 text-xs text-muted-foreground"
          onMouseDown={(event) => event.preventDefault()}
          onClick={onSkip}
        >
          {TOUR_UI.skip}
        </Button>

        {showNext && (
          <Button
            type="button"
            size="sm"
            className="h-8 px-4 text-xs"
            // preventDefault keeps focus where it is: during Act 3 a Radix
            // focus trap owns the dialog and would yank focus back mid-press.
            onMouseDown={(event) => event.preventDefault()}
            onClick={onNext}
          >
            {isLast ? TOUR_UI.finish : TOUR_UI.next}
          </Button>
        )}
      </div>
    </div>
  );
}
