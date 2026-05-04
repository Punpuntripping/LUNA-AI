"use client";

import { useState } from "react";
import { ChevronDown, ChevronLeft, FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { WorkspaceCard } from "./WorkspaceCard";
import type { WorkspaceItem, WorkspaceItemKind } from "@/types";

const KIND_LABELS: Record<WorkspaceItemKind, string> = {
  attachment: "المرفقات",
  note: "الملاحظات",
  agent_search: "نتائج البحث",
  agent_writing: "المسودات",
  convo_context: "ملخص المحادثة",
  references: "المراجع",
};

/** Order in which kind groups appear */
const KIND_ORDER: WorkspaceItemKind[] = [
  "agent_writing",
  "agent_search",
  "note",
  "attachment",
  "references",
  "convo_context",
];

interface WorkspaceListProps {
  items: WorkspaceItem[] | undefined;
  isLoading: boolean;
  onItemClick?: (itemId: string) => void;
}

/**
 * Groups workspace items by ``kind``, rendering each group as a collapsible
 * section. The pre-rename UI grouped on ``artifact_type``; that lives in
 * ``metadata.subtype`` now and is shown on the card itself.
 */
export function WorkspaceList({
  items,
  isLoading,
  onItemClick,
}: WorkspaceListProps) {
  const [collapsedGroups, setCollapsedGroups] = useState<
    Set<WorkspaceItemKind>
  >(new Set());

  function toggleGroup(kind: WorkspaceItemKind) {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) {
        next.delete(kind);
      } else {
        next.add(kind);
      }
      return next;
    });
  }

  if (isLoading) {
    return (
      <div className="space-y-3 p-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="animate-pulse space-y-2">
            <div className="h-4 w-24 rounded bg-muted" />
            <div className="h-16 w-full rounded-lg bg-muted" />
          </div>
        ))}
      </div>
    );
  }

  if (!items || items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
        <FileText className="h-10 w-10 text-muted-foreground/50" />
        <p className="text-sm text-muted-foreground">لا توجد عناصر بعد</p>
      </div>
    );
  }

  const grouped = new Map<WorkspaceItemKind, WorkspaceItem[]>();
  for (const item of items) {
    const existing = grouped.get(item.kind);
    if (existing) {
      existing.push(item);
    } else {
      grouped.set(item.kind, [item]);
    }
  }

  const sortedKinds = KIND_ORDER.filter((k) => grouped.has(k));

  return (
    <div className="space-y-1 p-2">
      {sortedKinds.map((kind) => {
        const groupItems = grouped.get(kind)!;
        const isCollapsed = collapsedGroups.has(kind);
        const label = KIND_LABELS[kind] ?? kind;

        return (
          <div key={kind}>
            <button
              onClick={() => toggleGroup(kind)}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-start text-xs font-semibold text-muted-foreground hover:bg-accent/30 transition-colors"
            >
              {isCollapsed ? (
                <ChevronLeft className="h-3.5 w-3.5 shrink-0" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5 shrink-0" />
              )}
              <span>{label}</span>
              <span className="ms-auto text-[10px] font-normal tabular-nums">
                {groupItems.length}
              </span>
            </button>

            {!isCollapsed && (
              <div
                className={cn(
                  "space-y-1.5 pb-2",
                  sortedKinds.length > 1 && "ps-2",
                )}
              >
                {groupItems.map((item) => (
                  <WorkspaceCard
                    key={item.item_id}
                    item={item}
                    onClick={onItemClick}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
