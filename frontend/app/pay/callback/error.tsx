"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

interface PayCallbackErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Boundary for the payment-return route.
 *
 * The copy must never assert that the payment failed — this boundary fires on a
 * render error, which is entirely independent of whether the card was charged.
 * It says what is actually true: the grant does not depend on this page, and
 * the receipts list is where the real status lives.
 */
// Next.js App Router requires a default export for error files.
// eslint-disable-next-line import/no-default-export
export default function PayCallbackError({
  error,
  reset,
}: PayCallbackErrorProps) {
  return (
    <div className="flex flex-col items-center gap-4 py-16 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="h-6 w-6 text-destructive" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-foreground">
          تعذّر عرض نتيجة الدفع
        </h2>
        <p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-muted-foreground">
          {error.message
            ? `${error.message} `
            : ""}
          إن تم خصم المبلغ فسيُفعَّل اشتراكك تلقائياً خلال دقائق. تجد حالة
          العملية في «سجل المدفوعات» داخل الإعدادات.
        </p>
      </div>
      <div className="flex items-center gap-3">
        <Button variant="outline" onClick={reset}>
          إعادة المحاولة
        </Button>
        <Link
          href="/chat"
          className="text-sm font-medium text-primary underline-offset-4 hover:underline"
        >
          العودة إلى ريحان
        </Link>
      </div>
    </div>
  );
}
