"use client";

import Link from "next/link";
import { ArrowLeft, Sparkles } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * «جرّب ريحان مجاناً» conversion block shown above the مدونة footer — for
 * ANONYMOUS readers only. A signed-in user is already converted: showing them
 * signup/login CTAs is the bug this component fixes, so for them (and during
 * the session probe) it renders nothing — the header's «العودة إلى ريحان»
 * covers navigation back to the app.
 */
export function BlogConversionCta() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);

  if (isLoading || isAuthenticated) return null;

  return (
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
  );
}
