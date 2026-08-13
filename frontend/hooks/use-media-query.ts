"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

/** Tailwind's `md` — the one breakpoint the app's layouts switch on. */
const DESKTOP_QUERY = "(min-width: 768px)";

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
  return !useMediaQuery(DESKTOP_QUERY);
}

/**
 * `useIsMobile`'s twin for the FIRST client render.
 *
 * `useSyncExternalStore` is obliged to return the server snapshot while
 * hydrating, so `useIsMobile()` reports `true` for one render on a desktop.
 * That is harmless for CSS-visible branches (`md:` classes cover the frame)
 * but not for a portalled modal layer: a Radix `Sheet` handed `open` for a
 * single commit still runs its scroll-lock and `pointer-events` effects.
 *
 * This one reads `matchMedia` in a lazy initializer, so the very first client
 * render is already correct. Its SSR value is `false` (no viewport to ask) —
 * safe precisely because the surfaces that use it are portalled: Radix renders
 * nothing at all on the server, so there is no markup to disagree about.
 */
export function useIsMobileNow(): boolean {
  const [isMobile, setIsMobile] = useState(
    () =>
      typeof window !== "undefined" && !window.matchMedia(DESKTOP_QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(DESKTOP_QUERY);
    const sync = () => setIsMobile(!mql.matches);
    sync();
    mql.addEventListener("change", sync);
    return () => mql.removeEventListener("change", sync);
  }, []);

  return isMobile;
}

/**
 * Imperative read for event handlers and store callbacks — the places that
 * cannot call a hook (e.g. the SSE dispatcher in `use-chat`). Same breakpoint,
 * same answer; `false` when there is no window.
 */
export function isMobileViewport(): boolean {
  if (typeof window === "undefined") return false;
  return !window.matchMedia(DESKTOP_QUERY).matches;
}
