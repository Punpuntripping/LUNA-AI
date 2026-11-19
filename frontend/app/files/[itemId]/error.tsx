"use client";

import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

interface FileErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

// Next.js App Router requires default export for error files
// eslint-disable-next-line import/no-default-export
export default function FileError({ reset }: FileErrorProps) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-4 text-center" dir="rtl">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="h-6 w-6 text-destructive" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-foreground">تعذّر عرض الملف</h2>
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">
          حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى.
        </p>
      </div>
      <Button variant="outline" onClick={reset}>
        إعادة المحاولة
      </Button>
    </div>
  );
}
