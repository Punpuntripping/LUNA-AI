"use client";

import { useState } from "react";
import { Pencil, Save, X, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useArtifact, useUpdateArtifact } from "@/hooks/use-artifacts";

interface ArtifactViewerProps {
  artifactId: string;
}

export function ArtifactViewer({ artifactId }: ArtifactViewerProps) {
  const { data: artifact, isLoading, error } = useArtifact(artifactId);
  const updateArtifact = useUpdateArtifact();

  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [editTitle, setEditTitle] = useState("");

  function enterEditMode() {
    if (!artifact) return;
    setEditContent(artifact.content_md);
    setEditTitle(artifact.title);
    setIsEditing(true);
  }

  function cancelEdit() {
    setIsEditing(false);
    setEditContent("");
    setEditTitle("");
  }

  function handleSave() {
    if (!artifact) return;

    updateArtifact.mutate(
      {
        artifactId: artifact.artifact_id,
        data: {
          title: editTitle.trim() || artifact.title,
          content_md: editContent,
        },
      },
      {
        onSuccess: () => {
          setIsEditing(false);
        },
      }
    );
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">جارٍ تحميل المستند...</p>
        </div>
      </div>
    );
  }

  // Error state
  if (error || !artifact) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <p className="text-sm text-destructive">
          حدث خطأ في تحميل المستند
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col min-h-0">
      {/* Header: title + edit controls */}
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
            {artifact.title}
          </h3>
        )}

        {artifact.is_editable && !isEditing && (
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
              disabled={updateArtifact.isPending}
              aria-label="إلغاء"
            >
              <X className="h-4 w-4" />
            </Button>
            <Button
              variant="default"
              size="sm"
              className="h-8 gap-1.5"
              onClick={handleSave}
              disabled={updateArtifact.isPending}
            >
              {updateArtifact.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              حفظ
            </Button>
          </div>
        )}
      </div>

      {/* Content area */}
      {isEditing ? (
        <div className="flex-1 p-4">
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="h-full w-full resize-none rounded-md border border-input bg-background p-3 text-sm leading-relaxed placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            dir="rtl"
            placeholder="محتوى المستند..."
          />
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div
            className="p-4 text-sm leading-relaxed text-foreground whitespace-pre-wrap"
            dir="rtl"
          >
            {artifact.content_md}
          </div>
        </ScrollArea>
      )}

      {/* Footer: metadata */}
      <div className="border-t px-4 py-2 text-[11px] text-muted-foreground">
        <span>
          آخر تحديث:{" "}
          {new Intl.DateTimeFormat("ar-SA", {
            dateStyle: "medium",
            timeStyle: "short",
          }).format(new Date(artifact.updated_at))}
        </span>
      </div>
    </div>
  );
}
