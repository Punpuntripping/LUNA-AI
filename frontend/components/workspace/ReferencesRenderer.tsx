"use client";

import { Construction } from "lucide-react";
import type { WorkspaceItem } from "@/types";

interface ReferencesRendererProps {
  item: WorkspaceItem;
}

/**
 * Stub renderer for ``references`` items. The content shape and renderer
 * are deferred to a later wave (see plan: "references content spec --
 * deferred"). For now we just echo what's stored, with a "coming soon"
 * banner so the user knows it's intentional.
 */
export function ReferencesRenderer({ item }: ReferencesRendererProps) {
  return (
    <div className="flex flex-1 flex-col">
      <div className="flex items-center gap-2 border-b bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
        <Construction className="h-3.5 w-3.5" />
        <span>قيد التطوير — قائمة المراجع</span>
      </div>
      <div className="flex-1 overflow-auto p-4 text-sm text-foreground" dir="rtl">
        <p className="whitespace-pre-wrap">{item.content_md ?? ""}</p>
      </div>
    </div>
  );
}
