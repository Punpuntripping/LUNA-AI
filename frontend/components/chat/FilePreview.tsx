"use client";

import { FileText, X, ImageIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PendingFile } from "@/types";

interface FilePreviewProps {
  files: PendingFile[];
  onRemove: (id: string) => void;
  className?: string;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isImageType(mimeType: string): boolean {
  return mimeType.startsWith("image/");
}

export function FilePreview({ files, onRemove, className }: FilePreviewProps) {
  if (files.length === 0) return null;

  return (
    <div
      dir="rtl"
      className={cn(
        "flex gap-2 overflow-x-auto py-2 px-1 scrollbar-thin",
        className
      )}
    >
      {files.map((file) => (
        <div
          key={file.id}
          className="relative flex-shrink-0 group rounded-lg border bg-muted/50 overflow-hidden"
        >
          {/* Remove button */}
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-0.5 end-0.5 z-10 h-5 w-5 rounded-full bg-background/80 opacity-0 group-hover:opacity-100 transition-opacity"
            onClick={() => onRemove(file.id)}
            aria-label="حذف الملف"
          >
            <X className="h-3 w-3" />
          </Button>

          {isImageType(file.mimeType) ? (
            /* Image thumbnail */
            <div className="w-20 h-20">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={file.previewUrl}
                alt={file.name}
                className="w-full h-full object-cover"
              />
            </div>
          ) : (
            /* PDF / other file icon */
            <div className="w-20 h-20 flex flex-col items-center justify-center gap-1 p-2">
              <FileText className="h-6 w-6 text-muted-foreground" />
              <ImageIcon className="hidden" />
            </div>
          )}

          {/* File info strip */}
          <div className="px-2 py-1 bg-background/80 border-t">
            <p className="text-[10px] text-foreground truncate max-w-[72px]">
              {file.name}
            </p>
            <p className="text-[9px] text-muted-foreground">
              {formatFileSize(file.size)}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}
