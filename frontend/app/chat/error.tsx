"use client";

import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ChatErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

// Next.js App Router requires default export for error files
// eslint-disable-next-line import/no-default-export
export default function ChatError({ error, reset }: ChatErrorProps) {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="h-6 w-6 text-destructive" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-foreground">
          حدث خطأ غير متوقع
        </h2>
        <p className="mt-1 text-sm text-muted-foreground max-w-sm">
          {error.message || "لم نتمكن من تحميل هذه الصفحة. يرجى المحاولة مرة أخرى."}
        </p>
      </div>
      <Button variant="outline" onClick={reset}>
        إعادة المحاولة
      </Button>
    </div>
  );
}
