"use client";

import { useCallback, useEffect, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, Sparkles } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { buttonVariants } from "@/components/ui/button";
import { loginHref } from "@/lib/safe-next";
import { cn } from "@/lib/utils";
import { SEARCH_CTA_COPY } from "@/lib/search/copy";
import {
  trackGateCtaClick,
  trackGateDismiss,
  trackGateView,
  type GateCta,
} from "@/components/analytics/useGateImpression";

/**
 * The anon conversion modal behind every search box (D9).
 *
 * Search is registered-only, so an anonymous visitor still SEES the box and can
 * still click it — and the click opens this instead of running a query. The box
 * is not disabled and not hidden: a dead control teaches nothing, while a live
 * one that answers with a pitch is the conversion moment the plan is after.
 *
 * ── Why this is not the `AnonCtaPopup` ────────────────────────────────────────
 * `anon_conversion_popup.md` fires on scroll depth inside a DOCUMENT and
 * explicitly excludes hubs — a directory grid has no reading depth, so a scroll
 * trigger there measures nothing but a flick. A search-box click is a different
 * signal entirely: an intent gesture, deliberate, unambiguous. So it gets its
 * own trigger (the box) and its own copy (§0.1), and shares that plan's
 * `?next=` carrier and nothing else. There is no session cadence here and no
 * `{n+1}` quiet period: the modal appears exactly as often as the visitor asks
 * for it by clicking.
 *
 * ── SEO ─────────────────────────────────────────────────────────────────────
 * No interstitial risk, and for the same reason the scroll popup has none: it
 * fires on a gesture Googlebot never performs. No user-agent branch, no
 * cloaking — the identical JS ships to every client. (`?q=` URLs are separately
 * `noindex` via the `X-Robots-Tag` rule in `middleware.ts`.)
 *
 * ── The `?next=` carrier ────────────────────────────────────────────────────
 * `returnTo` is the page the visitor was on INCLUDING its query string, so a
 * shared `/regulations?q=إجازة الأمومة` link returns them to that exact search
 * once they have an account — which is the whole point of converting them here
 * rather than dumping them on `/chat`. `loginHref` → `safeNext` is the single
 * return-to-page mechanism in the app (`lib/safe-next.ts`); `post-login-intent`
 * is a different tool for richer intents and is untouched by this component.
 */
export function SearchCtaModal({
  open,
  onOpenChange,
  returnTo,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Site-relative path (query string allowed) to return to after auth. */
  returnTo: string;
}) {
  // ── Analytics (product_analytics.md §5.3) ─────────────────────────────────
  // This surface needs no `IntersectionObserver`: an open modal IS seen, and it
  // only opens on a deliberate click, so `open` is the honest impression signal.
  //
  // ⚠ T4 — `path` is `usePathname()`, NEVER `returnTo`. `returnTo` deliberately
  // carries the query string («/regulations?q=إجازة الأمومة») so the reader lands
  // back on their search, and that `q` is user-typed legal text in a product for
  // lawyers. It must never reach the beacon.
  const pathname = usePathname() ?? "";
  /** The impression has been reported for the currently-open modal. */
  const shownRef = useRef(false);
  /** A CTA carried the reader away — that is a conversion, not a dismissal. */
  const ctaClickedRef = useRef(false);

  useEffect(() => {
    if (!open) {
      shownRef.current = false;
      return;
    }
    if (shownRef.current) return;
    shownRef.current = true;
    ctaClickedRef.current = false;
    trackGateView("search_modal", pathname);
  }, [open, pathname]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      // Closed by Escape, the X or the overlay, with no CTA taken: the visitor
      // asked what search costs and decided against an account.
      if (!next && !ctaClickedRef.current) {
        trackGateDismiss("search_modal", pathname);
      }
      onOpenChange(next);
    },
    [onOpenChange, pathname],
  );

  const handleCtaClick = useCallback(
    (cta: GateCta) => {
      ctaClickedRef.current = true;
      trackGateCtaClick("search_modal", pathname, cta);
    },
    [pathname],
  );

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        dir="rtl"
        className="max-w-md gap-5 rounded-2xl sm:rounded-2xl"
      >
        <DialogHeader className="items-center text-center sm:text-center">
          <div className="mb-1 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <Sparkles aria-hidden="true" className="h-6 w-6" />
          </div>
          <DialogTitle className="text-lg font-bold text-foreground">
            {SEARCH_CTA_COPY.title}
          </DialogTitle>
          {/* Line 1 answers «why chat instead of a result list». Line 2 names
              what the account unlocks — search across the wings — and without
              it the modal reads as a bait-and-switch to a reader who only
              wanted to filter the grid (§0.1). Both live in copy.ts. */}
          <DialogDescription className="mx-auto max-w-sm text-sm leading-relaxed">
            {SEARCH_CTA_COPY.body}
          </DialogDescription>
        </DialogHeader>

        <p className="mx-auto -mt-2 max-w-sm text-center text-sm leading-relaxed text-text-secondary">
          {SEARCH_CTA_COPY.unlock}
        </p>

        <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:justify-center">
          {/* `mode=register` opens /login directly on signup, so a button that
              says «ابدأ الآن» means what it says (anon_conversion_popup §7.7). */}
          <Link
            href={loginHref(returnTo, { register: true })}
            onClick={() => handleCtaClick("register")}
            className={cn(buttonVariants({ variant: "default", size: "lg" }))}
          >
            <Sparkles aria-hidden="true" className="h-4 w-4 shrink-0" />
            {SEARCH_CTA_COPY.primaryCta}
          </Link>
          <Link
            href={loginHref(returnTo)}
            onClick={() => handleCtaClick("login")}
            className={cn(buttonVariants({ variant: "outline", size: "lg" }))}
          >
            {SEARCH_CTA_COPY.secondaryCta}
            <ArrowLeft aria-hidden="true" className="h-4 w-4 shrink-0" />
          </Link>
        </div>
      </DialogContent>
    </Dialog>
  );
}
