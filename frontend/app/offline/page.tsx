import type { Metadata } from "next";
import { RetryButton } from "./RetryButton";

/**
 * Offline screen (`.claude/plans/pwa_step1.md` §1C).
 *
 * `public/sw.js` precaches this page at install and serves it whenever a
 * navigation fails for lack of a network. That shapes everything here:
 *   - Fully static, no data fetching, no auth — it is rendered from the SW
 *     cache with no connection at all.
 *   - Only the HTML is precached. The page's CSS/JS are content-hashed and
 *     usually still in the browser cache, but not guaranteed — so the layout
 *     carries inline fallback styles (CSS vars with the light-theme values as
 *     fallbacks) and the brand is TEXT, not an image that may not be cached.
 *   - Theme-aware through the design tokens: next-themes' inline script sets
 *     the `dark` class on <html> from localStorage, which works offline.
 */
export const metadata: Metadata = {
  title: "لا يوجد اتصال — ريحان",
  robots: { index: false, follow: false },
};

export const dynamic = "force-static";

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function OfflinePage() {
  return (
    <main
      className="flex min-h-dvh flex-col items-center justify-center gap-6 bg-canvas px-6 text-center text-text-primary"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100dvh",
        gap: "1.5rem",
        padding: "0 1.5rem",
        textAlign: "center",
        backgroundColor: "var(--canvas, #F7F2EC)",
        color: "var(--text-primary, #18141A)",
      }}
    >
      <span
        className="text-4xl font-bold text-primary"
        style={{
          fontSize: "2.25rem",
          fontWeight: 700,
          color: "var(--primary, #4A6B5F)",
        }}
      >
        ريحان
      </span>

      <div className="flex max-w-sm flex-col gap-2">
        <h1
          className="text-xl font-semibold"
          style={{ fontSize: "1.25rem", fontWeight: 600, margin: 0 }}
        >
          لا يوجد اتصال بالإنترنت
        </h1>
        <p
          className="text-sm leading-relaxed text-text-muted"
          style={{
            fontSize: "0.875rem",
            margin: 0,
            color: "var(--text-muted, #6A6581)",
          }}
        >
          تحقّق من اتصالك ثم أعد المحاولة. محادثاتك محفوظة ولن تضيع.
        </p>
      </div>

      <RetryButton />
    </main>
  );
}
