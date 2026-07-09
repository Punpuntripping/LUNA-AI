"use client";

import { LayoutTemplate, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PendingTemplate } from "@/types";

interface TemplateChipProps {
  template: PendingTemplate;
  onRemove: () => void;
  className?: string;
}

/**
 * Composer chip for the قالب picked from the «+» menu — the قوالبي twin of
 * ``BlogChips``. Single pill (one template per message); removing it just
 * clears the store slot — nothing was created server-side.
 */
export function TemplateChip({ template, onRemove, className }: TemplateChipProps) {
  return (
    <div dir="rtl" className={cn("flex flex-wrap gap-2", className)}>
      <div className="flex max-w-64 items-center gap-1.5 rounded-full border border-border bg-muted/50 px-2.5 py-1 text-xs text-foreground">
        <LayoutTemplate className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="truncate">{template.title.trim() || "قالب"}</span>
        <button
          type="button"
          onClick={onRemove}
          aria-label="إزالة القالب"
          className="shrink-0 rounded-full p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <X className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
}
