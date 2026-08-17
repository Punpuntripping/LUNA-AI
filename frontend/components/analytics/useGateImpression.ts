"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { track } from "@/lib/analytics/client";
import type { GateKind } from "@/lib/analytics/events";

/**
 * «Was this gate actually SEEN?» — the shared impression signal behind question 4
 * of `.claude/plans/product_analytics.md` («where did the visitor decide NOT to
 * sign in?»).
 *
 * ⚠ T6 — A GATE THAT RENDERED IS NOT A GATE THAT WAS SEEN. `GateBanner` and
 * `HubCtaWall` sit far below the fold on documents several viewports tall, so
 * counting renders would report a huge fake denominator and make gate conversion
 * look catastrophic. The impression is therefore an `IntersectionObserver`
 * measurement, modelled on `AnonCtaPopup`'s own `whenAnonCtaVisibility` (the
 * house pattern for "is a conversion surface on screen right now").
 *
 * Two conditions beyond "intersecting", both deliberate:
 *   · at least half of the surface — or half a viewport of it, whichever is
 *     smaller — is inside the viewport, so a surface taller than the screen
 *     still counts once a real portion of it is on display;
 *   · it stays that way for `MIN_VISIBLE_MS`, so a fling from top to bottom does
 *     not mint an impression for every gate it flew past.
 * …plus `document.visibilityState === "visible"`, because a gate that "appeared"
 * in a backgrounded or prerendered tab was never read by anybody.
 *
 * ⚠ T2 — PURE CLIENT. Every call site is a `"use client"` leaf and every event
 * is a fire-and-forget beacon issued AFTER hydration, so nothing here can enter
 * the ISR bake that the library wings share between visitors.
 *
 * ⚠ T9 — analytics must never break a page. `track()` is contractually
 * non-throwing, and every call below is wrapped anyway: a reader must never see
 * a degraded page because a tracker failed.
 *
 * ⚠ T4 — `path` is a PATHNAME, always. `?q=` on the search surfaces is
 * user-typed legal text; it never leaves the browser. Nothing here reads
 * `location.search`.
 */

/** Which CTA the reader took out of a gate. Matches the §3 taxonomy. */
export type GateCta = "register" | "login";

export interface GateImpressionOptions {
  /**
   * Overrides the path-derived content type, for a surface that knows its own
   * (`FullContentGate` is handed a `contentType`; a `GateBanner` is not).
   */
  contentType?: string;
  /**
   * Counted at all? Defaults to true. Pass false for a surface that is on the
   * page but is NOT the conversion surface — a `GateBanner` suppressed by
   * `GateCtaSuppressor`, or a wall that pitches a plan rather than an account.
   * Firing from a suppressed gate would double-count the denominator.
   */
  enabled?: boolean;
}

/** Half of the surface, or half a viewport of it — whichever is smaller. */
const MIN_VISIBLE_RATIO = 0.5;
/** How long that has to hold before it counts as read rather than scrolled past. */
const MIN_VISIBLE_MS = 500;

/**
 * Returns a ref callback to put on the gate's own element. Fires `gate_view`
 * at most ONCE per mount per path, the first time that element is genuinely
 * visible; a surface whose ref never attaches (an early `return null`, a
 * suppressed branch) never fires, which is exactly the intended behaviour.
 */
export function useGateImpression(
  gateKind: GateKind,
  options: GateImpressionOptions = {},
): (node: Element | null) => void {
  const { contentType, enabled = true } = options;
  const pathname = usePathname() ?? "";

  // State, not a ref: the element attaches AFTER the first render, and the
  // observer effect has to re-run when it does.
  const [node, setNode] = useState<Element | null>(null);
  /** The path this hook already reported, so one mount reports each path once. */
  const firedForRef = useRef<string | null>(null);

  const attach = useCallback((next: Element | null) => setNode(next), []);

  useEffect(() => {
    if (!enabled || !node) return;
    if (firedForRef.current === pathname) return;

    const element = node;
    let disposed = false;
    let holdTimer: ReturnType<typeof setTimeout> | undefined;
    let observer: IntersectionObserver | undefined;

    function teardown(): void {
      observer?.disconnect();
      observer = undefined;
      if (holdTimer) clearTimeout(holdTimer);
      holdTimer = undefined;
      window.removeEventListener("scroll", onFallbackCheck);
      window.removeEventListener("resize", onFallbackCheck);
      document.removeEventListener("visibilitychange", onFallbackCheck);
    }

    function fire(): void {
      if (disposed || firedForRef.current === pathname) return;
      firedForRef.current = pathname;
      teardown();
      trackGateView(gateKind, pathname, contentType);
    }

    /** Start (or cancel) the dwell that separates "seen" from "scrolled past". */
    function hold(visible: boolean): void {
      if (disposed) return;
      if (!visible || document.visibilityState !== "visible") {
        if (holdTimer) clearTimeout(holdTimer);
        holdTimer = undefined;
        return;
      }
      if (holdTimer) return;
      holdTimer = setTimeout(() => {
        holdTimer = undefined;
        fire();
      }, MIN_VISIBLE_MS);
    }

    /**
     * Rectangle maths — the same measurement the observer performs, but on
     * demand. Needed for two cases the observer does not cover: a tab that
     * becomes visible again without the geometry changing (no new IO callback),
     * and a browser with no `IntersectionObserver` at all.
     */
    function onFallbackCheck(): void {
      if (disposed) return;
      hold(isVisibleEnough(element.getBoundingClientRect()));
    }

    if (typeof IntersectionObserver !== "undefined") {
      observer = new IntersectionObserver(
        (entries) => {
          const entry = entries[entries.length - 1];
          if (!entry) return;
          hold(entry.isIntersecting && isVisibleEnough(entry.boundingClientRect));
        },
        // Several thresholds so a surface taller than the viewport still reports
        // as it scrolls through, instead of only at its two edges.
        { threshold: [0, 0.25, 0.5, 0.75, 1] },
      );
      observer.observe(element);
    } else {
      window.addEventListener("scroll", onFallbackCheck, { passive: true });
      window.addEventListener("resize", onFallbackCheck);
      onFallbackCheck();
    }

    // A hidden tab must never mint an impression, and must be re-measured when
    // the reader comes back to it.
    document.addEventListener("visibilitychange", onFallbackCheck);

    return () => {
      disposed = true;
      teardown();
    };
  }, [enabled, node, pathname, gateKind, contentType]);

  return attach;
}

