"use client";

import { cn } from "@/lib/utils";
import { FILE_REFS_AR, formatSectionPages, type FileRefRow } from "@/lib/contracts";
import { FileRefSquare } from "./FileRefChip";

/**
 * «من مستنداتك» (``.claude/plans/aggregator_file_refs.md`` §4.6, D9): under
 * «المراجع», one line per cited part of the user's own files, in order of first
 * mention — «7 · أسباب الحكم — الحكم المرفق (ص 3–4)»; a whole document shows
 * the file icon and its title. A line opens the same popup as the chip.
 * Renders nothing when the report cites no file.
 */
export function FileRefList({
  rows,
  onOpen,
}: {
  rows: FileRefRow[];
  onOpen: (ref: string) => void;
}) {
  if (rows.length === 0) return null;
  return (
    <div dir="rtl" className="mt-6 border-t pt-4" data-testid="file-refs-list">
      <h3 className="mb-3 text-sm font-semibold text-foreground">
        {FILE_REFS_AR.listTitle} <span className="text-muted-foreground">({rows.length})</span>
      </h3>
      <ul className="flex flex-col gap-1">
        {rows.map((row) => {
          const whole = row.section_seq == null;
          const pages = formatSectionPages(row.page_from, row.page_to);
          return (
            <li key={row.ref_id}>
              <button
                type="button"
                onClick={() => onOpen(row.ref_id)}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md px-1.5 py-1 text-start text-sm leading-6 transition-colors",
                  "hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  !row.available && "text-muted-foreground",
                )}
              >
                <FileRefSquare row={row} />
                <span className="min-w-0 flex-1">
                  {whole ? (
                    row.doc_title || FILE_REFS_AR.wholeDocument
                  ) : (
                    <>
                      {row.what || FILE_REFS_AR.sectionLabel(row.section_seq!)}
                      {row.doc_title && <> — {row.doc_title}</>}
                    </>
                  )}
                  {pages && (
                    <span className="ms-1.5 text-xs tabular-nums text-muted-foreground">({pages})</span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
