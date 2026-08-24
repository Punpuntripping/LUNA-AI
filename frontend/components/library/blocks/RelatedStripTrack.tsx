"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The INTERACTIVE SHELL of `RelatedStrip` — the scroll container plus its
 * prev/next buttons. Everything inside it (`children` = the `<li>`-wrapped hub
 * cards) is rendered by the SERVER component that owns this one and passed
 * through untouched: a client component may accept server-rendered children as
 * a prop, so the cards never enter the client bundle. Nothing in this file may
 * ever read or reshape `children`.
 *
 * WHY IT EXISTS. The strip used to be pure server markup with the scrollbar
 * painted away (`.scrollbar-none`) and the 0.25-card peek as its only
 * affordance. Measured on production at 1440×900: `scrollWidth` 1622 vs
 * `clientWidth` 752 — 870px of hidden cards — with a 0px scrollbar, zero
 * buttons, and a vertical mouse wheel that moved `scrollLeft` not at all. Every
 * remaining way in (trackpad swipe, shift+wheel, touch drag, arrow keys after
 * tabbing) is either invisible or unavailable to a Windows mouse user. So the
 * shelf grew real buttons.
 *
 * ⚠ RTL SCROLL COORDINATES ARE NOT PORTABLE — DO NOT HARDCODE THE SIGN.
 * In a `dir="rtl"` container modern Chromium puts the scroll origin at the
 * RIGHT edge and runs NEGATIVE leftward (measured: `scrollLeft = -231` reads
 * back `-231.2`, while `scrollLeft = 300` clamps to `0`), so "next" is a
 * DECREASING `scrollLeft` and `max` is `0`. Older WebKit ("default": origin at
 * the left, max at the right) and legacy Edge/IE ("reverse": origin at the
 * right, increasing leftward) disagree. `detectRtlScrollType()` settles it once
 * with a throwaway probe element, and everything downstream works in
 * `offset`/`max` — a distance from the inline START that is always ≥ 0 in all
 * three conventions. Scrolling itself goes through `scrollBy({left})` with a
 * derived sign, never a raw `scrollLeft =` assignment.
 *
 * ⚠ NEVER RADIX `ScrollArea` HERE EITHER — see the note on `RelatedStrip`.
 * A native scroll container is the only thing that keeps `CardShell`'s `h-full`
 * and the cards' `truncate` / `line-clamp` intact.
 */

type RtlScrollType = "negative" | "reverse" | "default";

let rtlScrollTypeCache: RtlScrollType | null = null;

/**
 * Which RTL scroll-coordinate convention this engine uses. Probed ONCE per
 * document with a 4px offscreen scroller and cached at module scope:
 *
 * - unscrolled `scrollLeft > 0`     ⇒ "default"  (origin left, max right)
 * - assigning `1` clamps back to 0  ⇒ "negative" (origin right, negative left)
 * - assigning `1` sticks            ⇒ "reverse"  (origin right, positive left)
 */
function detectRtlScrollType(): RtlScrollType {
  if (rtlScrollTypeCache) return rtlScrollTypeCache;

  const probe = document.createElement("div");
  probe.setAttribute("dir", "rtl");
  probe.style.cssText =
    "position:absolute;top:-9999px;width:4px;height:1px;overflow:scroll;visibility:hidden;";
  const inner = document.createElement("div");
  inner.style.cssText = "width:8px;height:1px;";
  probe.appendChild(inner);
  document.body.appendChild(probe);

  let type: RtlScrollType = "reverse";
  if (probe.scrollLeft > 0) {
    type = "default";
  } else {
    probe.scrollLeft = 1;
    if (probe.scrollLeft === 0) type = "negative";
  }

  probe.remove();
  rtlScrollTypeCache = type;
  return type;
}

interface TrackMetrics {
  /** Distance already scrolled away from the inline START. Always ≥ 0. */
  offset: number;
  /** Total scrollable distance. `0` ⇒ the track fits and cannot scroll. */
  max: number;
  rtl: boolean;
}

function readMetrics(el: HTMLElement): TrackMetrics {
  const max = Math.max(0, el.scrollWidth - el.clientWidth);
  const rtl = getComputedStyle(el).direction === "rtl";
  if (!rtl) return { offset: el.scrollLeft, max, rtl };

  switch (detectRtlScrollType()) {
    case "negative":
      return { offset: -el.scrollLeft, max, rtl };
    case "reverse":
      return { offset: el.scrollLeft, max, rtl };
    default:
      return { offset: max - el.scrollLeft, max, rtl };
  }
}

/** The `scrollBy({left})` sign that moves toward the inline END ("next"). */
function forwardSign(el: HTMLElement): 1 | -1 {
  if (getComputedStyle(el).direction !== "rtl") return 1;
  return detectRtlScrollType() === "reverse" ? 1 : -1;
}

