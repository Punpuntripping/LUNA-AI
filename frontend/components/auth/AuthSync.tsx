"use client";

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { supabase } from "@/lib/supabase";
import { useChatStore } from "@/stores/chat-store";
import { usePreferencesStore } from "@/stores/preferences-store";
import { isPublicPath } from "@/components/auth/AuthGuard";
import { loginHref } from "@/lib/safe-next";

/**
 * Cross-tab identity guard — fixes the account-mixing privacy leak.
 *
 * Supabase (`@supabase/ssr`) persists exactly ONE session per browser-origin:
 * the `sb-<ref>-auth-token` cookie, shared by every tab. Logging into a second
 * account in any tab overwrites that cookie for the whole browser. This tab's
 * next *silent* token refresh — focus `revalidateSession`, the proactive timer,
 * or a 401 retry — then quietly adopts the OTHER account's token, while the tab
 * still displays the first account's `user`, open conversation, and cached
 * lists. That mixed state is the bug we saw: account A's conversation stays on
 * screen under account B's live session (e.g. NAWARA's thread under XL0RCH's
 * sidebar). The backend stays correctly user-scoped, so it's a client-side
 * *display* leak — but a real one.
 *
 * Every session swap (including cross-tab ones, surfaced here as a
 * `TOKEN_REFRESHED` carrying the new account) funnels through
 * `onAuthStateChange`. We watch it, and the instant the authenticated user id
 * changes out from under us — or the session signs out — we purge all in-memory
 * client state and hard-reload, so a tab can only ever reflect the single
 * account currently active in the browser.
 */
export function AuthSync() {
  const queryClient = useQueryClient();
  // The Supabase auth user id this tab is currently bound to. Seeded by the
  // first session event we observe, then compared against every later event.
  const boundUserId = useRef<string | null>(null);

  useEffect(() => {
    // Wipe everything keyed to the previous account. A full reload follows and
    // would clear this anyway, but purging first closes the window between the
    // swap being detected and the navigation actually unloading the page.
    function purge() {
      queryClient.clear(); // all cached server data (convos, messages, workspace…)
      useChatStore.getState().reset(); // open conversation / streaming / pending
      usePreferencesStore.getState().reset(); // per-user prefs cache
    }

    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      const nextId = session?.user?.id ?? null;

      // First observation (mount / initial hydrate, or the user's own login on
      // this tab): just record who we are — no reset.
      if (boundUserId.current === null) {
        boundUserId.current = nextId;
        return;
      }

      // Same account, ordinary token refresh — nothing to do.
      if (nextId && nextId === boundUserId.current) return;

      if (event === "SIGNED_OUT" || nextId === null) {
        // Session ended here or in another tab — purge, then part ways with
        // the page in the least destructive way that still guarantees no
        // ghost data survives:
        //
        //   - PUBLIC page (library, /regulations, /blog…): these render for
        //     anonymous visitors by design, so bouncing a reader to /login
        //     just loses their place. Reload in place instead — the tab
        //     re-hydrates as anon on the same URL (auth-aware chrome flips to
        //     the signed-out variant; the content stays).
        //   - PRIVATE page: /login is the only correct destination, but carry
        //     the page as `?next=` so re-login returns the user here.
        purge();
        boundUserId.current = null;
        if (isPublicPath(window.location.pathname)) {
          window.location.reload();
        } else {
          window.location.replace(
            loginHref(
              `${window.location.pathname}${window.location.search}`,
            ),
          );
        }
        return;
      }

      // A DIFFERENT non-null user now owns the browser session: another account
      // took over. Purge and hard-reload so this tab re-hydrates cleanly as that
      // account, with no ghost data from the previous one left on screen.
      purge();
      boundUserId.current = nextId;
      window.location.reload();
    });

    return () => data.subscription.unsubscribe();
  }, [queryClient]);

  return null;
}
