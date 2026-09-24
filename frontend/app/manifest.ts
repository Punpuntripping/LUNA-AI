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
 * `id` pins the app's identity independently of `start_url`. Without it the
 * browser derives the identity FROM `start_url`, so a later change there
 * would register as a second, separate app on every device that installed
 * the first one. Never change `id`.
 *
 * `shortcuts` feed Android's long-press menu on the home-screen icon; iOS
 * ignores them harmlessly. They reuse the regular icons.
 *
 * `screenshots` (the rich Android install sheet) are NOT listed yet — see
 * `public/manifest-screenshots/README.md`. Referencing a missing file makes
 * Chrome discard the whole screenshots array and log a manifest warning.
 *
 * Next.js serves this at /manifest.webmanifest and injects the <link> tag.
 */
// eslint-disable-next-line import/no-default-export
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/chat",
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
        // Android crops icons to a circle/squircle. This variant sits the
        // «ريحان» lockup inside the 80% safe zone on the cream ground so
        // neither the wordmark nor the leaf gets clipped by the mask.
        // The cap is on the DIAGONAL, not the width: a lockup of aspect r
        // needs `width × √(1 + 1/r²) ≤ 0.80`, which at r = 1.473 allows 0.662
        // — hence 63% here against 84% for the uncropped icons.
        src: "/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    shortcuts: [
      {
        name: "محادثة جديدة",
        short_name: "جديدة",
        url: "/chat",
        icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }],
      },
      {
        name: "محادثاتي",
        url: "/chats",
        icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }],
      },
    ],
  };
}
