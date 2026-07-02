import Link from "next/link";
import { ArrowLeft, Sparkles } from "lucide-react";
import { ThemeToggle } from "@/components/ui/theme-toggle";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface BlogPageShellProps {
  children: React.ReactNode;
  /** Show the «جرّب ريحان مجاناً» conversion block above the footer. Default true. */
  showCta?: boolean;
}

/**
 * Shared public-page chrome for every مدونة surface — the question page, the
 * editorial article page, and the gated directory. Wraps caller-provided
 * ``children`` (which supply their OWN ``<main>`` + max-width) with the sticky
 * brand header, the conversion CTA, and the slim footer.
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
          {/* Logo block — mirrors login/page.tsx rounded badge */}
          <Link href="/login" className="flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground">
              ريحان
            </span>
            <span className="hidden text-sm font-semibold text-foreground sm:inline">
              المساعد القانوني الذكي
            </span>
          </Link>

          <div className="flex items-center gap-1.5">
            <ThemeToggle />
            <Link
              href="/login"
              className={cn(
                buttonVariants({ variant: "ghost", size: "sm" }),
                "hidden sm:inline-flex",
              )}
            >
              تسجيل الدخول
            </Link>
            <Link
              href="/login"
              className={cn(buttonVariants({ variant: "default", size: "sm" }))}
            >
              إنشاء حساب
            </Link>
          </div>
        </div>
      </header>

      {/* Page content — caller supplies its own <main> + max-width */}
      {children}

      {/* Conversion CTA — centered regardless of children width */}
      {showCta && (
        <div className="mx-auto w-full max-w-3xl px-4 pb-8">
          <section className="overflow-hidden rounded-xl border bg-primary/5 p-6 text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
              <Sparkles className="h-6 w-6" />
            </div>
            <h2 className="text-lg font-bold text-foreground">
              جرّب ريحان مجاناً
            </h2>
            <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-muted-foreground">
              المساعد القانوني الذكي للمحامين السعوديين — أنشئ تحليلاتك القانونية
              ومذكراتك مدعومة بالأنظمة والسوابق.
            </p>
            <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
              <Link
                href="/login"
                className={cn(buttonVariants({ variant: "default", size: "lg" }))}
              >
                <Sparkles className="h-4 w-4" />
                ابدأ الآن
              </Link>
              <Link
                href="/login"
                className={cn(buttonVariants({ variant: "outline", size: "lg" }))}
              >
                تسجيل الدخول
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </div>
          </section>
        </div>
      )}

      {/* Slim footer */}
      <footer className="border-t py-4 text-center text-xs text-muted-foreground">
        مُنشأ عبر{" "}
        <Link href="/login" className="font-medium text-primary hover:underline">
          ريحان
        </Link>
      </footer>
    </div>
  );
}
