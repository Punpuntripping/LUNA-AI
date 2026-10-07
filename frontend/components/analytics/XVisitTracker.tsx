"use client";

/**
 * X8 — "engaged visit": an X ad click whose landing stayed VISIBLE for 3 s
 * (`request_to_luna_x_conversions.md` addendum 2026-10-07). Bounces and
 * accidental clicks never reach X; the backend turns this into a Conversions
 * API event carrying only `twclid`.
 *
 * Also owns the X1 landing capture (`captureLandingAttribution`) — it must run
 * before the timer reads the stash, and this is the one leaf mounted for that.
 *
 * Mounted ONCE in `providers.tsx`, which survives client-side navigation, so
 * the visible-time count runs across pages in the same tab: 2 s on the landing
 * + 1 s on the next page counts.
 *
 * ⚠ T13 — the timer only SCHEDULES the check; elapsed time is a sum of
 * `Date.now()` differences over visible spans, and it only runs while visible.
 *
 * ⚠ A sessionStorage latch (= the twclid) stops a reload or a second page from
 * re-firing for the same click. Fails closed and never throws.
 */

import { useEffect } from "react";
import {
  captureLandingAttribution,
  readLandingAttribution,
} from "@/components/analytics/landing-attribution";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const ENDPOINT = `${API_BASE}/api/v1/attribution/x-visit`;

export const X_VISIT_SENT_STORAGE_KEY = "rayhan_x_visit_sent_v1";
const ENGAGED_MS = 3_000;

function alreadySent(twclid: string): boolean {
  try {
    return window.sessionStorage.getItem(X_VISIT_SENT_STORAGE_KEY) === twclid;
  } catch {
    // Unusable storage → no latch possible. Skip rather than risk a resend
    // per page; X dedupes on the twclid anyway, but quiet is the default.
    return true;
  }
}

function send(twclid: string): void {
  try {
    window.sessionStorage.setItem(X_VISIT_SENT_STORAGE_KEY, twclid);
  } catch {
    return;
  }
  try {
    // text/plain keeps it a "simple" CORS request (no preflight); the backend
    // reads raw bytes. keepalive lets it outlive a navigation away.
    void fetch(ENDPOINT, {
      method: "POST",
      keepalive: true,
      credentials: "omit",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ twclid }),
    }).catch(() => {});
  } catch {
    // T9.
  }
}

export function XVisitTracker(): null {
  useEffect(() => {
    captureLandingAttribution();

    let twclid: string | undefined;
    try {
      twclid = readLandingAttribution().twclid;
    } catch {
      return;
    }
    if (!twclid || alreadySent(twclid)) return;
    const clickId = twclid;

    let visibleMs = 0;
    let visibleSince: number | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let done = false;

    const clear = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    };

    const check = () => {
      if (done || visibleSince === null) return;
      const total = visibleMs + (Date.now() - visibleSince);
      if (total >= ENGAGED_MS) {
        done = true;
        clear();
        send(clickId);
        return;
      }
      clear();
      timer = setTimeout(check, ENGAGED_MS - total);
    };

    const onVisibility = () => {
      try {
        if (done) return;
        if (document.visibilityState === "visible") {
          if (visibleSince === null) visibleSince = Date.now();
          check();
        } else {
          if (visibleSince !== null) visibleMs += Date.now() - visibleSince;
          visibleSince = null;
          clear();
        }
      } catch {
        // T9.
      }
    };

    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      clear();
    };
  }, []);

  return null;
}
