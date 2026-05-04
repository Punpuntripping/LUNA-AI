"use client";

import { Loader2, FileWarning, Download } from "lucide-react";
import { useWorkspaceItemFileUrl } from "@/hooks/use-workspace";
import type { WorkspaceItem } from "@/types";

interface AttachmentMetadata {
  filename?: string;
  mime_type?: string;
  file_size_bytes?: number;
}

interface AttachmentRendererProps {
  item: WorkspaceItem;
}

/**
 * Renders an ``attachment`` workspace item.
 *
 * - ``image/*``       → ``<img>`` with the signed URL
 * - ``application/pdf``→ ``<iframe>`` with the signed URL
 * - anything else      → download fallback
 */
export function AttachmentRenderer({ item }: AttachmentRendererProps) {
  const { data, isLoading, error } = useWorkspaceItemFileUrl(item.item_id);
  const meta = (item.metadata as AttachmentMetadata) || {};
  const mimeType = meta.mime_type ?? "application/octet-stream";
  const filename = meta.filename ?? item.title;

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">جارٍ تحميل المرفق...</p>
        </div>
      </div>
    );
  }

  if (error || !data?.url) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <FileWarning className="h-8 w-8 text-destructive/70" />
        <p className="text-sm text-destructive">تعذّر تحميل الملف</p>
      </div>
    );
  }

  if (mimeType.startsWith("image/")) {
    return (
      <div className="flex flex-1 items-center justify-center overflow-auto bg-muted/40 p-4">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={data.url}
          alt={filename}
          className="max-h-full max-w-full rounded-md object-contain shadow"
        />
      </div>
    );
  }

  if (mimeType === "application/pdf") {
    return (
      <iframe
        src={data.url}
        title={filename}
        className="h-full w-full border-0"
      />
    );
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <p className="text-sm text-muted-foreground">
        لا يمكن عرض هذا النوع داخل المتصفح
      </p>
      <a
        href={data.url}
        download={filename}
        target="_blank"
        rel="noreferrer"
        className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90"
      >
        <Download className="me-2 h-4 w-4" />
        تنزيل {filename}
      </a>
    </div>
  );
}
