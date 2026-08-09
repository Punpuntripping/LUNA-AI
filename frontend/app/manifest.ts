import type { MetadataRoute } from "next";

/**
 * Web app manifest — the only mechanism that actually removes browser chrome.
 *
 * Once a user installs ريحان to their home screen, `display: "standalone"`
 * drops the address bar and toolbars permanently. This is a persistent user
 * choice, NOT something the app can trigger per-artifact: the Fullscreen API
 * requires transient user activation, so an artifact arriving over SSE can
 * never take the screen on its own.
 *
 * `start_url` points at /chat rather than the marketing landing — an installed
 * app should open the app. AuthGuard redirects to /login when unauthenticated.
 *
 * Orientation is deliberately left unlocked: legal documents in the workspace
 * read well in landscape and locking to portrait would fight the user.
 *
 * Next.js serves this at /manifest.webmanifest and injects the <link> tag.
 */
// eslint-disable-next-line import/no-default-export
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ريحان - المساعد القانوني الذكي",
    short_name: "ريحان",
    description: "مساعد ذكاء اصطناعي للمحامين السعوديين",
    lang: "ar",
    dir: "rtl",
    start_url: "/chat",
    scope: "/",
    display: "standalone",
    // Light canvas (--canvas, Herbarium Paper). The splash screen paints before
    // any CSS runs, so it can't follow the user's theme — light is the default.
    background_color: "#F7F2EC",
    theme_color: "#F7F2EC",
    categories: ["business", "productivity"],
    icons: [
      {
        src: "/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        // Android crops icons to a circle/squircle. This variant keeps the
        // lavender inside the 80% safe zone on an opaque canvas so the flower
        // tips survive the mask.
        src: "/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
