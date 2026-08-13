"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ENGAGE_RATIOS,
  MIN_DWELL_MS,
  MIN_GAP_MS,
  SHORT_PAGE_DWELL,
} from "@/lib/anon-cta/config";
import { anonCtaCopy } from "@/lib/anon-cta/copy";
import { isEligibleDoc } from "@/lib/anon-cta/eligibility";
import {
  canFire,
  muteAnonCta,
  noteEligibleDoc,
  readAnonCtaState,
  recordImpression,
} from "@/lib/anon-cta/session";

/**
 * «جرّب ريحان مجاناً» — the ACTIVE conversion surface on the five public content
 * wings (`.claude/plans/anon_conversion_popup.md`).
 *
 * The static `BlogConversionCta` is passive: a reader who never reaches the
 * footer never meets it. This is the same pitch, same wording, fired once the
 * reader has demonstrably READ something — scroll depth inside a DOCUMENT, never
 * on arrival and never on a hub. A reader who bounced in three seconds is not
 * asked for an account.
 *
 * TWO depths per document (2026-08-02): `ENGAGE_RATIOS` = 35% and 80%, each
 * firing at most once, so an engaged reader working through a whole نظام meets
 * the pitch twice while a reader who stops halfway meets it once. Both belong to
 * ONE round — the session cap counts rounds (documents), not raw impressions.
 *
 * ⚠ T9 — THIS COMPONENT IS PURE CLIENT, PERMANENTLY. Zero server data, zero
 * fetches, nothing that can vary per visitor before hydration. The library runs
 * ISR with a SHARED cache and no auth variance (see the header comment on
 * `HubCtaWall`): anything server-side added here would be baked into the page
 * every subsequent visitor is served. Nothing in this file may ever move into
 * `lib/library/api.ts` or a server component.
 *
 * ⚠ Googlebot renders JS but does not scroll and does not dwell, so the trigger
 * simply never fires for the crawler — no user-agent branch, no cloaking, and no
 * interstitial in the indexed render. That is why the primary signal is a
 * gesture rather than a timer.
 */
