"use client";

/**
 * Product analytics — the global tracker
 * (`.claude/plans/product_analytics.md` §5.2, Phase 1).
 *
 * Renders nothing. Mounted ONCE, in `components/providers.tsx`, which already
 * wraps the whole app from the root layout — so one mount covers every route:
 * public wings, blog, chat, checkout.
 *
 * ⚠ It must NOT copy `AnonCtaPopup`'s per-shell mounting (`LibraryPageShell` /
 * `BlogPageShell`). That pattern would miss the entire authed app, i.e. exactly
 * the half of the funnel that question 6 ("authed or anonymous?") is about.
 *
 * ⚠ T2 — PURE CLIENT, PERMANENTLY. The library wings run ISR with a SHARED
 * cache; anything emitted from a server render is baked into the page every
 * later visitor receives.
 *
 * Phase 1 only — three events:
 *
 *   `session_start`  once per TAB, keyed off the MINTING of the session key,
 *                    not off mount: the key survives a reload inside the same
 *                    tab, so a mount-keyed event would multiply session counts
 *                    by the number of reloads.
 *   `page_view`      on every `usePathname()` change, client-side nav included.
 *                    This is the denominator of bounce rate (§3, derived).
 *   `page_exit`      on `visibilitychange → hidden` and on leaving the page by
 *                    client-side navigation, carrying `dwell_ms` and
 *                    `max_scroll_pct`. At most ONE per page visit — a reader
 *                    who hides the tab, returns and then navigates away must
 *                    not have their time on that page counted twice.
 *
 * ⚠ T4 — the raw query string NEVER travels. `?q=` on the navigation search
 * surfaces is user-typed legal text in a product for lawyers. Only the pathname
 * is sent, plus `utm_source` / `utm_medium` / `utm_campaign` read BY NAME.
 *
 * ⚠ T5 — departure hangs off `visibilitychange`, never `unload`, which does not
 * fire reliably on mobile Safari — precisely the population being measured.
 *
 * ⚠ T13 — no timer ever measures elapsed time. Hidden tabs throttle
 * `setTimeout`/`setInterval` to ≥1s and often far worse, which is exactly when
 * these measurements are taken. `dwell_ms` is a difference of `Date.now()`
 * stamps captured at the events themselves. (The `requestAnimationFrame` below
 * coalesces scroll work; it measures nothing.)
 *
 * ⚠ T9 — every path here is guarded. A storage failure, a blocked beacon or a
 * 503 must be a no-op; a reader must never see a degraded page because a
 * tracker failed.
 */

