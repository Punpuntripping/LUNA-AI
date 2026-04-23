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
        "group/code relative rounded-lg bg-zinc-950 text-sm my-3 overflow-hidden",
        className
      )}
    >
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-zinc-800/60 border-b border-zinc-700/50">
        <span className="text-xs text-zinc-400 font-mono select-none">
          {language || "code"}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700/50"
          onClick={handleCopy}
          aria-label="نسخ الكود"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-green-400" />
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
              "!bg-transparent text-zinc-200 text-[13px] leading-relaxed",
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