export function AnonCtaPopup() {
  const pathname = usePathname() ?? "";
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);

  const [open, setOpen] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);

  /**
   * Gate 1 + gate 2 of the suppression chain (§5).
   *
   * ⚠ T7 — `isLoading === false` is the half that matters: without it the popup
   * flashes at signed-in readers on every page load, while the session probe is
   * still in flight. Same rule, same order, as `BlogConversionCta`.
   */
  const armed = !isLoading && !isAuthenticated && isEligibleDoc(pathname);

  /** Per-document trigger progress — which thresholds are spent, and when. */
  const progressRef = useRef<DocProgress>(freshProgress(""));
  /**
   * Mirrors `open` for the trigger closure, which does not re-run when the
   * dialog opens. A threshold must never fire ON TOP of a popup already on
   * screen: the body is scroll-locked while one is open, but a held dwell/gap
   * timer can still resolve behind it.
   */
  const openRef = useRef(false);
  /** Re-entry point for the trigger, called when a popup leaves the screen. */
  const reevaluateRef = useRef<(() => void) | null>(null);

  // ── Axis 2 of the cadence: a NEW eligible document drains the quiet period. ──
  // Deduped inside `noteEligibleDoc` on the stored `lastDoc` (T8), so a remount
  // or a re-render of the same path is not a second document.
  useEffect(() => {
    if (!armed) return;
    noteEligibleDoc(pathname);
  }, [armed, pathname]);

  // A client-side navigation must not leave the previous document's pitch on
  // screen — the popup is tied to the page that earned it.
  useEffect(() => {
    openRef.current = false;
    setOpen(false);
  }, [pathname]);

  // `prefers-reduced-motion` → fade only (§6). Handled as inline custom
  // properties rather than classes because inline style is the only layer that
  // is guaranteed to beat `ui/dialog`'s own animation utilities.
  useEffect(() => {
    if (!armed) return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(query.matches);
    const onChange = (event: MediaQueryListEvent) =>
      setReducedMotion(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [armed]);

  // ── The trigger (§3) + the rest of the gate chain (§5) ─────────────────────
  useEffect(() => {
    if (!armed) return;

    // Per-document state, keyed by path. Survives a re-run of this effect (an
    // auth-probe flip) so a document cannot regain a threshold it already spent.
    if (progressRef.current.path !== pathname) {
      progressRef.current = freshProgress(pathname);
    }
    const doc = progressRef.current;
    if (doc.spent) return;

    // Cheap pre-check: muted, capped or inside the quiet period → never even
    // attach a listener. Re-read at fire time, because our own writes move it.
    if (!canFire(readAnonCtaState(), pathname)) return;

    const startedAt = Date.now();
    let disposed = false;
    let hasScrolled = false;
    let rafId = 0;
    let dwellTimer: ReturnType<typeof setTimeout> | undefined;
    let shortTimer: ReturnType<typeof setTimeout> | undefined;
    let cancelVisibility: (() => void) | undefined;
    let bodyObserver: ResizeObserver | undefined;

    function teardown(): void {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", syncMode);
      bodyObserver?.disconnect();
      if (rafId) window.cancelAnimationFrame(rafId);
      rafId = 0;
      if (dwellTimer) clearTimeout(dwellTimer);
      if (shortTimer) clearTimeout(shortTimer);
      dwellTimer = undefined;
      shortTimer = undefined;
    }

    /**
     * One fire attempt. `index` is the threshold being spent, or `null` for the
     * short-page path (which yields a single impression per document).
     *
     * Gates 3–5 are evaluated HERE, at the moment of firing, and a blocked fire
     * is DROPPED, not queued (§5) — the THRESHOLD is spent either way, so a
     * blocked 35% never comes back, while the reader's 80% still can.
     */
    function attempt(index: number | null): void {
      if (disposed || doc.spent) return;
      // A popup is on screen: this is not a drop, just not now — the `open`
      // effect re-enters the trigger the moment it closes.
      if (openRef.current) return;

      if (index === null) {
        doc.spent = true;
      } else {
        doc.done[index] = true;
        if (doc.done.every(Boolean)) doc.spent = true;
      }
      if (doc.spent) teardown();

      // Gate 3 — cadence for THIS document (an open round may still fire its
      // later threshold; a new round needs the cap and the quiet period).
      if (!canFire(readAnonCtaState(), pathname)) return;

      // Gate 4 — no other dialog open. The reference-source dialog, the usage
      // limits dialog, the onboarding tour and the اسأل ريحان panel all register
      // as `role="dialog"`; stacking a pitch on top of one is the worst version
      // of this feature.
      if (document.querySelector('[role="dialog"]') !== null) return;

      // Gate 5 — no anon CTA already on screen (T6).
      cancelVisibility = whenAnonCtaVisibility((visible) => {
        if (disposed || visible || openRef.current) return;
        // T11 — an impression that cannot be PERSISTED must not be shown, or a
        // broken sessionStorage becomes an unbounded loop of popups.
        if (!recordImpression(pathname)) return;
        openRef.current = true;
        setOpen(true);
      });
    }

    /**
     * The scroll path: the lowest UNFIRED threshold the reader has crossed,
     * subject to two floors — `MIN_DWELL_MS` since the document mounted, and
     * `MIN_GAP_MS` since the previous popup on this document left the screen.
     *
     * The gap is what stops a single fling from top to bottom — which crosses
     * BOTH thresholds in one gesture — from stacking two popups.
     *
     * When a floor has not elapsed the fire is HELD on a timer and re-checked
     * rather than dropped: a reader who flings and then stops scrolling emits no
     * further scroll events, and a listener-only implementation would never fire
     * for them.
     */
    function evaluateScroll(): void {
      if (disposed || doc.spent) return;
      if (openRef.current) return;
      // ⚠ The long-page path REQUIRES a real gesture. A document between 1.2 and
      // ~1.8 viewports already sits above 0.35 at scroll 0 — with the lower
      // threshold this trips more easily than it did at 0.55 — and a
      // gesture-free fire would also put the popup into a headless render (T10).
      if (!hasScrolled) return;
      // Short pages have no meaningful scroll signal — the dwell timer owns them.
      if (isShortPage()) return;

      const index = nextThreshold(scrollProgress(), doc.done);
      if (index === -1) return;

      const now = Date.now();
      const untilDwell = MIN_DWELL_MS - (now - startedAt);
      const untilGap = doc.lastShotEndedAt
        ? MIN_GAP_MS - (now - doc.lastShotEndedAt)
        : 0;
      const wait = Math.max(untilDwell, untilGap, 0);

      if (wait <= 0) {
        attempt(index);
        return;
      }
      if (!dwellTimer) {
        dwellTimer = setTimeout(() => {
          dwellTimer = undefined;
          evaluateScroll();
        }, wait);
      }
    }

    function onScroll(): void {
      if (doc.spent) return;
      hasScrolled = true;
      if (rafId) return;
      rafId = window.requestAnimationFrame(() => {
        rafId = 0;
        evaluateScroll();
      });
    }

    /**
     * ⚠ T4 — the case the naive trigger gets wrong. A one-screen مادة has scroll
     * progress 1.0 on load, so a plain depth check fires instantly on exactly
     * the pages where the reader has read the least. When the document is not
     * meaningfully scrollable the scroll signal is ABANDONED and a
     * SHORT_PAGE_DWELL timer substitutes for it — ONE impression, and no second
     * threshold, because there is no depth to cross.
     *
     * ⚠ T5 — and that reading is not stable: `FullContentGate` can swap in a
     * full document and change the page height dramatically, so a "short" page
     * becomes long mid-visit. Re-evaluated on `resize` and on every
     * `ResizeObserver` callback for `document.body`.
     */
    function syncMode(): void {
      if (disposed || doc.spent) return;

      if (isShortPage()) {
        if (!shortTimer) {
          const waited = Date.now() - startedAt;
          shortTimer = setTimeout(
            () => {
              shortTimer = undefined;
              // Re-check: the page may have grown while the timer ran.
              if (isShortPage()) attempt(null);
            },
            Math.max(0, SHORT_PAGE_DWELL - waited),
          );
        }
        return;
      }

      // The page is (now) scrollable: drop the substitute timer and hand the
      // decision back to the scroll signal.
      if (shortTimer) {
        clearTimeout(shortTimer);
        shortTimer = undefined;
      }
      evaluateScroll();
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", syncMode);
    if (typeof ResizeObserver !== "undefined") {
      bodyObserver = new ResizeObserver(() => syncMode());
      bodyObserver.observe(document.body);
    }
    reevaluateRef.current = syncMode;
    syncMode();

    return () => {
      disposed = true;
      reevaluateRef.current = null;
      teardown();
      cancelVisibility?.();
    };
  }, [armed, pathname]);

  // A popup just left the screen: the reader may already be past the next
  // threshold, and without this nothing would re-evaluate until they scroll
  // again — which, on a page they have already read to the end, may be never.
  useEffect(() => {
    if (open) return;
    reevaluateRef.current?.();
  }, [open]);

  const handleOpenChange = useCallback((next: boolean) => {
    if (!next) {
      openRef.current = false;
      // The same-document gap runs from the moment the popup LEFT the screen,
      // not from when it appeared: measuring from the impression would let the
      // second threshold open the instant a reader dismissed a first popup they
      // had spent six seconds reading.
      progressRef.current.lastShotEndedAt = Date.now();
    }
    setOpen(next);
  }, []);

  // Clicking either CTA mutes the session BEFORE navigating (§4): the reader is
  // on their way to /login, and pitching them again after a back button — in the
  // same session — is nagging.
  const handleCtaClick = useCallback(() => {
    muteAnonCta();
    handleOpenChange(false);
  }, [handleOpenChange]);

  const next = encodeURIComponent(pathname || "/");
  // Built inline on purpose: `lib/safe-next.ts` validates `next` on the READ
  // side (LoginForm + the auth callback), which is where an attacker-controlled
  // value actually arrives, and its allowlist is deliberately WIDER than the
  // five wings this popup runs on. Nothing to import here.
  const registerHref = `/login?next=${next}&mode=register`;
  const loginHref = `/login?next=${next}`;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        dir="rtl"
        // `prefers-reduced-motion` → identity transforms, so `animate-in` is
        // reduced to the opacity half of the keyframe: a fade, no slide, no zoom.
        style={reducedMotion ? STILL_MOTION : undefined}
        className={cn(
          // ── < sm: BOTTOM SHEET, capped at 60vh. ──────────────────────────
          // This constraint is NOT aesthetic. An interstitial that covers the
          // main content of a search landing is the exact pattern Google
          // demotes; a sheet occupying the bottom 60% leaves the article
          // readable and sits outside that definition. Do not drop it.
          "bottom-0 top-auto start-0 end-0 mx-auto max-h-[60vh] max-w-full translate-y-0 overflow-y-auto rounded-t-2xl",
          // The sheet is flush with the screen edge and `viewportFit:"cover"`
          // extends that edge under the home indicator — pad the DialogContent's
          // own p-6 by the inset so the CTA buttons stay tappable.
          "max-sm:pb-[calc(1.5rem+env(safe-area-inset-bottom))]",
          // Horizontal transform centring is removed at BOTH breakpoints so the
          // entrance keyframe (which REPLACES `transform`) never has to guess
          // the resting offset — in RTL the shadcn default would otherwise slide
          // a full-width sheet across the whole screen.
          "translate-x-0 rtl:translate-x-0",
          "max-sm:data-[state=open]:slide-in-from-bottom-8 max-sm:data-[state=open]:slide-in-from-left-0 max-sm:data-[state=open]:zoom-in-100",
          "max-sm:data-[state=closed]:slide-out-to-bottom-8 max-sm:data-[state=closed]:slide-out-to-left-0 max-sm:data-[state=closed]:zoom-out-100",
          // ── ≥ sm: centred modal. ─────────────────────────────────────────
          "sm:bottom-auto sm:top-[50%] sm:max-h-[85vh] sm:max-w-md sm:translate-y-[-50%] sm:rounded-2xl",
          "sm:data-[state=open]:slide-in-from-left-0 sm:data-[state=closed]:slide-out-to-left-0",
        )}
      >
        <DialogHeader className="items-center text-center sm:text-center">
          <div className="mb-1 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <Sparkles aria-hidden="true" className="h-6 w-6" />
          </div>
          <DialogTitle className="text-lg font-bold text-foreground">
            {anonCtaCopy.title}
          </DialogTitle>
          <DialogDescription className="mx-auto max-w-sm text-sm leading-relaxed">
            {anonCtaCopy.body}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col items-stretch gap-2 pt-1 sm:flex-row sm:justify-center">
          <Link
            href={registerHref}
            onClick={handleCtaClick}
            className={cn(buttonVariants({ variant: "default", size: "lg" }))}
          >
            <Sparkles aria-hidden="true" className="h-4 w-4 shrink-0" />
            {anonCtaCopy.primaryCta}
          </Link>
          <Link
            href={loginHref}
            onClick={handleCtaClick}
            className={cn(buttonVariants({ variant: "outline", size: "lg" }))}
          >
            {anonCtaCopy.secondaryCta}
            <ArrowLeft aria-hidden="true" className="h-4 w-4 shrink-0" />
          </Link>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------
// Per-document trigger progress
// ------------------------------------------------------------------

interface DocProgress {
  /** The pathname this progress belongs to. */
  path: string;
  /**
   * One flag per `ENGAGE_RATIOS` entry — spent whether the fire showed or was
   * dropped by a gate (drop, don't queue).
   */
  done: boolean[];
  /** When the last popup on this document LEFT the screen (epoch ms, 0 = none). */
  lastShotEndedAt: number;
  /**
   * Nothing more can fire on this document: every threshold is spent, or the
   * short-page path produced its single impression.
   */
  spent: boolean;
}

function freshProgress(path: string): DocProgress {
  return {
    path,
    done: ENGAGE_RATIOS.map(() => false),
    lastShotEndedAt: 0,
    spent: false,
  };
}

/**
 * The LOWEST unfired threshold the reader has crossed, or -1.
 *
 * Lowest-first matters for the fling case: a reader who lands at the bottom has
 * crossed both, so the 35% one is spent first and the pair still arrives in
 * order — with `MIN_GAP_MS` between them.
 */
function nextThreshold(progress: number, done: boolean[]): number {
  for (let i = 0; i < ENGAGE_RATIOS.length; i += 1) {
    if (!done[i] && progress >= ENGAGE_RATIOS[i]) return i;
  }
  return -1;
}

// ------------------------------------------------------------------
// DOM helpers — measurement only, no state
// ------------------------------------------------------------------

/**
 * How much taller than the viewport a document must be before its scroll
 * position means anything. Below this it is a "one screen" page and §3's dwell
 * fallback takes over. A DOM measurement, not a cadence knob — hence here and
 * not in `config.ts`.
 */
const SCROLLABLE_FACTOR = 1.2;

function isShortPage(): boolean {
  return (
    document.documentElement.scrollHeight <=
    window.innerHeight * SCROLLABLE_FACTOR
  );
}

/** Share of the document read so far — the viewport bottom over total height. */
function scrollProgress(): number {
  const total = document.documentElement.scrollHeight;
  if (total <= 0) return 0;
  return (window.scrollY + window.innerHeight) / total;
}

/**
 * Gate 5 (§5, T6) — is any EXISTING anon conversion surface on screen right now?
 *
 * The tagged surfaces are `FullContentGate`'s anonymous reveal panel, the `Wall`
 * in `HubCtaWall` and `BlogConversionCta`, each carrying `data-anon-cta`. A
 * reader looking at «سجّل مجاناً لعرض النص كاملاً» does not need a modal that
 * says the same thing.
 *
 * One-shot `IntersectionObserver`, created at fire time and disconnected in its
 * first callback — no standing observer, nothing to keep in sync with the DOM.
 * Answers `false` immediately when the page carries no tagged surface at all,
 * and falls back to rectangle maths where `IntersectionObserver` is missing.
 *
 * Returns a canceller for the case where the component unmounts inside that one
 * frame.
 */
function whenAnonCtaVisibility(
  decide: (visible: boolean) => void,
): () => void {
  const targets = Array.from(document.querySelectorAll("[data-anon-cta]"));
  if (targets.length === 0) {
    decide(false);
    return () => {};
  }

  if (typeof IntersectionObserver === "undefined") {
    decide(targets.some(isRectOnScreen));
    return () => {};
  }

  const observer = new IntersectionObserver((entries) => {
    observer.disconnect();
    decide(entries.some((entry) => entry.isIntersecting));
  });
  targets.forEach((target) => observer.observe(target));
  return () => observer.disconnect();
}

function isRectOnScreen(element: Element): boolean {
  const rect = element.getBoundingClientRect();
  return (
    rect.bottom > 0 &&
    rect.right > 0 &&
    rect.top < window.innerHeight &&
    rect.left < window.innerWidth
  );
}

/**
 * Identity values for `tailwindcss-animate`'s enter/exit keyframe variables.
 * Applied inline (highest precedence, and unlike a class it cannot lose to a
 * media-query utility) so a reduced-motion reader gets the opacity half of the
 * animation and nothing else.
 */
const STILL_MOTION = {
  "--tw-enter-translate-x": "0px",
  "--tw-enter-translate-y": "0px",
  "--tw-enter-scale": "1",
  "--tw-exit-translate-x": "0px",
  "--tw-exit-translate-y": "0px",
  "--tw-exit-scale": "1",
} as CSSProperties;
