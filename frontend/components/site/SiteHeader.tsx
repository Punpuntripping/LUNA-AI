import Link from "next/link";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { HeaderAuthActions } from "@/components/site/HeaderAuthActions";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteMobileNav } from "@/components/site/SiteMobileNav";

/**
 * The one global header for every non-sidebar surface (marketing, legal, blog,
 * and — via `LibraryPageShell` composing this — the SEO library). Replaces the
 * three divergent public headers (`LandingHeader`, the inline `BlogPageShell`
 * header, `LegalPageShell`'s logo box).
 *
 * Server component. The interactive pieces are the client `SiteNav` (desktop
 * dropdowns), `SiteMobileNav` (drawer), `ThemeToggle`, and `HeaderAuthActions`
 * (auth-aware buttons). RTL: brand at the start (right), actions at the end.
 */
export function SiteHeader() {
  // NOTE: `backdrop-blur` below makes <header> the containing block for every
  // `position: fixed` DESCENDANT, and its `z-30` traps them in this stacking
  // context. Anything fixed and full-screen must therefore portal to <body>
  // rather than render inside the bar — see `SiteMobileNav`.
  return (
    <header className="sticky top-0 z-30 border-b border-border/60 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4">
        {/* Brand → public front door (AuthGuard bounces authed users onward). */}
        <Link href="/" className="flex shrink-0 items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground">
            ريحان
          </span>
          <span className="text-base font-bold tracking-tight text-foreground">
            ريحان
          </span>
        </Link>

        {/* The nav travels WITH the action cluster rather than hugging the brand,
            so the whole of it lands at the end of the bar next to «تسجيل الدخول».
            `justify-between` then leaves one clean gap between brand and nav. */}
        <div className="flex items-center gap-1.5">
          {/* Desktop action cluster; the drawer carries its own copy < lg. */}
          <div className="hidden items-center gap-2 lg:flex">
            <SiteNav />
            <ThemeToggle />
            <HeaderAuthActions />
          </div>

          {/* Mobile bar: the primary CTA only. Everything else in this cluster
              used to sit inside the lg-only wrapper above, which left phones —
              the majority of search traffic — with a header of nothing but a
              logo and a hamburger, and no visible way into the product. Nav
              slots and «تسجيل الدخول» stay one tap away in the drawer. */}
          <div className="lg:hidden">
            <HeaderAuthActions compact />
          </div>

          <SiteMobileNav />
        </div>
      </div>
    </header>
  );
}