// ------------------------------------------------------------------
// The three gate events — one wrapper each, so the prop shape and the
// never-throw guarantee live in ONE place rather than in seven components.
// ------------------------------------------------------------------

/** `gate_view` for a surface that owns its own "seen" moment (a popup/modal). */
export function trackGateView(
  gateKind: GateKind,
  path: string,
  contentType?: string,
): void {
  try {
    track("gate_view", {
      gate_kind: gateKind,
      path,
      content_type: contentType ?? contentTypeFromPath(path),
    });
  } catch {
    // T9 — a tracker failure is never a reader's problem.
  }
}

/** `gate_cta_click` — the reader took the way out this gate offered. */
export function trackGateCtaClick(
  gateKind: GateKind,
  path: string,
  cta: GateCta,
): void {
  try {
    track("gate_cta_click", { gate_kind: gateKind, path, cta });
  } catch {
    // T9.
  }
}

/** `gate_dismiss` — a popup closed with no CTA click. The refusal, captured. */
export function trackGateDismiss(gateKind: GateKind, path: string): void {
  try {
    track("gate_dismiss", { gate_kind: gateKind, path });
  } catch {
    // T9.
  }
}

// ------------------------------------------------------------------
// Path → content type
// ------------------------------------------------------------------

/**
 * What KIND of thing the reader was looking at when the gate appeared, derived
 * from the pathname alone — no props to thread through six components, and no
 * second source of truth to drift.
 *
 * Hubs and documents are kept distinct on purpose: «gate seen on a نظام» and
 * «gate seen on the أنظمة directory» are different events in the funnel even
 * though they share a wing. `path` is sent alongside on every event, so this is
 * a grouping dimension, never the only record of where the gate was.
 */
export function contentTypeFromPath(pathname: string): string {
  const seg = pathname.split("/").filter(Boolean);
  if (seg.length === 0) return "home";

  const [wing, second, third] = seg;
  // `/{wing}` and `/{wing}/page/{n}` are both the directory grid, not a document.
  const isHub = second === undefined || second === "page";

  switch (wing) {
    case "regulations":
      if (isHub) return "regulations_hub";
      // /regulations/{slug}/{article} is a مادة, not the نظام.
      return third ? "article" : "regulation";
    case "circulars":
      return isHub ? "circulars_hub" : "circular";
    case "judgments":
      // /judgments/courts/{slug} is a court LIST, not a ruling.
      return isHub || second === "courts" ? "judgments_hub" : "judgment";
    case "compliance":
      return isHub ? "compliance_hub" : "compliance";
    case "blog":
      return isHub ? "blog_hub" : "blog_post";
    case "forms":
      return isHub ? "forms_hub" : "form";
    case "calculators":
      return isHub ? "calculators_hub" : "calculator";
    case "library":
      return isHub ? "library_hub" : "library_section";
    default:
      return "other";
  }
}

// ------------------------------------------------------------------
// Visibility measurement
// ------------------------------------------------------------------

/**
 * Is enough of this rectangle on screen to call it seen?
 *
 * The bar is half the surface OR half a viewport of it, whichever is smaller —
 * a hub wall shorter than the screen has to be half in view, while a gate panel
 * taller than the screen counts once it fills half of the display. A single
 * ratio would either never fire for the tall one or fire on a sliver of the
 * short one.
 */
function isVisibleEnough(rect: DOMRect): boolean {
  const viewport = window.innerHeight || document.documentElement.clientHeight;
  if (viewport <= 0 || rect.height <= 0) return false;

  const visible = Math.min(rect.bottom, viewport) - Math.max(rect.top, 0);
  if (visible <= 0) return false;

  return visible >= Math.min(rect.height, viewport) * MIN_VISIBLE_RATIO;
}
