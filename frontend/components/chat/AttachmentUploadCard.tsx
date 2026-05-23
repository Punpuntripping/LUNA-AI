"use client";

import { FileText, X, Check, AlertCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PendingFile } from "@/types";

interface AttachmentUploadCardProps {
  file: PendingFile;
  onRemove: (id: string) => void;
  className?: string;
}

function isImageType(mimeType: string): boolean {
  return mimeType.startsWith("image/");
}

function getFileExtension(name: string): string {
  const ext = name.split(".").pop()?.toUpperCase();
  return ext || "FILE";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Per-file preview card shown inside the chat input while an attachment
 * is being uploaded. Mirrors the existing FilePreview card shape (so the
 * layout doesn't jump when an upload completes) but layers a progress
 * bar + cancel/retry affordances on top.
 */
export function AttachmentUploadCard({
  file,
  onRemove,
  className,
}: AttachmentUploadCardProps) {
  const isUploading =
    file.uploadStatus === "queued" || file.uploadStatus === "uploading";
  const isCompleted = file.uploadStatus === "completed";
  const isFailed = file.uploadStatus === "failed";
  const progressPct = Math.round((file.uploadProgress || 0) * 100);

  return (
    <div
      dir="rtl"
      className={cn(
        "relative flex-shrink-0 w-40 rounded-xl border bg-muted/30 overflow-hidden group",
        isFailed && "border-destructive/40",
        isCompleted && "border-emerald-500/40",
        className,
      )}
    >
      {/* Top-start remove / cancel button. While uploading this also
          triggers the tus abort via the parent — onRemove is wired to
          both pull from the store AND call upload.cancel(). */}
      <button
        type="button"
        onClick={() => onRemove(file.id)}
        className="absolute top-1.5 start-1.5 z-10 flex h-5 w-5 items-center justify-center rounded-full bg-foreground/80 text-background hover:bg-foreground transition-colors"
        aria-label={isUploading ? "إلغاء الرفع" : "حذف الملف"}
      >
        <X className="h-3 w-3" />
      </button>

      {/* Status badge — top-end corner. */}
      {isCompleted && (
        <div className="absolute top-1.5 end-1.5 z-10 flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-white">
          <Check className="h-3 w-3" />
        </div>
      )}
      {isFailed && (
        <div className="absolute top-1.5 end-1.5 z-10 flex h-5 w-5 items-center justify-center rounded-full bg-destructive text-destructive-foreground">
          <AlertCircle className="h-3 w-3" />
        </div>
      )}

      {/* Visual area — image thumbnail or PDF icon. */}
      {isImageType(file.mimeType) && file.previewUrl ? (
        <div className="h-20 w-full relative">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={file.previewUrl}
            alt={file.name}
            className={cn(
              "w-full h-full object-cover",
              isUploading && "opacity-60",
            )}
          />
          {isUploading && (
            <div className="absolute inset-0 flex items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-foreground/80" />
            </div>
          )}
        </div>
      ) : (
        <div className="h-20 w-full flex items-center justify-center">
          {isUploading ? (
            <Loader2 className="h-7 w-7 animate-spin text-muted-foreground" />
          ) : (
            <FileText
              className={cn(
                "h-8 w-8",
                isFailed
                  ? "text-destructive/60"
                  : "text-muted-foreground/50",
              )}
            />
          )}
        </div>
      )}

      {/* Info strip: filename + size + status line. */}
      <div className="px-2.5 py-1.5 border-t bg-background/50 space-y-1">
        <p className="text-xs text-foreground truncate font-medium">
          {file.name}
        </p>

        {isUploading && (
          <>
            {/* Progress bar — width tracks uploadProgress 0..1. */}
            <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all duration-200"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <p className="text-[10px] text-muted-foreground tabular-nums">
              {progressPct}% · {formatBytes(file.size)}
            </p>
          </>
        )}

        {isCompleted && (
          <span className="inline-block rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            {getFileExtension(file.name)} · {formatBytes(file.size)}
          </span>
        )}

        {isFailed && (
          <p className="text-[10px] text-destructive">
            {file.errorMessage ?? "فشل الرفع"}
          </p>
        )}
      </div>
    </div>
  );
}
