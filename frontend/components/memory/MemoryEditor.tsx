"use client";

import { useState, useEffect } from "react";
import { Save, Loader2, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useArtifact, useUpdateArtifact } from "@/hooks/use-artifacts";

interface MemoryEditorProps {
  artifactId: string;
}

export function MemoryEditor({ artifactId }: MemoryEditorProps) {
  const { data: artifact, isLoading, error } = useArtifact(artifactId);
  const updateArtifact = useUpdateArtifact();

  const [isEditing, setIsEditing] = useState(false);
  const [content, setContent] = useState("");

  // Sync content when artifact loads or changes
  useEffect(() => {
    if (artifact) {
      setContent(artifact.content_md);
    }
  }, [artifact]);

  function handleSave() {
    if (!artifact) return;

    updateArtifact.mutate(
      {
        artifactId: artifact.artifact_id,
        data: { content_md: content },
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
      <div className="flex items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">جارٍ تحميل الذاكرة...</p>
        </div>
      </div>
    );
  }

  // Error state
  if (error || !artifact) {
    return (
      <div className="flex items-center justify-center p-8">
        <p className="text-sm text-destructive">
          حدث خطأ في تحميل الذاكرة
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col rounded-lg border bg-card">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
        <h3 className="text-sm font-semibold text-foreground">
          {artifact.title}
        </h3>

        <div className="flex items-center gap-1">
          {isEditing ? (
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
          ) : (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => setIsEditing(true)}
              aria-label="تعديل"
            >
              <Pencil className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Content */}
      {isEditing ? (
        <div className="p-4">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="min-h-[200px] w-full resize-y rounded-md border border-input bg-background p-3 text-sm leading-relaxed placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            dir="rtl"
            placeholder="اكتب ملاحظات القضية هنا..."
          />
        </div>
      ) : (
        <ScrollArea className="max-h-[400px]">
          <div
            className="p-4 text-sm leading-relaxed text-foreground whitespace-pre-wrap"
            dir="rtl"
          >
            {artifact.content_md || (
              <span className="text-muted-foreground">
                لا توجد ملاحظات بعد. اضغط على زر التعديل لإضافة ملاحظات.
              </span>
            )}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
