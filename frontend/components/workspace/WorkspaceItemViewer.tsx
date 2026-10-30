"use client";

import { useEffect, useState } from "react";
import { Pencil, Save, X, Loader2, Share2, BookmarkPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { trackWiClosed } from "@/components/analytics/run-tracker";
import {
  useUpdateWorkspaceItem,
  useWorkspaceItem,
} from "@/hooks/use-workspace";
import { ArtifactPreview } from "@/components/workspace/ArtifactPreview";
import { ReferencePanel } from "@/components/workspace/ReferencePanel";
import { ShareArtifactDialog } from "@/components/workspace/ShareArtifactDialog";
import { SaveAsBlogDialog } from "@/components/workspace/SaveAsBlogDialog";
import { WiPreviewWall } from "@/components/workspace/WiPreviewWall";
import {
  useWorkspaceItemReferences,
  useWorkspaceItemReferencesHiddenCount,
} from "@/hooks/use-workspace-item-references";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";
import {
  isWiPreviewLocked,
  wiPreviewVisibleContent,
} from "@/lib/contracts/wi-preview";
import type { WorkspaceItem } from "@/types";

const USER_EDITABLE_KINDS: ReadonlySet<WorkspaceItem["kind"]> = new Set<
  WorkspaceItem["kind"]
>(["note", "agent_writing"]);

function isUserEditable(item: WorkspaceItem): boolean {
  if (!USER_EDITABLE_KINDS.has(item.kind)) return false;
  // WI preview lock (wi_preview_lock.md §1.3): the client holds only the
  // visible part — saving it would overwrite the hidden rest (server: 402).
  if (isWiPreviewLocked(item)) return false;
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
  // WI preview lock: a free user's locked report / draft — read-only, visible
  // part only, wall at the end, no share / save-as-blog (see NoteEditor).
  const previewLocked = isWiPreviewLocked(item);
  // Migration 049: refs come from workspace_item_references, not metadata.
  // Only fetched when the item is an agent_search kind; ``enabled`` keeps
  // us from spamming the endpoint on note / attachment / convo_context.
  const referenceOpts = {
    enabled: item?.kind === "agent_search",
    locked: previewLocked,
  };
  const { data: references = [], isLoading: isLoadingReferences } =
    useWorkspaceItemReferences(itemId, referenceOpts);
  const hiddenReferenceCount = useWorkspaceItemReferencesHiddenCount(
    itemId,
    referenceOpts,
  );

  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [shareOpen, setShareOpen] = useState(false);
  const [saveBlogOpen, setSaveBlogOpen] = useState(false);

  // `wi_dwell` (product_analytics §3b): this viewer is mounted for exactly as
  // long as the item is open — the dialog that hosts it unmounts on close — so
  // unmount IS the close signal, and it covers navigating away too. Id-matched
  // and idempotent inside the tracker, so it can never double-count against
  // the chat pane's own close signal, and it is a no-op for an item that was
  // opened by something other than a WorkspaceCard click.
  useEffect(() => {
    return () => {
      trackWiClosed(itemId);
    };
  }, [itemId]);

  function enterEditMode() {
    if (!item || !isUserEditable(item)) return;
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
    // Never PATCH a preview's visible part over the whole body.
    if (!item || isWiPreviewLocked(item)) return;

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
  // A preview can never be in edit mode — even if the lock arrived (refetch)
  // while the textarea was open.
  const editing = isEditing && !previewLocked;
  // Share / save-as-blog: agent drafts that are NOT a preview (§9.4).
  const publishable = item.kind === "agent_writing" && !previewLocked;

  return (
    <div className="flex flex-1 flex-col min-h-0">
      <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
        {editing ? (
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

        {!editing && (
          <div className="flex items-center gap-1 shrink-0">
            {publishable && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => setShareOpen(true)}
                aria-label="مشاركة"
              >
                <Share2 className="h-4 w-4" />
              </Button>
            )}
            {publishable && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => setSaveBlogOpen(true)}
                aria-label="حفظ كمدونة"
              >
                <BookmarkPlus className="h-4 w-4" />
              </Button>
            )}
            {editable && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={enterEditMode}
                aria-label="تعديل"
              >
                <Pencil className="h-4 w-4" />
              </Button>
            )}
          </div>
        )}

        {editing && (
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

      {editing ? (
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
        <ArtifactPreview
          // A preview renders and copies its visible part only — never the
          // legacy wall line the server appends for old builds.
          content={wiPreviewVisibleContent(item)}
          footer={
            item.kind === "agent_search" || (previewLocked && item.preview) ? (
              <>
                {previewLocked && item.preview && (
                  <WiPreviewWall
                    preview={item.preview}
                    contentType={item.kind}
                    // No ReferencePanel for a draft here, so the wall itself
                    // carries «وN مصادر أخرى في الجزء المقفل».
                    showHiddenRefs={item.kind !== "agent_search"}
                  />
                )}
                {item.kind === "agent_search" && (
                  <ReferencePanel
                    references={references}
                    // Access-tiers Phase C: enables the on-demand source reveal.
                    itemId={item.item_id}
                    isLoading={isLoadingReferences}
                    hiddenCount={previewLocked ? hiddenReferenceCount : undefined}
                  />
                )}
              </>
            ) : null
          }
        />
      )}

      <div className="border-t px-4 py-2 text-xs text-muted-foreground">
        <span>
          آخر تحديث:{" "}
          {new Intl.DateTimeFormat(AR_DATE_LOCALE, {
            dateStyle: "medium",
            timeStyle: "short",
          }).format(new Date(item.updated_at))}
        </span>
      </div>

      {publishable && (
        <ShareArtifactDialog
          itemId={item.item_id}
          open={shareOpen}
          onOpenChange={setShareOpen}
        />
      )}

      {publishable && (
        <SaveAsBlogDialog
          itemId={item.item_id}
          open={saveBlogOpen}
          onOpenChange={setSaveBlogOpen}
        />
      )}
    </div>
  );
}
