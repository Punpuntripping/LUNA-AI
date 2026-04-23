"use client";

import { useRef, useCallback } from "react";
import { Search, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface ConversationSearchProps {
  value: string;
  onChange: (value: string) => void;
}

export function ConversationSearch({ value, onChange }: ConversationSearchProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleClear = useCallback(() => {
    onChange("");
    inputRef.current?.blur();
  }, [onChange]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        handleClear();
      }
    },
    [handleClear]
  );

  return (
    <div className="relative">
      {/* Search icon — on the right side in RTL */}
      <Search className="absolute top-1/2 -translate-y-1/2 end-2.5 h-4 w-4 text-muted-foreground pointer-events-none" />

      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="بحث في المحادثات..."
        dir="rtl"
        className={cn(
          "w-full h-8 rounded-md text-sm",
          "bg-muted/50 border border-border/50",
          "pe-9 ps-8 py-1",
          "text-foreground placeholder:text-muted-foreground/70",
          "outline-none focus:ring-1 focus:ring-ring focus:border-ring",
          "transition-colors"
        )}
      />

      {/* Clear button — on the left side in RTL, only visible when there's text */}
      {value.length > 0 && (
        <button
          type="button"
          onClick={handleClear}
          className="absolute top-1/2 -translate-y-1/2 start-2 p-0.5 rounded-sm text-muted-foreground hover:text-foreground transition-colors"
          aria-label="مسح البحث"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
