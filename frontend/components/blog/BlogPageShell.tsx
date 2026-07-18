import Link from "next/link";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { HeaderAuthActions } from "@/components/site/HeaderAuthActions";
import { BlogConversionCta } from "@/components/blog/BlogConversionCta";
import { SiteFooter } from "@/components/site/SiteFooter";

interface BlogPageShellProps {
  children: React.ReactNode;
  /** Show the «جرّب ريحان مجاناً» conversion block above the footer. Default true. */
  showCta?: boolean;
}

/**
 * Shared public-page chrome for every مدونة surface — the question page, the
 * editorial article page, and the gated directory. Wraps caller-provided
 * ``children`` (which supply their OWN ``<main>`` + max-width) with the sticky
 * brand header, the conversion CTA, and the full site footer.
 *
 * Auth-aware chrome: HeaderAuthActions swaps the login/signup buttons for
 * «العودة إلى ريحان» when a session exists, and BlogConversionCta hides the
 * signup pitch from signed-in readers — the same shell serves the anonymous
 * /blog surfaces and the authed /blogs management view.
 *
 * RTL throughout. The CTA sits inside its own centered ``max-w-3xl`` wrapper so
 * it reads correctly regardless of how wide the children content column is
 * (the directory grid is wider than the article column).
 */
export function BlogPageShell({ children, showCta = true }: BlogPageShellProps) {
  return (
    <div dir="rtl" className="flex min-h-screen flex-col bg-background">
      {/* Header bar */}
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-3 px-4 py-3">
          {/* Logo block — links to the public front door (authed users get
              bounced onward to /chat, their home) */}
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

      {/* Page content — caller supplies its own <main> + max-width */}
      {children}

      {/* Conversion CTA — anonymous readers only, centered regardless of
          children width */}
      {showCta && <BlogConversionCta />}

      <SiteFooter />
    </div>
  );
}
