"use client";

import { memo } from "react";
import { cn } from "@/lib/utils";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";

interface StreamingTextProps {
  content: string;
  className?: string;
}

export const StreamingText = memo(function StreamingText({
  content,
  className,
}: StreamingTextProps) {
  return (
    <div
      dir="rtl"
      lang="ar"
      className={cn("streaming-text", className)}
    >
      <MarkdownRenderer content={content} />
      <span
        className="inline-block w-[2px] h-[1em] bg-foreground align-text-bottom ms-0.5 animate-blink"
        aria-hidden="true"
      />
    </div>
  );
});
