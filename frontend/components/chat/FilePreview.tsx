"use client";

import { FileText, X, ImageIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PendingFile } from "@/types";

interface FilePreviewProps {
  files: PendingFile[];
  onRemove: (id: string) => void;
  className?: string;
}

function getFileExtension(name: string): string {
  const ext = name.split(".").pop()?.toUpperCase();
  return ext || "FILE";
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
        "flex gap-3 overflow-x-auto py-2 px-1 scrollbar-thin",
        className
      )}
    >
      {files.map((file) => (
        <div
          key={file.id}
          className="relative flex-shrink-0 w-36 rounded-xl border bg-muted/30 overflow-hidden group"
        >
          {/* Remove button — top-start corner */}
          <button
            type="button"
            onClick={() => onRemove(file.id)}
            className="absolute top-1.5 start-1.5 z-10 flex h-5 w-5 items-center justify-center rounded-full bg-foreground/80 text-background hover:bg-foreground transition-colors"
            aria-label="حذف الملف"
          >
            <X className="h-3 w-3" />
          </button>

          {isImageType(file.mimeType) ? (
            /* Image thumbnail */
            <div className="h-20 w-full">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={file.previewUrl}
                alt={file.name}
                className="w-full h-full object-cover"
              />
            </div>
          ) : (
            /* Document preview area */
            <div className="h-20 w-full flex items-center justify-center">
              <FileText className="h-8 w-8 text-muted-foreground/50" />
              <ImageIcon className="hidden" />
            </div>
          )}

          {/* File info strip */}
          <div className="px-2.5 py-1.5 border-t bg-background/50">
            <p className="text-xs text-foreground truncate font-medium">
              {file.name}
            </p>
            <span className="inline-block mt-0.5 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              {getFileExtension(file.name)}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
