"use client";

import Link from "next/link";
import { useAuthStore } from "@/stores/auth-store";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Auth-aware action cluster for the public-page headers — this is what makes
 * the site render TWO header variants from one shell:
 *
 *   - signed-out → تسجيل الدخول (ghost) + ابدأ الآن (primary), both to /login
 *   - signed-in  → اسأل ريحان (primary, same shape as ابدأ الآن), straight to /chat
 *
 * Public pages render inside AuthGuard, which always fires ``loadUser()`` —
 * so the store is populated here even for anonymous visitors. While the
 * session probe is in flight we render an invisible same-footprint
 * placeholder instead of defaulting to the signed-out variant, so a
 * signed-in user never sees a «تسجيل الدخول» flash.
 *
 * ``compact`` is the mobile header-bar variant: the signed-out state drops the
 * ghost «تسجيل الدخول» and keeps only the primary CTA, because two buttons plus
 * the brand and the hamburger do not fit a 390px bar. The full pair still
 * renders in the drawer footer, which is where the mobile login path lives.
 */
export function HeaderAuthActions({ compact = false }: { compact?: boolean }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);

  if (isLoading) {
    // Footprint must match the resolved variant or the bar reflows on probe.
    return (
      <span
        aria-hidden="true"
        className={cn("inline-block h-8", compact ? "w-24" : "w-36")}
      />
    );
  }

  if (isAuthenticated) {
    return (
      <Link
        href="/chat"
        className={cn(buttonVariants({ size: "sm" }), "text-sm font-semibold")}
      >
        اسأل ريحان
      </Link>
    );
  }

  return (
    <>
      {!compact && (
        <Link
          href="/login"
          className={cn(
            buttonVariants({ variant: "ghost", size: "sm" }),
            "text-sm",
          )}
        >
          تسجيل الدخول
        </Link>
      )}
      <Link
        href="/login"
        className={cn(buttonVariants({ size: "sm" }), "text-sm font-semibold")}
      >
        ابدأ الآن
      </Link>
    </>
  );
}
