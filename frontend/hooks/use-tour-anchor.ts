"use client";

import { useEffect, useRef, useState } from "react";
import type { TourAnchorId } from "@/components/tour/tour-content";

/**
 * DOM plumbing for the coach-mark tour: resolving `data-tour` anchors, tracking
 * their viewport rect across scroll/resize/layout, and watching the two
 * navigation beats that have no chat-store state behind them.
 *
 * Everything here is read-only against the app's DOM. The tour never mutates a
 * component's state directly — it observes, and at worst dispatches a real
 * click on a real button (`clickTourAnchor`), which is indistinguishable from
 * the user doing it.
 */

export interface TourRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface TourAnchorState {
  /** Viewport-space union of every resolved anchor, or null when none is on screen. */
  rect: TourRect | null;
  found: boolean;
  /**
   * The rect has stopped moving (smooth scroll finished, dialog open animation
   * settled). The card holds its fade-in until this flips, so it never paints
   * against a stale mid-scroll rect (§5.5).
   */
  settled: boolean;
}

const EMPTY_ANCHOR: TourAnchorState = { rect: null, found: false, settled: false };

/** Frames the rect must hold still before we call the scroll settled. */
const SETTLE_FRAMES = 3;
/** Sub-pixel noise below this is not a move. */
const RECT_EPSILON = 0.5;

// ---------------------------------------------------------------------------
// Resolution
// ---------------------------------------------------------------------------

/** Raw lookup — may return an element with a zero rect (hidden, unmounted parent). */
export function queryTourAnchor(id: TourAnchorId): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.querySelector<HTMLElement>(`[data-tour="${id}"]`);
}

/**
 * The first anchor with this id that actually occupies space.
 *
 * A zero-area rect is treated as "absent" on purpose: measuring it would put
 * the spotlight on a 0×0 hole at the viewport origin, which is the single most
 * broken-looking failure a coach mark has.
 */
export function resolveTourAnchor(id: TourAnchorId): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const nodes = Array.from(
    document.querySelectorAll<HTMLElement>(`[data-tour="${id}"]`),
  );
  for (const node of nodes) {
    const rect = node.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) return node;
  }
  return null;
}

/**
 * Dispatch a real click on an anchor — the stall guard's "do it for me" path.
 * Returns false when the anchor is absent or disabled, so the caller can still
 * advance the tour instead of pretending something happened.
 */
export function clickTourAnchor(id: TourAnchorId): boolean {
  const node = resolveTourAnchor(id) ?? queryTourAnchor(id);
  if (!node) return false;
  const clickable =
    node.matches("button, a, [role='button']") ||
    node.querySelector<HTMLElement>("button, a, [role='button']");
  const target = clickable instanceof HTMLElement ? clickable : node;
  if (target instanceof HTMLButtonElement && target.disabled) return false;
  target.click();
  return true;
}

/**
 * Close the «عرض المصدر» reveal dialog without owning its state.
 *
 * Preferred path is its own close button (Radix renders one with an «إغلاق»
 * sr-only label). Falls back to an Escape keydown on `document`, which is what
 * Radix's `useEscapeKeydown` listens to. Returns false when no dialog was open.
 */
export function dismissSourceDialog(): boolean {
  if (typeof document === "undefined") return false;
  const dialog = document.querySelector<HTMLElement>(
    "[role='dialog'][data-state='open']:not([data-tour-card])",
  );
  if (!dialog) return false;
  const closeButton = Array.from(
    dialog.querySelectorAll<HTMLButtonElement>("button"),
  ).find((button) => button.textContent?.trim() === "إغلاق");
  if (closeButton) {
    closeButton.click();
    return true;
  }
  document.dispatchEvent(
    new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
  );
  return true;
}

// ---------------------------------------------------------------------------
// Rect tracking
// ---------------------------------------------------------------------------

