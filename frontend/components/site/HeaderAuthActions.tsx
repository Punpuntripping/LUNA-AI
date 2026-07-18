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
 *   - signed-in  → العودة إلى ريحان (primary), straight to /chat
 *
 * Public pages render inside AuthGuard, which always fires ``loadUser()`` —
 * so the store is populated here even for anonymous visitors. While the
 * session probe is in flight we render an invisible same-footprint
 * placeholder instead of defaulting to the signed-out variant, so a
 * signed-in user never sees a «تسجيل الدخول» flash.
 */
export function HeaderAuthActions() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);

  if (isLoading) {
    return <span aria-hidden="true" className="inline-block h-8 w-36" />;
  }

  if (isAuthenticated) {
    return (
      <Link
        href="/chat"
        className={cn(buttonVariants({ size: "sm" }), "text-sm font-semibold")}
      >
        العودة إلى ريحان
      </Link>
    );
  }

  return (
    <>
      <Link
        href="/login"
        className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "text-sm")}
      >
        تسجيل الدخول
      </Link>
      <Link
        href="/login"
        className={cn(buttonVariants({ size: "sm" }), "text-sm font-semibold")}
      >
        ابدأ الآن
      </Link>
    </>
  );
}
