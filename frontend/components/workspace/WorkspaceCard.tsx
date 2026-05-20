"use client";

import { useEffect, useRef } from "react";
import { FileText, Paperclip, NotebookPen, BookOpen, MessageSquare } from "lucide-react";
import { cn, getRelativeTimeAr } from "@/lib/utils";
import { useChatStore } from "@/stores/chat-store";
import type { WorkspaceItem, WorkspaceItemKind } from "@/types";

const KIND_ICON: Record<WorkspaceItemKind, typeof FileText> = {
  attachment: Paperclip,
  note: NotebookPen,
  agent_search: BookOpen,
  agent_writing: FileText,
  convo_context: MessageSquare,
  references: BookOpen,
};

const KIND_LABEL: Record<WorkspaceItemKind, string> = {
  attachment: "مرفق",
  note: "ملاحظة",
  agent_search: "بحث",
  agent_writing: "مسودة",
  convo_context: "ملخص المحادثة",
  references: "مراجع",
};

const SUBTYPE_LABEL: Record<string, string> = {
  report: "تقرير",
  contract: "عقد",
  memo: "مذكرة",
  summary: "ملخص",
  memory_file: "ذاكرة",
  legal_opinion: "رأي قانوني",
  legal_synthesis: "تحليل قانوني",
};

const KIND_COLORS: Record<WorkspaceItemKind, string> = {
  attachment: "bg-slate-500/10 text-slate-500",
  note: "bg-amber-500/10 text-amber-500",
  agent_search: "bg-teal-500/10 text-teal-500",
  agent_writing: "bg-indigo-500/10 text-indigo-500",
  convo_context: "bg-sky-500/10 text-sky-500",
  references: "bg-violet-500/10 text-violet-500",
};

interface WorkspaceCardProps {
  item: WorkspaceItem;
  onClick?: (itemId: string) => void;
}

export function WorkspaceCard({ item, onClick }: WorkspaceCardProps) {
  const openWorkspaceItem = useChatStore((s) => s.openWorkspaceItem);
  // Phase E (§9 O5): when the chat-store flags this card's item_id as the
  // currently highlighted one, render a primary ring and scroll into view.
  // Scoped per-conversation, so a card in a different conversation never
  // accidentally rings.
  const isHighlighted = useChatStore((s) => {
    if (!item.conversation_id) return false;
    return (
      s.workspaceByConversation[item.conversation_id]?.highlightedItemId ===
      item.item_id
    );
  });

  const buttonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!isHighlighted) return;
    // The pane's outer ScrollArea handles vertical scroll; `scrollIntoView`
    // with `block: "center"` keeps the card comfortably in view without
    // pinning it to either edge. `behavior: "smooth"` matches the existing
    // ref-flash UX so the two highlights feel like the same family.
    buttonRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [isHighlighted]);

  const Icon = KIND_ICON[item.kind] ?? FileText;
  const subtype = (item.metadata as { subtype?: string } | undefined)?.subtype;
  const label = subtype
    ? SUBTYPE_LABEL[subtype] ?? subtype
    : KIND_LABEL[item.kind];
  const badgeColor =
    KIND_COLORS[item.kind] ?? "bg-muted text-muted-foreground";

  function handleClick() {
    if (onClick) {
      onClick(item.item_id);
    } else if (item.conversation_id) {
      openWorkspaceItem(item.conversation_id, item.item_id);
    }
  }

  return (
    <button
      ref={buttonRef}
      id={`workspace-item-${item.item_id}`}
      onClick={handleClick}
      className={cn(
        "w-full rounded-lg border border-border/50 bg-card p-3 text-start",
        "transition-[box-shadow,background-color] duration-300",
        "hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        // Phase E (§9 O5): primary ring while highlighted; clears itself
        // ~2.5s after the chip click via the store's setTimeout.
        isHighlighted && "ring-2 ring-primary ring-offset-2 ring-offset-background",
      )}
    >
      <div className="flex items-start gap-3">
        <div className="mt-0.5 shrink-0">
          <Icon className="h-4 w-4 text-muted-foreground" />
        </div>

        <div className="flex-1 min-w-0 space-y-1.5">
          <p className="text-sm font-medium truncate text-foreground">
            {item.title}
          </p>

          <div className="flex items-center gap-2">
            <span
              className={cn(
                "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium",
                badgeColor,
              )}
            >
              {label}
            </span>

            <span className="text-[11px] text-muted-foreground">
              {getRelativeTimeAr(item.created_at)}
            </span>
          </div>
        </div>
      </div>
    </button>
  );
}
