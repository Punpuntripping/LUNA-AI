"use client";

import { useState } from "react";
import { Pencil, Save, X, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  useUpdateWorkspaceItem,
  useWorkspaceItem,
} from "@/hooks/use-workspace";
import type { WorkspaceItem } from "@/types";

const USER_EDITABLE_KINDS: ReadonlySet<WorkspaceItem["kind"]> = new Set<
  WorkspaceItem["kind"]
>(["note", "agent_writing"]);

function isUserEditable(item: WorkspaceItem): boolean {
  if (!USER_EDITABLE_KINDS.has(item.kind)) return false;
  if (item.kind === "agent_writing") {
    const lockedUntil =
      (item.metadata as { locked_until?: string } | undefined)?.locked_until;
    if (lockedUntil) {
      const lockTs = Date.parse(lockedUntil);
      if (!Number.isNaN(lockTs) && lockTs > Date.now()) return false;
    }
  }
  return true;
}

interface WorkspaceItemViewerProps {
  itemId: string;
}

export function WorkspaceItemViewer({ itemId }: WorkspaceItemViewerProps) {
  const { data: item, isLoading, error } = useWorkspaceItem(itemId);
  const updateItem = useUpdateWorkspaceItem();

  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [editTitle, setEditTitle] = useState("");

  function enterEditMode() {
    if (!item) return;
    setEditContent(item.content_md ?? "");
    setEditTitle(item.title);
    setIsEditing(true);
  }

  function cancelEdit() {
    setIsEditing(false);
    setEditContent("");
    setEditTitle("");
  }

  function handleSave() {
    if (!item) return;

    updateItem.mutate(
      {
        itemId: item.item_id,
        data: {
          title: editTitle.trim() || item.title,
          content_md: editContent,
        },
      },
      {
        onSuccess: () => {
          setIsEditing(false);
        },
      },
    );
  }

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">جارٍ تحميل العنصر...</p>
        </div>
      </div>
    );
  }

  if (error || !item) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <p className="text-sm text-destructive">حدث خطأ في تحميل العنصر</p>
      </div>
    );
  }

  const editable = isUserEditable(item);

  return (
    <div className="flex flex-1 flex-col min-h-0">
      <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
        {isEditing ? (
          <input
            type="text"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            className="flex-1 rounded-md border border-input bg-background px-2 py-1 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-ring"
            dir="rtl"
          />
        ) : (
          <h3 className="flex-1 truncate text-sm font-semibold text-foreground">
            {item.title}
          </h3>
        )}

        {editable && !isEditing && (
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0"
            onClick={enterEditMode}
            aria-label="تعديل"
          >
            <Pencil className="h-4 w-4" />
          </Button>
        )}

        {isEditing && (
          <div className="flex items-center gap-1 shrink-0">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={cancelEdit}
              disabled={updateItem.isPending}
              aria-label="إلغاء"
            >
              <X className="h-4 w-4" />
            </Button>
            <Button
              variant="default"
              size="sm"
              className="h-8 gap-1.5"
              onClick={handleSave}
              disabled={updateItem.isPending}
            >
              {updateItem.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              حفظ
            </Button>
          </div>
        )}
      </div>

      {isEditing ? (
        <div className="flex-1 p-4">
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="h-full w-full resize-none rounded-md border border-input bg-background p-3 text-sm leading-relaxed placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            dir="rtl"
            placeholder="محتوى العنصر..."
          />
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div
            className="p-4 text-sm leading-relaxed text-foreground whitespace-pre-wrap"
            dir="rtl"
          >
            {item.content_md ?? ""}
          </div>
        </ScrollArea>
      )}

      <div className="border-t px-4 py-2 text-[11px] text-muted-foreground">
        <span>
          آخر تحديث:{" "}
          {new Intl.DateTimeFormat("ar-SA", {
            dateStyle: "medium",
            timeStyle: "short",
          }).format(new Date(item.updated_at))}
        </span>
      </div>
    </div>
  );
}
