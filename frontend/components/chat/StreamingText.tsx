"use client";

import { memo, useMemo } from "react";
import { cn } from "@/lib/utils";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { splitStreamingContent } from "@/lib/markdown/streaming";

interface StreamingTextProps {
  content: string;
  className?: string;
}

export const StreamingText = memo(function StreamingText({
  content,
  className,
}: StreamingTextProps) {
  // Split at the last stable block boundary. The prefix's string value only
  // changes when a new block completes, so its memoized renderer skips the
  // markdown re-parse; each reveal frame re-parses just the short tail.
  const { prefix, tail } = useMemo(
    () => splitStreamingContent(content),
    [content],
  );

  return (
    <div
      dir="rtl"
      lang="ar"
      className={cn("streaming-text", className)}
    >
      {prefix.length > 0 && <MarkdownRenderer content={prefix} streaming />}
      <MarkdownRenderer content={tail} streaming />
      <span
        className="inline-block w-[2px] h-[1em] bg-foreground align-text-bottom ms-0.5 animate-blink"
        aria-hidden="true"
      />
    </div>
  );
});
