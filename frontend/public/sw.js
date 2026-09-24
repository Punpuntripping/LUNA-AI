/* eslint-disable */
/**
 * ريحان service worker — `.claude/plans/pwa_step1.md` §1C + §1D.
 *
 * Hand-written and deliberately minimal. NO Workbox / next-pwa: framework
 * caching of Next pages fights ISR, auth and the Cloudflare purge workflow.
 *
 * What it does:
 *   1. Offline screen. The ONLY requests it touches are same-origin GET
 *      navigations (`request.mode === "navigate"`): network first, and only if
 *      the network THROWS (no connection) it answers with the cached /offline
 *      page. An HTTP error (404/500) from the network is passed through as-is.
 *   2. Web push «إجابتك جاهزة» (`push` / `notificationclick`).
 *
 * What it must NEVER do: call `respondWith` for anything else. /api/*, the SSE
 * streams, RSC fetches, `_next/static`, and every cross-origin request
 * (Supabase, the backend, Moyasar, Apple Pay, Turnstile) fall straight through
 * to the network, byte-for-byte unchanged. If you are tempted to add a cache
 * route here, read the plan first.
 *
 * Versioning: bump SHELL_CACHE (`rayhan-shell-vN`) whenever /offline changes
 * shape. `activate` deletes every other `rayhan-shell-*` cache.
 *
 * Delivery: /sw.js is served `Cache-Control: no-cache, no-store,
 * must-revalidate` by next.config.mjs, and MUST also bypass the Cloudflare
 * cache (cache rule on the exact path /sw.js). If the edge caches this file,
 * a broken worker stays pinned on users' phones for the edge TTL.
 *
 * ─── KILL SWITCH ──────────────────────────────────────────────────────────
 * If a broken worker ships, do NOT just delete this file (a 404 on update
 * leaves the old worker running). Replace the ENTIRE contents of
 * public/sw.js with the self-unregistering version below, deploy, and purge
 * /sw.js at Cloudflare. Browsers re-check /sw.js on every navigation (the
 * no-cache header guarantees it), install this one, and it removes itself:
 *
 *   self.addEventListener("install", () => self.skipWaiting());
 *   self.addEventListener("activate", (event) => {
 *     event.waitUntil((async () => {
 *       const keys = await caches.keys();
 *       await Promise.all(
 *         keys.filter((k) => k.startsWith("rayhan-shell-")).map((k) => caches.delete(k)),
 *       );
 *       await self.registration.unregister();
 *       const wins = await self.clients.matchAll({ type: "window" });
 *       wins.forEach((c) => c.navigate(c.url));
 *     })());
 *   });
 *
 * Keep it deployed for weeks (installed apps may be opened rarely), and keep
 * ServiceWorkerRegistrar mounted meanwhile — or remove it in the same deploy;
 * either way the kill worker is what cleans existing devices.
 * ──────────────────────────────────────────────────────────────────────────
 */

const SHELL_CACHE = "rayhan-shell-v1";
const SHELL_PREFIX = "rayhan-shell-";
const OFFLINE_URL = "/offline";
const ICON_URL = "/icon-192.png";

// ─── Lifecycle ─────────────────────────────────────────────────────────────

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE);
      // `cache: "reload"` skips the HTTP cache so the precached copy is the
      // current deploy's page, not a stale one. Only /offline is precached:
      // its CSS/JS are content-hashed and unknown here, and the page is built
      // to render acceptably from HTML alone (inline fallback styles).
      await cache.add(new Request(OFFLINE_URL, { cache: "reload" }));
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => k.startsWith(SHELL_PREFIX) && k !== SHELL_CACHE)
          .map((k) => caches.delete(k)),
      );
      // Navigation preload: the browser starts the navigation request while
      // the worker boots, so an installed worker adds no latency to page loads.
      if (self.registration.navigationPreload) {
        try {
          await self.registration.navigationPreload.enable();
        } catch (_) {
          // Unsupported / already enabled — plain fetch below still works.
        }
      }
      await self.clients.claim();
    })(),
  );
});

// ─── Fetch: navigations only ───────────────────────────────────────────────

