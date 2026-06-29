import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { cn } from "@/lib/utils";
import { PRIMARY_CTA_HREF } from "./content";

/**
 * Slim top bar for the public landing page. RTL: logo sits at the start (right),
 * actions at the end (left). Server component — the only interactive child is
 * the client ThemeToggle.
 */
export function LandingHeader() {
  return (
    <header className="sticky top-0 z-20 border-b border-border/60 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4">
        {/* Brand */}
        <Link href="/" className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground">
            ريحان
          </span>
          <span className="text-base font-bold tracking-tight text-foreground">
            ريحان
          </span>
        </Link>

        {/* Actions */}
        <nav className="flex items-center gap-2">
          <a
            href="#pricing"
            className={cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "hidden text-sm sm:inline-flex",
            )}
          >
            الأسعار
          </a>
          <ThemeToggle />
          <Link
            href="/login"
            className={cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              "text-sm",
            )}
          >
            تسجيل الدخول
          </Link>
          <Link
            href={PRIMARY_CTA_HREF}
            className={cn(buttonVariants({ size: "sm" }), "text-sm font-semibold")}
          >
            ابدأ الآن
          </Link>
        </nav>
      </div>
    </header>
  );
}
