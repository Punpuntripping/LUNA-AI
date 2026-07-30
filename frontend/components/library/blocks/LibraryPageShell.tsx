import Link from "next/link";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { HeaderAuthActions } from "@/components/site/HeaderAuthActions";
import { BlogConversionCta } from "@/components/blog/BlogConversionCta";
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

          <div className="flex items-center gap-1.5">
            <ThemeToggle />
            <HeaderAuthActions />
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

      <SiteFooter />
    </div>
  );
}
