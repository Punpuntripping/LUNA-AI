"use client";

import { useState, useCallback, memo } from "react";
import { Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface CodeBlockProps {
  language?: string;
  children: string;
  className?: string;
}

export const CodeBlock = memo(function CodeBlock({
  language,
  children,
  className,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may not be available in some contexts
    }
  }, [children]);

  return (
    <div
      dir="ltr"
      className={cn(
        "group/code relative rounded-lg bg-code text-sm my-3 overflow-hidden",
        className
      )}
    >
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-code-head border-b border-code-border">
        <span className="text-xs text-code-muted font-mono select-none">
          {language || "code"}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 text-code-muted hover:text-code-fg hover:bg-code-border/60"
          onClick={handleCopy}
          aria-label="نسخ الكود"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-success-fg" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </Button>
      </div>

      {/* Code content */}
      <div className="overflow-x-auto p-4">
        <pre className="!m-0 !p-0 !bg-transparent">
          <code
            className={cn(
              "!bg-transparent text-code-fg text-[13px] leading-relaxed",
              language && `hljs language-${language}`
            )}
          >
            {children}
          </code>
        </pre>
      </div>
    </div>
  );
});
