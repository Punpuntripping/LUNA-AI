"use client";

import { useEffect } from "react";

/**
 * Registers `/sw.js` (`.claude/plans/pwa_step1.md` §1C).
 *
 * - Production only. In dev a service worker outlives HMR reloads and serves
 *   stale navigations, which is pure pain for no benefit.
 * - Deferred to window `load` so registration (and the SW's own install
 *   fetch of /offline) never competes with the page's first paint.
 * - Fails silently: no SW simply means no offline screen and no push — the
 *   app itself is unaffected, so there is nothing worth surfacing to the user.
 *
 * Renders nothing.
 */
export function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (!("serviceWorker" in navigator)) return;

    const register = () => {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .catch(() => {
          // Deliberately silent — see the doc comment.
        });
    };

    if (document.readyState === "complete") {
      // Mounted after `load` already fired (e.g. slow hydration).
      register();
      return;
    }
    window.addEventListener("load", register, { once: true });
    return () => window.removeEventListener("load", register);
  }, []);

  return null;
}
