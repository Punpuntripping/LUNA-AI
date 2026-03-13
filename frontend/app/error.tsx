"use client";

import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

interface RootErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

// Next.js App Router requires default export for error files
// eslint-disable-next-line import/no-default-export
export default function RootError({ error, reset }: RootErrorProps) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="h-7 w-7 text-destructive" />
      </div>
      <div>
        <h2 className="text-xl font-semibold text-foreground">
          حدث خطأ غير متوقع
        </h2>
        <p className="mt-2 text-sm text-muted-foreground max-w-md">
          نعتذر عن هذا الخطأ. يمكنك محاولة إعادة تحميل الصفحة أو العودة للصفحة الرئيسية.
        </p>
      </div>
      <div className="flex gap-3">
        <Button variant="outline" onClick={reset}>
          إعادة المحاولة
        </Button>
        <Button variant="default" onClick={() => (window.location.href = "/")}>
          العودة للرئيسية
        </Button>
      </div>
    </div>
  );
}
