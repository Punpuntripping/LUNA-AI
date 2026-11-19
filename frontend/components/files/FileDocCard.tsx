"use client";

import Link from "next/link";
import { ListTree } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  fileTypePillLabel,
  formatFileDate,
  formatPageCount,
  type LibraryFile,
} from "@/lib/contracts";
import { FileCardMenu } from "./FileCard";
import { FileTypeTile, fileFamilyStyle } from "./file-type-style";
import { FILES_COPY } from "./copy";

interface FileDocCardProps {
  file: LibraryFile;
  /** The file page. The whole card is the open target. */
  href: string;
  onRequestDelete: () => void;
  /** Phase 3 «أضف إلى محادثة» in the menu, inactive («قريبًا»). */
  showUpcoming?: boolean;
}

/** What an untyped file is, when the summarizer has not said: its format. */
function formatLabel(mime: string | null): string | null {
  if (!mime) return null;
  if (mime === "application/pdf") return "PDF";
  if (mime.startsWith("image/")) return "صورة";
  return null;
}

/**
 * One file on `/files` — a "document tile", sibling of the template card
 * (`MyTemplatesGrid`: rounded-xl card, bold 2-line title, 3-line preview,
 * quiet date line, action at the top end) but recognisably a FILE:
 *
 * - the type's icon on its family tint (`FileTypeTile`) leads, so the type
 *   reads before the label does;
 * - a slim spine on the START edge (the binding side of an Arabic document)
 *   in the family's tint — the ⋮ menu keeps the top END corner to itself;
 * - a dashed rule above the footer, like the fold of a page, then pages · date
 *   and «مقسّم إلى أقسام» when the file opens as an outline.
 *
 * Equal height in a row (`h-full` + footer pushed down); a long title clamps
 * to two lines and breaks inside long unspaced names; no summary shows
 * «لا يوجد ملخص بعد». The pane keeps its compact `FileCard`.
 */
export function FileDocCard({
  file,
  href,
  onRequestDelete,
  showUpcoming = false,
}: FileDocCardProps) {
  const style = fileFamilyStyle(file.doc_type);
  const typeLabel = fileTypePillLabel(file.doc_type);
  const label = typeLabel ?? formatLabel(file.mime_type);
  const pages = formatPageCount(file.page_count);
  const date = formatFileDate(file.created_at);
  const summary = (file.summary_preview ?? "").trim();

  return (
    <div className="relative h-full" dir="rtl">
      <Link
        href={href}
        className={cn(
          "relative flex h-full flex-col overflow-hidden rounded-xl border bg-card p-4 ps-5 shadow-sm transition",
          "hover:border-primary/30 hover:shadow-md",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        )}
      >
        <span aria-hidden="true" className={cn("absolute inset-y-0 start-0 w-1", style.spine)} />

        <div className="flex min-w-0 items-center gap-2.5 pe-8">
          <FileTypeTile docType={file.doc_type} />
          {label && (
            <span
              className={cn(
                "min-w-0 truncate text-xs",
                typeLabel ? cn("font-semibold", style.label) : "font-medium text-muted-foreground",
              )}
            >
              {label}
            </span>
          )}
        </div>

        <h3
          className="mt-3 line-clamp-2 break-words text-sm font-bold leading-6 text-foreground"
          title={file.title}
        >
          {file.title}
        </h3>

        {summary ? (
          <p className="mt-1.5 line-clamp-3 break-words text-xs leading-relaxed text-muted-foreground">
            {summary}
          </p>
        ) : (
          <p className="mt-1.5 text-xs text-muted-foreground/70">{FILES_COPY.noSummary}</p>
        )}

        <div className="mt-auto pt-4">
          <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 border-t border-dashed border-border pt-2.5 text-xs tabular-nums text-muted-foreground">
            {pages && <span className="whitespace-nowrap">{pages}</span>}
            {pages && date && <span aria-hidden="true">·</span>}
            {date && <span className="whitespace-nowrap">{date}</span>}
            {file.has_sections && (
              <span className="ms-auto inline-flex items-center gap-1 whitespace-nowrap">
                <ListTree className="h-3.5 w-3.5" aria-hidden="true" />
                {FILES_COPY.hasSections}
              </span>
            )}
          </div>
        </div>
      </Link>

      <FileCardMenu
        href={href}
        onRequestDelete={onRequestDelete}
        showUpcoming={showUpcoming}
        className="absolute end-2 top-2"
      />
    </div>
  );
}
