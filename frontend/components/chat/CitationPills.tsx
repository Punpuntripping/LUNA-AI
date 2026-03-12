"use client";

import { Scale } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Citation } from "@/types";

interface CitationPillsProps {
  citations: Citation[];
  className?: string;
}

export function CitationPills({ citations, className }: CitationPillsProps) {
  if (citations.length === 0) return null;

  return (
    <div
      dir="rtl"
      lang="ar"
      className={cn("flex flex-wrap gap-1.5 mt-2", className)}
    >
      {citations.map((citation) => (
        <Button
          key={citation.article_id}
          variant="outline"
          size="sm"
          className="h-7 gap-1.5 rounded-full px-3 text-xs font-normal"
          onClick={() => {
            // Future: open citation detail side panel
          }}
        >
          <Scale className="h-3 w-3 shrink-0" />
          <span>
            {citation.law_name} - مادة {citation.article_number}
          </span>
        </Button>
      ))}
    </div>
  );
}