/**
 * One click = one full PAGE of cards (as many as are wholly in view), so seven
 * cards are two clicks apart instead of six. The stride is measured off the
 * live first card + the flex `column-gap` rather than re-deriving the `basis`
 * arithmetic, which keeps the button honest at every breakpoint. Scroll-snap
 * lands the result on a card edge.
 */
function pageStep(el: HTMLElement): number {
  const first = el.firstElementChild;
  const card = first ? first.getBoundingClientRect().width : 0;
  const styles = getComputedStyle(el);
  const gap = parseFloat(styles.columnGap) || 0;
  const stride = card + gap;
  if (stride <= 0) return el.clientWidth;

  const inner =
    el.clientWidth -
    (parseFloat(styles.paddingLeft) || 0) -
    (parseFloat(styles.paddingRight) || 0);
  const perPage = Math.max(1, Math.floor((inner + gap) / stride));
  return stride * perPage;
}

/** Sub-pixel scroll positions are normal (`-231.2`); never compare exactly. */
const EDGE_EPSILON = 2;

const BUTTON_CLASS = cn(
  "absolute top-1/2 z-10 hidden h-9 w-9 -translate-y-1/2 items-center justify-center",
  // `bg-card`, NOT `bg-card/95`: the v2 tokens are raw `var(--surface-1)` hex,
  // and Tailwind v3 cannot compute an alpha channel from those — it DROPS the
  // utility entirely (verified against the compiled CSS: `bg-card/95` emits
  // nothing), which would leave the disc transparent and put a chevron straight
  // on top of card text wherever it overlaps a gap.
  //
  // `border-strong`, not `border`: the disc sits ON a card of the same
  // `--surface-1` background, so the outline is the only thing separating them —
  // and in Warm Slate dark that difference is otherwise nearly nil.
  "rounded-full border border-border-strong bg-card text-text-secondary shadow-md",
  "transition-all duration-200",
  // Same reason as above, and it bites the slash-opacity form too:
  // `hover:border-primary/40` compiles to NOTHING here (`CardShell` carries that
  // dead class today). Solid tokens only.
  "hover:border-primary hover:bg-accent hover:text-primary",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  // Invisible AND inert at its end — a disabled disc floating over a card is
  // noise, but the button stays in the a11y tree so its state is announced.
  // `pointer-events-none` matters: a disabled button still swallows the click
  // that would otherwise open the card underneath it.
  "disabled:pointer-events-none disabled:opacity-0",
  // Touch drags the shelf directly; buttons there are clutter, not affordance.
  "sm:inline-flex",
);

