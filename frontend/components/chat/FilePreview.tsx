"use client";

import { cn } from "@/lib/utils";
import { AttachmentUploadCard } from "@/components/chat/AttachmentUploadCard";
import type { PendingFile } from "@/types";

interface FilePreviewProps {
  files: PendingFile[];
  /** Removes the file from the chat-store AND aborts its tus upload. */
  onRemove: (id: string) => void;
  className?: string;
}

/**
 * Horizontal strip of attachment cards shown above the chat input. Each
 * card renders its own progress / status / cancel UI — this component
 * only handles the layout.
 */
export function FilePreview({ files, onRemove, className }: FilePreviewProps) {
  if (files.length === 0) return null;

  return (
    <div
      dir="rtl"
      className={cn(
        "flex gap-3 overflow-x-auto py-2 px-1 scrollbar-thin",
        className,
      )}
    >
      {files.map((file) => (
        <AttachmentUploadCard
          key={file.id}
          file={file}
          onRemove={onRemove}
        />
      ))}
    </div>
  );
}
