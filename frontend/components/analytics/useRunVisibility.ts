"use client";

import { useEffect } from "react";
import {
  flushOpenWorkspaceItem,
  trackPageLeave,
  trackTabHidden,
  trackTabVisible,
} from "@/components/analytics/run-tracker";

/**
 * The SINGLE owner of `tab_hidden` / `tab_visible` / `page_leave`
 * (`.claude/plans/product_analytics.md` §3b, Phase 3).
 *
 * Single owner is not a style preference: two components listening to
 * `visibilitychange` would double every abandonment number, and the whole
 * point of these three events is a count. The listeners are therefore
 * installed at MODULE scope and reference-counted, so the hook can be called
 * from more than one place (`useSendMessage` is mounted by both the chat page
 * and `ChatContainer`) and still produce exactly one event per browser event.
 *
 * Every event is stamped with `run_state` from the run tracker — the field
 * that separates *left before the answer* from *left after reading it* on the
 * exact same browser event.
 *
 * Traps this file exists to respect:
 *
 * - **T11** — `pagehide` is the only true close signal, and mobile Safari
 *   drops it often, on exactly the population most likely to background a long
 *   run. `page_leave` is therefore a CONFIRMED-close subset, never the
 *   definition of "left"; the honest bucket is "did not return in this
 *   session", derived from `tab_hidden` with no following `tab_visible`.
 * - **T12** — `pagehide` with `persisted === true` is bfcache: the user tapped
 *   back and the page is frozen for reuse. That is not a departure and must
 *   not be counted as one.
 * - **T13** — no `setInterval` / `setTimeout` anywhere near elapsed time.
 *   Hidden tabs throttle timers to ≥1s or worse, which is precisely when these
 *   measurements happen. `ms_hidden` is a difference of two `Date.now()`
 *   stamps taken at the two events themselves.
 */

let listenerRefCount = 0;
/** `Date.now()` at the last `visibilitychange → hidden`. T13: a stamp, not a timer. */
let hiddenAt: number | null = null;

function handleVisibilityChange(): void {
  if (document.visibilityState === "hidden") {
    hiddenAt = Date.now();
    trackTabHidden();
    return;
  }
  const msHidden = hiddenAt === null ? null : Date.now() - hiddenAt;
  hiddenAt = null;
  trackTabVisible(msHidden);
}

function handlePageHide(event: PageTransitionEvent): void {
  // T12 — bfcache freeze, not a departure.
  if (event.persisted) return;
  // Close out an open workspace item first so its dwell rides the same flush
  // as the departure instead of being dropped.
  flushOpenWorkspaceItem();
  trackPageLeave();
}

/**
 * Mounts the visibility listeners for as long as any chat surface is alive.
 * Safe to call from several components — the listeners are installed once.
 */
export function useRunVisibility(): void {
  useEffect(() => {
    if (typeof document === "undefined") return;

    listenerRefCount += 1;
    if (listenerRefCount === 1) {
      document.addEventListener("visibilitychange", handleVisibilityChange);
      window.addEventListener("pagehide", handlePageHide);
    }

    return () => {
      listenerRefCount -= 1;
      if (listenerRefCount === 0) {
        document.removeEventListener("visibilitychange", handleVisibilityChange);
        window.removeEventListener("pagehide", handlePageHide);
        hiddenAt = null;
      }
    };
  }, []);
}
