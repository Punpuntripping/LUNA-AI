"use client";

import { useId, useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronLeft, FileText, ScanText } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { CopyMenu } from "@/components/common/CopyMenu";
import { OriginalFileView } from "@/components/workspace/AttachmentRenderer";
import {
  fileTypePillLabel,
  formatFileDate,
  formatPageCount,
  stripMarkdownImages,
  type LibraryFileDetail,
} from "@/lib/contracts";
import { FileTypePill } from "./FileTypePill";
import { FileTypeTile, fileFamilyStyle } from "./file-type-style";
import { FileSections } from "./FileSections";
import { FILES_COPY } from "./copy";

interface FileViewerProps {
  file: LibraryFileDetail;
  /**
   * `page` — `/chats/files/[itemId]`: renders the title as the page heading.
   * `pane` — the conversation pane's detail mode, whose header already shows
   * the title and the WI badge, so the viewer starts at the meta line.
   */
  variant?: "page" | "pane";
  /** Shown above everything — the pane passes the partial-read banner. */
  notice?: ReactNode;
}

/**
 * One opened file (plan §3.3), for the pane and for `/files/[itemId]` alike.
 *
 * - Header: page — the type tile (the same identity as the `/files` card), the
 *   type label, the title, pages · date; pane — type pill, pages, date (the
 *   pane header already shows the title and the WI badge).
 * - Toolbar: «الملف الأصلي» while the original can still be opened (else the
 *   retention line), «نسخ النص» (the full extracted text).
 * - The full stored summary, collapsible.
 * - The sections when the file has a fresh map; otherwise (`sections: null` —
 *   no map, or a stale one) the full extracted text exactly as the attachment
 *   viewer has always shown it.
 */
export function FileViewer({ file, variant = "page", notice }: FileViewerProps) {
  const [showOriginal, setShowOriginal] = useState(false);
  const text = file.content_md ?? "";
  const pages = formatPageCount(file.page_count);
  const date = formatFileDate(file.created_at);

  if (showOriginal && file.original_available) {
    // Same shape as AttachmentRenderer's original-file mode: a bar with the
    // way back, then the signed-URL view filling the rest.
    return (
      <div className="flex min-h-0 flex-1 flex-col" dir="rtl">
        <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-1.5">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 gap-1.5 text-xs"
            onClick={() => setShowOriginal(false)}
          >
            <ScanText className="h-3.5 w-3.5" />
            {FILES_COPY.extractedText}
          </Button>
        </div>
        <div className={cn("flex min-h-0 flex-1 flex-col", variant === "page" && "min-h-[75dvh]")}>
          <OriginalFileView
            itemId={file.item_id}
            mimeType={file.mime_type}
            filename={file.filename ?? file.title}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col" dir="rtl">
      {notice}
      <div className={cn(variant === "pane" ? "px-4 pb-8 pt-3" : "pb-10")}>
        {variant === "page" ? (
          <PageHeader file={file} pages={pages} date={date} />
        ) : (
          <header>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs tabular-nums text-muted-foreground">
              <FileTypePill docType={file.doc_type} />
              {pages && <span className="whitespace-nowrap">{pages}</span>}
              {pages && date && <span aria-hidden="true">·</span>}
              {date && <span className="whitespace-nowrap">{date}</span>}
            </div>
          </header>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-1 border-y border-border/60 py-1">
          {file.original_available ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground max-md:h-9"
              onClick={() => setShowOriginal(true)}
            >
              <FileText className="h-3.5 w-3.5" />
              {FILES_COPY.original}
            </Button>
          ) : (
            <span className="px-2 text-xs text-muted-foreground">{FILES_COPY.purged}</span>
          )}
          <CopyMenu variant="bar" text={text} label={FILES_COPY.copyText} />
        </div>

        {file.summary?.trim() ? <SummaryBlock summary={file.summary} /> : null}

        {file.sections ? (
          <FileSections sections={file.sections} documents={file.documents ?? []} />
        ) : (
          <FullText text={text} />
        )}
      </div>
    </div>
  );
}

/** The file page's header: type tile + label, title, pages · date. */
function PageHeader({
  file,
  pages,
  date,
}: {
  file: LibraryFileDetail;
  pages: string;
  date: string;
}) {
  const label = fileTypePillLabel(file.doc_type);
  return (
    <header className="flex items-start gap-3.5">
      <FileTypeTile docType={file.doc_type} size="lg" className="mt-1" />
      <div className="min-w-0 flex-1">
        {label && (
          <p className={cn("text-xs font-semibold", fileFamilyStyle(file.doc_type).label)}>
            {label}
          </p>
        )}
        <h1 className="break-words text-xl font-semibold leading-8 text-foreground sm:text-2xl sm:leading-9">
          {file.title}
        </h1>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs tabular-nums text-muted-foreground">
          {pages && <span className="whitespace-nowrap">{pages}</span>}
          {pages && date && <span aria-hidden="true">·</span>}
          {date && <span className="whitespace-nowrap">{date}</span>}
        </div>
      </div>
    </header>
  );
}

function SummaryBlock({ summary }: { summary: string }) {
  const [open, setOpen] = useState(true);
  const bodyId = useId();
  return (
    <section className="mt-4 rounded-lg border border-border/60 bg-muted/30">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-center gap-2 rounded-lg px-4 py-2.5 text-start text-sm font-semibold text-foreground",
          "hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <ChevronLeft className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        )}
        {FILES_COPY.summary}
      </button>
      {open && (
        <div id={bodyId} className="border-t border-border/60 px-4 py-3">
          <MarkdownRenderer content={summary} />
        </div>
      )}
    </section>
  );
}

/**
 * No map (or a stale one): the whole extracted text, as before — through the
 * same renderer — minus the OCR image placeholders (`![img-0.jpeg](img-0.jpeg)`),
 * which point at nothing and rendered as broken images. «نسخ النص» still
 * copies the stored text untouched.
 */
function FullText({ text }: { text: string }) {
  const shown = useMemo(() => stripMarkdownImages(text), [text]);
  return (
    <section className="mt-6">
      <h2 className="mb-2 text-sm font-semibold text-foreground">{FILES_COPY.extractedText}</h2>
      {shown.trim() ? (
        <MarkdownRenderer content={shown} />
      ) : (
        <p className="text-sm text-muted-foreground">{FILES_COPY.noText}</p>
      )}
    </section>
  );
}