import { useCallback, useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { flushAnalytics, track } from "@/lib/analytics/client";
import { ensureAnalyticsSession } from "@/lib/analytics/session";

/** Entry attribution: the referrer's HOST only — never the full URL (§2). */
function referrerHost(): string | undefined {
  try {
    const referrer = document.referrer;
    if (!referrer) return undefined;
    // The source page's own query string can carry someone else's search terms,
    // so the URL is parsed and everything but the hostname is discarded.
    const host = new URL(referrer).hostname;
    return host || undefined;
  } catch {
    return undefined;
  }
}

/**
 * The three campaign parameters, by name. ⚠ T4 — nothing else is ever read out
 * of the query string.
 */
function utmParams(): Record<string, string> {
  const out: Record<string, string> = {};
  try {
    const params = new URLSearchParams(window.location.search);
    for (const key of ["utm_source", "utm_medium", "utm_campaign"] as const) {
      const value = params.get(key);
      if (value) out[key] = value.slice(0, 120);
    }
  } catch {
    // No query, no attribution. Not an error.
  }
  return out;
}

export function AnalyticsTracker(): null {
  const pathname = usePathname();

  /** The path whose visit is currently OPEN. `null` before the first view. */
  const openPathRef = useRef<string | null>(null);
  /** `Date.now()` at the moment that visit opened — the t₀ of `dwell_ms`. */
  const enteredAtRef = useRef<number>(Date.now());
  /** Deepest scroll reached on the open visit, 0–100. */
  const maxScrollRef = useRef<number>(0);
  /** The open visit already reported its exit — at most one per visit. */
  const exitedRef = useRef<boolean>(false);
  /** rAF coalescing flag for the scroll listener. */
  const scrollTickingRef = useRef<boolean>(false);

  const measureScroll = useCallback(() => {
    try {
      const doc = document.documentElement;
      const viewport = window.innerHeight || doc.clientHeight || 0;
      const total = Math.max(doc.scrollHeight || 0, document.body?.scrollHeight || 0);
      if (!viewport || !total) return;

      const seen = Math.min(total, (window.scrollY || doc.scrollTop || 0) + viewport);
      const pct = Math.round((seen / total) * 100);
      const clamped = Math.max(0, Math.min(100, pct));
      if (clamped > maxScrollRef.current) maxScrollRef.current = clamped;
    } catch {
      // A layout read that throws costs one scroll sample, nothing more.
    }
  }, []);

  /**
   * Close the open visit. Idempotent: the second caller is a no-op, so a
   * hide-then-navigate sequence reports one exit and one dwell, not two.
   */
  const closeVisit = useCallback(() => {
    const path = openPathRef.current;
    if (!path || exitedRef.current) return;
    exitedRef.current = true;
    track("page_exit", {
      path,
      dwell_ms: Math.max(0, Date.now() - enteredAtRef.current),
      max_scroll_pct: maxScrollRef.current,
    });
  }, []);

  // ---------------------------------------------------------------
  // Mount: `session_start` + the departure and scroll listeners.
  // Declared BEFORE the page_view effect so the tab's first event is
  // `session_start`, as §3 describes it ("first event of a tab").
  // ---------------------------------------------------------------
  useEffect(() => {
    // `created` is true only on the call that minted the key, so a reload
    // inside the same tab — and React StrictMode's double-invoked effect in
    // development — resolves to false and emits nothing.
    const { sessionKey, created } = ensureAnalyticsSession();
    if (sessionKey && created) {
      let entryPath: string | undefined;
      try {
        entryPath = window.location.pathname || undefined;
      } catch {
        entryPath = undefined;
      }
      const host = referrerHost();
      track("session_start", {
        ...(entryPath ? { entry_path: entryPath } : {}),
        ...(host ? { referrer_host: host } : {}),
        ...utmParams(),
      });
    }

    const onVisibility = () => {
      // ⚠ T5 — `visibilitychange → hidden` is the only signal that survives a
      // backgrounded tab on mobile Safari.
      if (document.visibilityState !== "hidden") return;
      closeVisit();
      // The hidden tab's timers are throttled and may never run again, so the
      // debounce cannot be trusted to ship this one (T13).
      flushAnalytics();
    };

    const onScroll = () => {
      if (scrollTickingRef.current) return;
      scrollTickingRef.current = true;
      window.requestAnimationFrame(() => {
        scrollTickingRef.current = false;
        measureScroll();
      });
    };

    try {
      document.addEventListener("visibilitychange", onVisibility);
      window.addEventListener("scroll", onScroll, { passive: true });
    } catch {
      // A document that refuses listeners loses depth and exit, not the page.
    }

    return () => {
      try {
        document.removeEventListener("visibilitychange", onVisibility);
        window.removeEventListener("scroll", onScroll);
      } catch {
        // Nothing to undo.
      }
    };
  }, [closeVisit, measureScroll]);

  // ---------------------------------------------------------------
  // Every route change: close the previous visit, open a new one,
  // emit `page_view`.
  // ---------------------------------------------------------------
  useEffect(() => {
    if (!pathname) return;
    // Guards a re-render, a remount and StrictMode's double invocation: the
    // same path is the same visit, and counting it twice would halve every
    // bounce rate.
    if (openPathRef.current === pathname) return;

    // `openPathRef` still holds the PREVIOUS path here, which is the visit
    // being closed.
    closeVisit();

    openPathRef.current = pathname;
    enteredAtRef.current = Date.now();
    maxScrollRef.current = 0;
    exitedRef.current = false;

    // ⚠ T4 — the pathname only. No query string, ever.
    track("page_view", { path: pathname });
    measureScroll();
  }, [pathname, closeVisit, measureScroll]);

  return null;
}