function unionRects(rects: readonly DOMRect[]): TourRect | null {
  if (rects.length === 0) return null;
  let top = Infinity;
  let left = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  for (const rect of rects) {
    top = Math.min(top, rect.top);
    left = Math.min(left, rect.left);
    right = Math.max(right, rect.right);
    bottom = Math.max(bottom, rect.bottom);
  }
  return { top, left, width: right - left, height: bottom - top };
}

function sameRect(a: TourRect | null, b: TourRect | null): boolean {
  if (a === null || b === null) return a === b;
  return (
    Math.abs(a.top - b.top) < RECT_EPSILON &&
    Math.abs(a.left - b.left) < RECT_EPSILON &&
    Math.abs(a.width - b.width) < RECT_EPSILON &&
    Math.abs(a.height - b.height) < RECT_EPSILON
  );
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export interface UseTourAnchorOptions {
  /**
   * `false` ⇒ do not look, do not scroll, report nothing found. Used when the
   * step's anchors belong to a surface that is currently covered — below `md`
   * the chat is completely hidden behind the workspace overlay, so Act 1's
   * anchors still have real rects while being invisible (§7.1).
   */
  active?: boolean;
  /** Scroll the first resolved anchor into view once per step. Default true. */
  scrollIntoView?: boolean;
}

/**
 * Track the union rect of one or more `data-tour` anchors.
 *
 * Measurement is a rAF loop rather than a pile of scroll/resize listeners. The
 * anchors here live inside a Radix ScrollArea viewport, inside a resizable
 * split panel, inside a dialog that animates on open — a listener set that
 * covers all of those is both longer and less correct than simply re-measuring
 * one element per frame. The loop publishes state only when the rect actually
 * moves, so a still target costs zero renders, and it idles while the tab is
 * hidden.
 */
export function useTourAnchor(
  ids: readonly TourAnchorId[],
  options: UseTourAnchorOptions = {},
): TourAnchorState {
  const { active = true, scrollIntoView = true } = options;
  // Anchors arrive as a fresh array literal each render; the joined key is what
  // actually identifies the step for the effect below.
  const key = ids.join("|");
  const [state, setState] = useState<TourAnchorState>(EMPTY_ANCHOR);
  const stateRef = useRef<TourAnchorState>(EMPTY_ANCHOR);

  useEffect(() => {
    const publish = (next: TourAnchorState) => {
      const prev = stateRef.current;
      if (
        prev.found === next.found &&
        prev.settled === next.settled &&
        sameRect(prev.rect, next.rect)
      ) {
        return;
      }
      stateRef.current = next;
      setState(next);
    };

    if (!active || typeof window === "undefined" || key.length === 0) {
      publish(EMPTY_ANCHOR);
      return;
    }

    const anchorIds = key.split("|") as TourAnchorId[];
    let rafId = 0;
    let stableFrames = 0;
    let lastRect: TourRect | null = null;
    let hasScrolled = false;

    const tick = () => {
      rafId = window.requestAnimationFrame(tick);
      if (document.hidden) return;

      const nodes = anchorIds
        .map((id) => resolveTourAnchor(id))
        .filter((node): node is HTMLElement => node !== null);

      if (nodes.length === 0) {
        stableFrames = 0;
        lastRect = null;
        publish(EMPTY_ANCHOR);
        return;
      }

      // Bring the target on screen the first time it resolves for this step,
      // then let the loop follow the smooth scroll to its resting place.
      if (scrollIntoView && !hasScrolled) {
        hasScrolled = true;
        nodes[0].scrollIntoView({
          behavior: prefersReducedMotion() ? "auto" : "smooth",
          block: "center",
          inline: "nearest",
        });
      }

      const rect = unionRects(nodes.map((node) => node.getBoundingClientRect()));
      if (rect === null) {
        stableFrames = 0;
        publish(EMPTY_ANCHOR);
        return;
      }

      stableFrames = sameRect(rect, lastRect) ? stableFrames + 1 : 0;
      lastRect = rect;
      publish({ rect, found: true, settled: stableFrames >= SETTLE_FRAMES });
    };

    rafId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(rafId);
  }, [key, active, scrollIntoView]);

  return state;
}

// ---------------------------------------------------------------------------
// DOM beats — the two transitions with no store state (§5.3)
// ---------------------------------------------------------------------------

export interface TourDomBeats {
  /** The «عرض المصدر» dialog is mounted and open. */
  sourceDialogOpen: boolean;
  /** The «الإحالات» disclosure reports `aria-expanded="true"`. */
  crossrefsExpanded: boolean;
}

const NO_BEATS: TourDomBeats = {
  sourceDialogOpen: false,
  crossrefsExpanded: false,
};

/** Safety poll — covers any mutation shape the observer's filter misses. */
const BEAT_POLL_MS = 500;

function readDomBeats(): TourDomBeats {
  if (typeof document === "undefined") return NO_BEATS;

  // Prefer the explicit anchor; fall back to "some Radix dialog is open, and it
  // isn't ours" so the step still advances if the anchor attribute is missing
  // from the dialog. `[data-tour-card]` excludes the tour's own card.
  //
  // `data-state` is checked rather than mere presence because Radix keeps the
  // content mounted through its close animation — a just-dismissed dialog would
  // otherwise still read as open for ~200ms. Absent attribute ⇒ treat as open,
  // so this works on a plain element too.
  const sourceAnchor = document.querySelector('[data-tour="source-dialog"]');
  const sourceDialogOpen =
    (sourceAnchor !== null &&
      sourceAnchor.getAttribute("data-state") !== "closed") ||
    document.querySelector(
      "[role='dialog'][data-state='open']:not([data-tour-card])",
    ) !== null;

  const crossrefsAnchor = queryTourAnchor("ref-crossrefs");
  const disclosure =
    crossrefsAnchor === null
      ? null
      : crossrefsAnchor.hasAttribute("aria-expanded")
        ? crossrefsAnchor
        : crossrefsAnchor.querySelector<HTMLElement>("[aria-expanded]");

  return {
    sourceDialogOpen,
    crossrefsExpanded: disclosure?.getAttribute("aria-expanded") === "true",
  };
}

/**
 * Observe the two non-store beats.
 *
 * MutationObserver is the primary mechanism — the reveal dialog appearing is a
 * childList insertion into `<body>` (Radix portals there), and the disclosure
 * flip is an `aria-expanded` attribute change — coalesced through one rAF so a
 * burst of unrelated mutations costs a single re-read. A slow 500ms poll rides
 * alongside as the belt to the observer's braces; both are mounted only while
 * a step that needs them is active.
 */
export function useTourDomBeats(active: boolean): TourDomBeats {
  const [beats, setBeats] = useState<TourDomBeats>(NO_BEATS);
  const beatsRef = useRef<TourDomBeats>(NO_BEATS);

  useEffect(() => {
    if (!active || typeof document === "undefined") {
      beatsRef.current = NO_BEATS;
      setBeats(NO_BEATS);
      return;
    }

    let rafId = 0;
    let cancelled = false;

    const evaluate = () => {
      rafId = 0;
      if (cancelled) return;
      const next = readDomBeats();
      const prev = beatsRef.current;
      if (
        prev.sourceDialogOpen === next.sourceDialogOpen &&
        prev.crossrefsExpanded === next.crossrefsExpanded
      ) {
        return;
      }
      beatsRef.current = next;
      setBeats(next);
    };

    const schedule = () => {
      if (rafId !== 0) return;
      rafId = window.requestAnimationFrame(evaluate);
    };

    evaluate();

    const observer = new MutationObserver(schedule);
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["aria-expanded", "data-state"],
    });
    const intervalId = window.setInterval(schedule, BEAT_POLL_MS);

    return () => {
      cancelled = true;
      observer.disconnect();
      window.clearInterval(intervalId);
      if (rafId !== 0) window.cancelAnimationFrame(rafId);
    };
  }, [active]);

  return beats;
}
