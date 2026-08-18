"use client";

/**
 * `signup_completed` — the last step of the funnel in `product_analytics.md`
 * §5.4 (`gate_view` → `gate_cta_click` → `signup_started` → **signup_completed**).
 *
 * Until this shipped the name existed in the taxonomy and in the backend's
 * `PUBLIC_EVENT_NAMES`, and was emitted by nothing: step 4 of the primary
 * funnel did not exist, so "how many of the readers we gated actually opened an
 * account?" was unanswerable by construction. That is exactly the failure the
 * header comment in `lib/analytics/events.ts` warns about — an event nobody
 * emits reads exactly like an event nobody triggers.
 *
 * ## Why account AGE, and not the signup call sites
 *
 * There are two ways to create an account and neither can fire this itself:
 *
 *   1. **Email + password** (`auth-store.register` → `supabase.auth.signUp`).
 *      With email confirmation ON — production — `signUp()` returns NO session.
 *      The row exists, but the visitor is not authenticated until they click
 *      the link in their inbox, which may be minutes or hours later and is
 *      usually a different page load. Firing at the call site would count
 *      accounts that never confirm.
 *   2. **Google OAuth** (`signInWithOAuth` → `/auth/callback`). The account is
 *      auto-created on first sign-in, and the callback is a SERVER route
 *      handler — it has no `sessionStorage`, no session key, and cannot beacon.
 *
 * So this watches the one thing both paths converge on: an authenticated user
 * whose account is NEW. `created_at` comes from `/auth/me` — server truth,
 * stamped by the `handle_new_user()` trigger — so a client cannot assert its
 * way into a signup the way a `?signup=1` redirect marker could.
 *
 * ## ⚠ Count it as `count(DISTINCT user_id)`, never `count(*)`
 *
 * The latch below is per TAB (sessionStorage, §2 — there is no durable key
 * here, deliberately). A visitor who signs up and opens a second tab inside the
 * freshness window fires this twice. That is the right trade: `user_id` is
 * settled server-side on every event, so duplicates collapse under DISTINCT,
 * whereas a durable client key would buy exactness by breaking the privacy
 * posture the whole feature rests on.
 *
 * ## ⚠ Known blind spot
 *
 * An account confirmed MORE than `FRESH_ACCOUNT_MS` after it was created is
 * never counted. The window has to be finite or every returning login would
 * re-fire; 24 h covers same-day email confirmation, which is nearly all of it.
 * Making this exact needs a server-side `signup_tracked_at` on `users`, i.e. a
 * migration — see the note in the plan before reaching for a wider window.
 */

import { useEffect, useRef } from "react";
import { track } from "@/lib/analytics/client";
import { readGateAttribution } from "@/components/analytics/signup-attribution";
import { useAuthStore } from "@/stores/auth-store";

/** How new an account may be and still count as "just created". */
const FRESH_ACCOUNT_MS = 24 * 60 * 60 * 1000;

/**
 * `created_at` is a SERVER stamp compared against a CLIENT clock, and a browser
 * running a few minutes fast would compute a negative age and silently drop
 * every signup it saw. Tolerate the skew rather than lose the event.
 */
const CLOCK_SKEW_TOLERANCE_MS = 5 * 60 * 1000;

/** Per-tab latch, so a route change inside the window cannot re-fire. */
const FIRED_STORAGE_KEY = "rayhan_analytics_signup_done_v1";

export function SignupCompletedTracker(): null {
  const user = useAuthStore((state) => state.user);
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  /** Belt to the storage key's braces: effects re-run under StrictMode. */
  const settledRef = useRef(false);

  useEffect(() => {
    if (settledRef.current) return;
    // Not signed in yet, or `/auth/me` has not landed. Both are normal on a
    // public page; the effect re-runs when the store fills in.
    if (!isAuthenticated || !user?.created_at) return;

    const createdAt = Date.parse(user.created_at);
    if (!Number.isFinite(createdAt)) return;

    const age = Date.now() - createdAt;
    // An established account signing in again is NOT a signup.
    if (age > FRESH_ACCOUNT_MS || age < -CLOCK_SKEW_TOLERANCE_MS) {
      settledRef.current = true;
      return;
    }

    settledRef.current = true;

    try {
      if (window.sessionStorage.getItem(FIRED_STORAGE_KEY)) return;
      window.sessionStorage.setItem(FIRED_STORAGE_KEY, "1");
    } catch {
      // Unusable storage → fall through and emit. A duplicate is recoverable
      // (count DISTINCT user_id); a missing signup is not.
    }

    try {
      const gate = readGateAttribution();
      track("signup_completed", {
        // How long the account took to become a signed-in session: ~0 for
        // Google OAuth, the inbox round trip for email + password. It is the
        // cheapest way to see the confirmation step's real cost.
        ms_since_created: Math.max(0, Math.round(age)),
        // Absent when this tab has no gate click to attribute — an omitted key
        // reads as «no gate», where a null would read as «a gate, unknown».
        ...(gate
          ? { gate_kind: gate.gate_kind, gate_path: gate.gate_path }
          : {}),
      });
    } catch {
      // T9 — analytics must never surface anything to the visitor.
    }
  }, [isAuthenticated, user]);

  return null;
}
