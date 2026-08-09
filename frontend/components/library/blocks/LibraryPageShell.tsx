import Link from "next/link";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { HeaderAuthActions } from "@/components/site/HeaderAuthActions";
import { SiteMobileNav } from "@/components/site/SiteMobileNav";
import { BlogConversionCta } from "@/components/blog/BlogConversionCta";
import { AnonCtaPopup } from "@/components/marketing/AnonCtaPopup";
import { SiteFooter } from "@/components/site/SiteFooter";
import { cn } from "@/lib/utils";
import type { LibraryPageShellProps } from "@/types/library";

/**
 * Public-page chrome for every SEO library surface (reg docs, مواد, judgments,
 * circulars, compliance, forms, calculators, topic hubs). Modeled on
 * `BlogPageShell`: the same sticky auth-aware brand header, the anon-only
 * conversion CTA, and the full site footer — RTL throughout.
 *
 * Unlike BlogPageShell (whose children own their `<main>`), the shell owns the
 * `<main>` here and applies the `maxWidth` variant:
 *   - `doc` → narrow reading column (max-w-3xl) for documents/articles.
 *   - `hub` → wide directory column (max-w-6xl) for the 3×3 hub grids.
 * Server component (renders client leaves: header actions, theme toggle, CTA).
 */
export function LibraryPageShell({
  children,
  maxWidth = "doc",
  showCta = true,
}: LibraryPageShellProps) {
  return (
    <div dir="rtl" className="flex min-h-screen flex-col bg-background">
      {/* Header bar */}
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-3 px-4 py-3">
          <Link href="/" className="flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground">
              ريحان
            </span>
            <span className="hidden text-sm font-semibold text-foreground sm:inline">
              المساعد القانوني الذكي
            </span>
          </Link>

          {/* Desktop keeps the original theme + full auth pair. On mobile the
              bar carried a toggle and TWO buttons on a 390px width and offered
              no navigation at all — these are the pages most search traffic
              lands on. Below `lg` it collapses to the primary CTA plus the
              shared drawer, which already carries «تسجيل الدخول», the theme
              toggle and the whole SITE_NAV. */}
          <div className="flex items-center gap-1.5">
            <div className="hidden items-center gap-1.5 lg:flex">
              <ThemeToggle />
              <HeaderAuthActions />
            </div>

            <div className="lg:hidden">
              <HeaderAuthActions compact />
            </div>

            <SiteMobileNav />
          </div>
        </div>
      </header>

      {/* Page content */}
      <main
        className={cn(
          "mx-auto w-full flex-1 px-4 py-8",
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

      <SiteFooter />
    </div>
  );
}
