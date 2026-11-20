"use client";

import { createContext, useContext, type ReactNode } from "react";

/**
 * A seam for rendering the REAL sidebar outside its real routes. The app never
 * provides it — `useAppNavOverride()` is `null` everywhere in production, and
 * the sidebar then reads `usePathname()`, pushes with the router and follows
 * `useFilesEnabled()` (build flag + `GET /files/status`).
 *
 * Only the dev preview (`app/dev-preview/my-files`) provides it, to show a row
 * as active, keep the «الملفات» / «المحادثات» rows inside the preview, and draw
 * the flag-off sidebar next to the flag-on one.
 */
export interface AppNavOverride {
  /** Treated as the current path for the rows' active state. */
  pathname?: string;
  /** Replaces `router.push` for the rows («محادثة جديدة» included). */
  navigate?: (href: string) => void;
  /** Replaces `useFilesEnabled()` for the sidebar rows only. */
  filesNav?: boolean;
}

const AppNavOverrideContext = createContext<AppNavOverride | null>(null);

export function AppNavOverrideProvider({
  value,
  children,
}: {
  value: AppNavOverride;
  children: ReactNode;
}) {
  return (
    <AppNavOverrideContext.Provider value={value}>{children}</AppNavOverrideContext.Provider>
  );
}

export function useAppNavOverride(): AppNavOverride | null {
  return useContext(AppNavOverrideContext);
}