export function RelatedStripTrack({
  label,
  trackClassName,
  children,
}: {
  /** Accessible name for the track — the strip's Arabic heading. */
  label: string;
  trackClassName: string;
  /** Server-rendered `<li>` cards. Passed through, never inspected. */
  children: ReactNode;
}) {
  const trackRef = useRef<HTMLUListElement>(null);
  const prevRef = useRef<HTMLButtonElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);

  const trackId = useId();

  // All three start false so the server HTML and the first client render agree
  // (no buttons); the mount measurement is what reveals them.
  const [canPrev, setCanPrev] = useState(false);
  const [canNext, setCanNext] = useState(false);
  const [rtl, setRtl] = useState(true);

  const sync = useCallback(() => {
    const el = trackRef.current;
    if (!el) return;

    const { offset, max, rtl: isRtl } = readMetrics(el);
    const scrollable = max > EDGE_EPSILON;
    const atStart = !scrollable || offset <= EDGE_EPSILON;
    const atEnd = !scrollable || offset >= max - EDGE_EPSILON;

    setRtl(isRtl);
    setCanPrev(!atStart);
    setCanNext(!atEnd);

    // Focus would be dropped on the floor when the button the keyboard is
    // sitting on goes disabled at an end. Hand it to the track, which is
    // itself a tab stop and arrow-key scrollable.
    const active = document.activeElement;
    if ((atStart && active === prevRef.current) || (atEnd && active === nextRef.current)) {
      el.focus({ preventScroll: true });
    }
  }, []);

  useEffect(() => {
    const el = trackRef.current;
    if (!el) return;

    sync();
    el.addEventListener("scroll", sync, { passive: true });

    // The card `basis` is a percentage of the track, so `scrollWidth` only ever
    // moves when the track does — observing the container is enough.
    const observer = new ResizeObserver(sync);
    observer.observe(el);

    return () => {
      el.removeEventListener("scroll", sync);
      observer.disconnect();
    };
  }, [sync]);

  /**
   * A vertical wheel over a horizontal shelf is what a mouse user expects, and
   * doing nothing (the old behaviour) reads as "this doesn't scroll".
   *
   * Registered manually because React's synthetic `wheel` listener is PASSIVE
   * at the root — `preventDefault()` from `onWheel` is a no-op.
   *
   * THREE RULES KEEP IT FROM STEALING THE PAGE, and all three matter:
   *
   * 1. A gesture with real horizontal intent (trackpad swipe, shift+wheel) is
   *    left to the browser untouched.
   * 2. Against the requested end, the strip RELEASES the gesture for good, so
   *    the page scrolls on instead of the shelf trapping the reader.
   * 3. LATCHING — A GESTURE BELONGS TO WHOEVER IT STARTED ON. Chromium does
   *    this natively: once the page is scrolling, the wheel keeps scrolling the
   *    page even as the cursor crosses other scrollers. But native latching
   *    cannot save us here, because `preventDefault()` cancels the default
   *    action BEFORE the browser gets to apply it — so a reader flicking down
   *    the article with the pointer resting over the strip would have the page
   *    stop dead and the cards slide sideways instead. Hence the WINDOW-level
   *    tracker below: it watches every wheel event, decides at the first event
   *    of each gesture whether that gesture started inside the strip, and the
   *    element listener only acts on gestures that did.
   *
   * The tracker is PASSIVE and does nothing but bookkeeping — a non-passive
   * wheel listener on window would block scroll compositing for the whole page,
   * which is the opposite of the point. Capture phase, so it always runs before
   * the element listener for the same event.
   */
  useEffect(() => {
    const el = trackRef.current;
    if (!el) return;

    // Same idle window Chromium uses to call a wheel gesture finished.
    const GESTURE_GAP_MS = 120;
    let lastWheelAt = -Infinity;
    let startedInside = false;
    let released = false;

    const track = (event: WheelEvent) => {
      const fresh = event.timeStamp - lastWheelAt >= GESTURE_GAP_MS;
      lastWheelAt = event.timeStamp;
      if (!fresh) return;
      const target = event.target;
      startedInside = target instanceof Node && el.contains(target);
      released = false;
    };

    const onWheel = (event: WheelEvent) => {
      if (!startedInside || released) return;
      if (event.ctrlKey || event.defaultPrevented) return;
      // Trackpads and shift+wheel already scroll this axis natively.
      if (Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;

      // deltaMode: 0 = pixels, 1 = lines (Firefox), 2 = pages.
      const unit =
        event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? el.clientHeight : 1;
      const delta = event.deltaY * unit;

      const { offset, max } = readMetrics(el);
      const blocked =
        delta === 0 ||
        max <= EDGE_EPSILON ||
        (delta > 0 ? offset >= max - EDGE_EPSILON : offset <= EDGE_EPSILON);

      if (blocked) {
        // Against that end: hand the rest of the gesture to the page.
        released = true;
        return;
      }

      event.preventDefault();
      // "instant", not "auto": the track carries CSS `scroll-behavior: smooth`
      // for the buttons, and an animated wheel is mush.
      el.scrollBy({ left: forwardSign(el) * delta, behavior: "instant" });
    };

    window.addEventListener("wheel", track, { passive: true, capture: true });
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      window.removeEventListener("wheel", track, { capture: true });
      el.removeEventListener("wheel", onWheel);
    };
  }, []);

  const step = useCallback((direction: 1 | -1) => {
    const el = trackRef.current;
    if (!el) return;
    el.scrollBy({
      left: forwardSign(el) * direction * pageStep(el),
      // "auto" defers to CSS, so `motion-reduce:scroll-auto` on the track turns
      // the animation off for readers who asked for that.
      behavior: "auto",
    });
  }, []);

  const overflows = canPrev || canNext;

  return (
    <div className="relative">
      <ul
        id={trackId}
        ref={trackRef}
        aria-label={label}
        // A scroll container that is not otherwise focusable is unreachable by
        // keyboard in Chromium (WCAG 2.1.1). One tab stop buys arrow-key
        // scrolling for the whole strip.
        tabIndex={0}
        className={trackClassName}
      >
        {children}
      </ul>

      {overflows && (
        <>
          <button
            ref={prevRef}
            type="button"
            onClick={() => step(-1)}
            disabled={!canPrev}
            aria-controls={trackId}
            aria-label="بطاقات سابقة"
            // Logical inset: the "previous" end of the track is the RIGHT edge
            // in Arabic and the left one if this ever renders LTR.
            className={cn(BUTTON_CLASS, "start-1")}
          >
            {rtl ? (
              <ChevronRight aria-hidden="true" className="h-5 w-5" />
            ) : (
              <ChevronLeft aria-hidden="true" className="h-5 w-5" />
            )}
          </button>

          <button
            ref={nextRef}
            type="button"
            onClick={() => step(1)}
            disabled={!canNext}
            aria-controls={trackId}
            aria-label="بطاقات تالية"
            className={cn(BUTTON_CLASS, "end-1")}
          >
            {rtl ? (
              <ChevronLeft aria-hidden="true" className="h-5 w-5" />
            ) : (
              <ChevronRight aria-hidden="true" className="h-5 w-5" />
            )}
          </button>
        </>
      )}
    </div>
  );
}
