import { SitePageShell } from "@/components/site/SitePageShell";
import { BlogConversionCta } from "@/components/blog/BlogConversionCta";
import { AnonCtaPopup } from "@/components/marketing/AnonCtaPopup";
import { cn } from "@/lib/utils";
import type { LibraryPageShellProps } from "@/types/library";

/**
 * Public-page chrome for every SEO library surface (reg docs, مواد, judgments,
 * circulars, compliance, forms, calculators, topic hubs).
 *
 * COMPOSES `SitePageShell` — it must never fork its own header again. It did
 * until 2026-08-24, and the fork silently dropped `SiteNav`: every one of these
 * routes rendered a brand bar with a theme toggle and the auth buttons, but no
 * «عن ريحان / اكتشف ريحان / المكتبة القانونية / الباقات» on desktop. Since the
 * library dropdown IS the sitewide crawl skeleton, the pages that most need the
 * internal links were the only ones without them. (Mobile was unaffected — the
 * fork already mounted the shared `SiteMobileNav` drawer.)
 *
 * What stays local to this shell, because `SitePageShell` is deliberately
 * chrome-only and its other callers own their own `<main>`:
 *   - the `maxWidth` reading column: `doc` → max-w-3xl, `hub` → max-w-6xl;
 *   - the anon conversion CTA above the footer;
 *   - the reading-depth popup.
 * Server component (renders client leaves: the CTA and the popup).
 */
export function LibraryPageShell({
  children,
  maxWidth = "doc",
  showCta = true,
}: LibraryPageShellProps) {
  return (
    <SitePageShell>
      <main
        className={cn(
          "mx-auto w-full px-4 py-8",
          maxWidth === "hub" ? "max-w-6xl" : "max-w-3xl",
        )}
      >
        {children}
      </main>

      {/* Conversion CTA — anonymous readers only */}
      {showCta && <BlogConversionCta />}

      {/* The ACTIVE half of the same pitch: a popup earned by reading depth.
          Mounted unconditionally — it decides its own eligibility from the
          pathname (documents under the five wings only), so /forms,
          /calculators and every hub cost nothing but a no-op render. Pure
          client, zero server data: it must never vary the shared ISR cache. */}
      <AnonCtaPopup />
    </SitePageShell>
  );
}