self.addEventListener("fetch", (event) => {
  const request = event.request;
  // Everything that is not a same-origin GET navigation is left alone — no
  // respondWith, so the browser handles it exactly as if no worker existed.
  if (request.method !== "GET" || request.mode !== "navigate") return;
  if (new URL(request.url).origin !== self.location.origin) return;

  event.respondWith(
    (async () => {
      try {
        const preloaded = await event.preloadResponse;
        if (preloaded) return preloaded;
        return await fetch(request);
      } catch (_) {
        // Network unreachable. The response is served at the ORIGINAL URL, so
        // «إعادة المحاولة» (location.reload) retries the page the user wanted.
        const cached = await caches.match(OFFLINE_URL, { cacheName: SHELL_CACHE });
        if (cached) return cached;
        return new Response("لا يوجد اتصال بالإنترنت", {
          status: 503,
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        });
      }
    })(),
  );
});

// ─── Push: «إجابتك جاهزة» ──────────────────────────────────────────────────
//
// Payload (JSON): { title, body, url, tag }. Privacy rule (PDPL, وضع السرية):
// the backend never puts question/answer text in a push — see plan §1D.

/** Only same-origin paths are honoured; anything else falls back to /chat. */
function safeAppUrl(raw) {
  try {
    const url = new URL(typeof raw === "string" && raw ? raw : "/chat", self.location.origin);
    if (url.origin !== self.location.origin) return new URL("/chat", self.location.origin);
    return url;
  } catch (_) {
    return new URL("/chat", self.location.origin);
  }
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_) {
    data = {};
  }

  const title = typeof data.title === "string" && data.title ? data.title : "ريحان";
  const body = typeof data.body === "string" && data.body ? data.body : "إجابتك جاهزة";
  const target = safeAppUrl(data.url);
  const tag = typeof data.tag === "string" && data.tag ? data.tag : undefined;

  event.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      // The user is already looking at this conversation — no notification.
      const watching = wins.some((c) => {
        try {
          return c.visibilityState === "visible" && new URL(c.url).pathname === target.pathname;
        } catch (_) {
          return false;
        }
      });
      if (watching) return;

      await self.registration.showNotification(title, {
        body,
        icon: ICON_URL,
        // `badge` (Android status-bar glyph) is intentionally omitted until a
        // monochrome transparent PNG exists: Android renders the badge as an
        // alpha mask, so the opaque app icon would show as a solid square.
        tag,
        // Same tag = the newer notification replaces the older one; renotify
        // still alerts the user for the replacement.
        renotify: Boolean(tag),
        lang: "ar",
        dir: "rtl",
        data: { url: target.pathname + target.search + target.hash },
      });

      if (self.navigator && "setAppBadge" in self.navigator) {
        try {
          await self.navigator.setAppBadge();
        } catch (_) {
          // Badging is cosmetic.
        }
      }
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const target = safeAppUrl(event.notification.data && event.notification.data.url);
  // `?src=push` lets analytics attribute the open (plan §1E `push_opened`).
  target.searchParams.set("src", "push");

  event.waitUntil(
    (async () => {
      if (self.navigator && "clearAppBadge" in self.navigator) {
        try {
          await self.navigator.clearAppBadge();
        } catch (_) {
          // Cosmetic.
        }
      }

      const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const sameOrigin = wins.filter((c) => {
        try {
          return new URL(c.url).origin === self.location.origin;
        } catch (_) {
          return false;
        }
      });
      // Prefer a window already on the conversation, then any app window.
      const client =
        sameOrigin.find((c) => new URL(c.url).pathname === target.pathname) || sameOrigin[0];

      if (client) {
        try {
          const focused = await client.focus();
          const onTarget = new URL(focused.url).pathname === target.pathname;
          // Already showing the conversation: focusing is enough — navigating
          // would reload it for nothing.
          if (!onTarget && "navigate" in focused) {
            // navigate() only works on windows this worker controls; it
            // rejects otherwise, and we fall back to a new window below.
            await focused.navigate(target.href);
          }
          return;
        } catch (_) {
          // Fall through to openWindow.
        }
      }
      await self.clients.openWindow(target.href);
    })(),
  );
});
