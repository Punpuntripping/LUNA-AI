"use client";

import { FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { FILE_REFS_AR, type FileRefRow } from "@/lib/contracts";

/**
 * The square of a file ref (``.claude/plans/aggregator_file_refs.md`` §4.6):
 * the «الخيوط» section-number square (``AngleMapDiagram``) showing the section
 * number only (D1); a whole-document ref (``[F2]``) shows a file icon in the
 * same square (D8). Colour (D14, owner 2026-11-28 — replaces D2's neutral): the
 * brand's aubergine/mauve pair with a soft shadow (``--file-ref-*``), so the
 * user's own file never looks like a library citation (sage ``[n]``).
 */
const SQUARE_CLASS =
  "inline-flex min-w-7 items-center justify-center rounded-md border border-file-ref-border bg-file-ref px-1.5 text-xs font-semibold tabular-nums leading-6 text-file-ref-fg shadow-file-ref";

function SquareContent({ row }: { row: Pick<FileRefRow, "section_seq"> }) {
  return row.section_seq == null ? (
    <FileText aria-hidden="true" className="h-3.5 w-3.5" />
  ) : (
    <>{row.section_seq}</>
  );
}

/** The chip in the report body: tap → the section's popup. */
export function FileRefChip({
  row,
  onOpen,
}: {
  row: FileRefRow;
  onOpen: (ref: string) => void;
}) {
  return (
    <button
      type="button"
      dir="ltr"
      style={{ unicodeBidi: "isolate" }}
      title={row.what || undefined}
      aria-label={FILE_REFS_AR.chipLabel(row)}
      onClick={() => onOpen(row.ref_id)}
      className={cn(
        SQUARE_CLASS,
        "mx-0.5 align-middle transition-colors hover:bg-file-ref-hover",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-file-ref-fg/50",
      )}
    >
      <SquareContent row={row} />
    </button>
  );
}

/** The same square, not interactive — the «من مستنداتك» list's lead. */
export function FileRefSquare({
  row,
  className,
}: {
  row: Pick<FileRefRow, "section_seq">;
  className?: string;
}) {
  return (
    <span aria-hidden="true" dir="ltr" className={cn(SQUARE_CLASS, "shrink-0", className)}>
      <SquareContent row={row} />
    </span>
  );
}
