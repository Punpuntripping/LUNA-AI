"use client";

import { cn } from "@/lib/utils";

interface StreamingTextProps {
  content: string;
  className?: string;
}

export function StreamingText({ content, className }: StreamingTextProps) {
  return (
    <div
      dir="rtl"
      lang="ar"
      className={cn("whitespace-pre-wrap text-sm leading-relaxed", className)}
    >
      {content}
      <span
        className="inline-block w-[2px] h-[1em] bg-foreground align-text-bottom ms-0.5 animate-blink"
        aria-hidden="true"
      />
    </div>
  );
}
