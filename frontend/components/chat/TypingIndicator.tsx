"use client";

import { cn } from "@/lib/utils";

interface TypingIndicatorProps {
  className?: string;
}

export function TypingIndicator({ className }: TypingIndicatorProps) {
  return (
    <div
      dir="rtl"
      lang="ar"
      className={cn(
        "flex items-center gap-2 px-4 py-3 rounded-xl bg-card border max-w-fit",
        className
      )}
    >
      <span className="text-xs text-muted-foreground">يفكر...</span>
      <div className="flex items-center gap-1" aria-hidden="true">
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce-dot [animation-delay:0ms]" />
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce-dot [animation-delay:150ms]" />
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce-dot [animation-delay:300ms]" />
      </div>
    </div>
  );
}
