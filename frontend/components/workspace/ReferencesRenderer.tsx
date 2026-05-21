"use client";

import { Construction } from "lucide-react";
import { ArtifactPreview } from "./ArtifactPreview";
import type { WorkspaceItem } from "@/types";

interface ReferencesRendererProps {
  item: WorkspaceItem;
}

/**
 * Stub renderer for ``references`` items. The structured content shape and
 * renderer are deferred to a later wave (see plan: "references content spec —
 * deferred"). For now we echo whatever content_md is stored through the
 * shared ``ArtifactPreview`` so headings + lists + bold render properly and
 * the user can copy the raw body — same affordances as every other artifact.
 */
export function ReferencesRenderer({ item }: ReferencesRendererProps) {
  return (
    <div className="flex flex-1 flex-col min-h-0">
      <div className="flex items-center gap-2 border-b bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
        <Construction className="h-3.5 w-3.5" />
        <span>قيد التطوير — قائمة المراجع</span>
      </div>
      <ArtifactPreview content={item.content_md ?? ""} />
    </div>
  );
}
