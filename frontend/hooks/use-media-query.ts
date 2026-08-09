"use client";

import { useSyncExternalStore } from "react";

/**
 * SSR-safe media query hook.
 *
 * `useSyncExternalStore` is used rather than useState+useEffect so the value is
 * read during the same commit that hydrates — a useEffect-based hook renders
 * one extra frame at the server-snapshot value, flashing the wrong layout.
 *
 * The server snapshot is `false`, so `useIsMobile()` is `true` during SSR:
 * markup ships mobile-first and widens on the client, matching the `max-md:`
 * CSS the sidebar already relies on. Consumers that render a desktop-only
 * branch should still carry a `md:hidden` / `hidden md:flex` guard so the
 * pre-hydration frame is correct without JS.
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Tailwind's `md` breakpoint (768px) — below it, panes stack instead of split. */
export function useIsMobile(): boolean {
  return !useMediaQuery("(min-width: 768px)");
}
