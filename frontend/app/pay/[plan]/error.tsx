"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

interface PayPlanErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Boundary for the checkout route.
 *
 * The copy deliberately says nothing about whether money moved: this boundary
 * catches render-time failures, which happen before or alongside the form and
 * never after a charge. Reassuring a user that "no payment was taken" would be
 * a claim we cannot make from here — pointing them at the receipts list, which
 * shows the truth, is the honest move.
 */
// Next.js App Router requires a default export for error files.
// eslint-disable-next-line import/no-default-export
export default function PayPlanError({ error, reset }: PayPlanErrorProps) {
  return (
    <div className="flex flex-col items-center gap-4 py-16 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="h-6 w-6 text-destructive" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-foreground">
          تعذّر عرض صفحة الدفع
        </h2>
        <p className="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">
          {error.message ||
            "حدث خطأ غير متوقع. يمكنك المحاولة مرة أخرى أو العودة إلى الباقات."}
        </p>
      </div>
      <div className="flex items-center gap-3">
        <Button variant="outline" onClick={reset}>
          إعادة المحاولة
        </Button>
        <Link
          href="/pricing"
          className="text-sm font-medium text-primary underline-offset-4 hover:underline"
        >
          العودة إلى الباقات
        </Link>
      </div>
    </div>
  );
}
