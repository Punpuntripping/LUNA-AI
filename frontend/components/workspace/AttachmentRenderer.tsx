"use client";

import { useState } from "react";
import { Loader2, FileWarning, Download, FileText, ScanText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ArtifactPreview } from "./ArtifactPreview";
import { useWorkspaceItemFileUrl } from "@/hooks/use-workspace";
import type { WorkspaceItem } from "@/types";

interface AttachmentMetadata {
  filename?: string;
  mime_type?: string;
  file_size_bytes?: number;
  /**
   * OCR pipeline marker (agents/memory/ocr_extractor): absent = never
   * attempted (extraction runs when the message is sent), ``done`` = text in
   * ``content_md``, ``empty`` = no extractable text, ``failed`` /
   * ``skipped_*`` = extraction did not produce text.
   */
  ocr_status?: string;
  /**
   * Retention-sweep marker (backend/app/services/attachment_cleanup.py): the
   * original file has been removed from storage. The item and its extracted
   * text survive — this is the only thing that is gone, so there is nothing to
   * toggle to and no error to report.
   */
  original_purged_at?: string;
}

interface AttachmentRendererProps {
  item: WorkspaceItem;
}

/**
 * Renders an ``attachment`` workspace item.
 *
 * Primary view is the **pure OCR extraction** (``content_md``, written by the
 * ocr_extractor at message-send time) — that text is exactly what the agents
 * read, so showing it here lets the user verify what the pipeline actually
 * saw. A toolbar toggle switches to the original file (signed-URL image /
 * PDF iframe), which is also the automatic fallback while no extraction
 * exists yet (pre-send) or when OCR failed/was skipped.
 *
 * Once the retention sweep has purged the original (``metadata
 * .original_purged_at``) there is no second view: the extraction IS the item,
 * and the toggle is replaced by a line saying where the file went. The item
 * itself never disappears — losing it would take the WI alias and the chat
 * history's attachment tag with it.
 */
export function AttachmentRenderer({ item }: AttachmentRendererProps) {
  const meta = (item.metadata as AttachmentMetadata) || {};
  const ocrText = (item.content_md ?? "").trim();
  // The retention sweep removed the original. Never offer a toggle to a file
  // that cannot load — the extracted text is now the whole item.
  const purged = Boolean(meta.original_purged_at);
  const [showOriginal, setShowOriginal] = useState(false);

  if (ocrText && (purged || !showOriginal)) {
    return (
      <ArtifactPreview
        content={ocrText}
        copyLabel="نسخ النص المستخرج"
        headerActions={
          purged ? (
            <span className="text-xs text-muted-foreground">
              حُذف الملف الأصلي بعد انتهاء مدة الاحتفاظ
            </span>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="h-8 gap-1.5 text-xs"
              onClick={() => setShowOriginal(true)}
            >
              <FileText className="h-3.5 w-3.5" />
              الملف الأصلي
            </Button>
          )
        }
      />
    );
  }

  // Purged with nothing extracted — the only honest thing left to show is why.
  if (purged) {
    return (
      <div className="flex h-full flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
        <FileWarning className="h-8 w-8 text-muted-foreground/70" />
        <p className="text-sm text-muted-foreground">
          حُذف الملف الأصلي بعد انتهاء مدة الاحتفاظ
        </p>
        <p className="text-xs text-muted-foreground">
          {meta.ocr_status === "empty"
            ? "ولم يكن يحتوي على نص قابل للاستخراج"
            : "ولم يُستخرج منه نص"}
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-1 flex-col">
      {ocrText ? (
        // Original-file mode entered from the OCR view — offer the way back.
        <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-1.5">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            onClick={() => setShowOriginal(false)}
          >
            <ScanText className="h-3.5 w-3.5" />
            النص المستخرج
          </Button>
        </div>
      ) : (
        <OcrStatusNote status={meta.ocr_status} />
      )}
      <OriginalFileView item={item} />
    </div>
  );
}

/**
 * Slim banner explaining why there is no extracted text yet — shown above the
 * original-file fallback so the "OCR-first" contract stays visible.
 */
function OcrStatusNote({ status }: { status?: string }) {
  let text: string;
  if (!status) {
    text = "لم يُستخرج نص المستند بعد — يُستخرج تلقائيًا عند إرسال الرسالة";
  } else if (status === "empty") {
    text = "لا يحتوي المستند على نص قابل للاستخراج";
  } else {
    // failed / skipped_unsupported / skipped_quota / skipped_too_large
    text = "تعذّر استخراج نص هذا المستند";
  }
  return (
    <div className="border-b bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
      {text}
    </div>
  );
}

/**
 * The pre-existing signed-URL renderer for the raw file:
 * - ``image/*``        → ``<img>``
 * - ``application/pdf`` → ``<iframe>``
 * - anything else       → download fallback
 */
function OriginalFileView({ item }: { item: WorkspaceItem }) {
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
        className="h-full w-full flex-1 border-0"
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
