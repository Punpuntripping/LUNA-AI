"use client";

import { useMemo } from "react";
import Link from "next/link";
import { FileText } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import { useFilesUi } from "@/components/files/files-ui";
import { isFileRefGone, useFileRefSection } from "@/hooks/use-workspace-item-file-refs";
import { FILE_REFS_AR, formatSectionPages, stripMarkdownImages, type FileRefRow } from "@/lib/contracts";

/**
 * The popup of a file ref (``.claude/plans/aggregator_file_refs.md`` §4.6) —
 * the ``AngleSectionDialog`` layout fed from
 * ``GET /workspace/{item_id}/file-refs/{ref_id}/section``: title = the section's
 * description, then the document, «القسم 7», pages, owner, «قدّمه», and the
 * section's text from its top (no highlight, D5). «افتح الملف» opens the file
 * in «الملفات» when it is still listed there. A 410 = the attachment was
 * deleted on purpose → «لم يعد هذا الملف متاحًا».
 *
 * The header renders at once from the list row; the route's answer replaces it
 * when it lands.
 */
export function FileRefSectionDialog({
  itemId,
  row,
  onClose,
}: {
  itemId: string;
  /** The ref being shown, or null when closed. */
  row: FileRefRow | null;
  onClose: () => void;
}) {
  const filesUi = useFilesUi();
  const { data, error, isLoading } = useFileRefSection(itemId, row?.ref_id ?? null);
  const gone = isFileRefGone(error) || row?.available === false;
  const head = data ?? row;
  const text = useMemo(() => stripMarkdownImages(data?.content_md ?? "").trim(), [data]);
  const pages = head ? formatSectionPages(head.page_from, head.page_to) : "";
  const whole = head?.section_seq == null;

  return (
    <Dialog open={row != null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent dir="rtl" className="max-h-[85dvh] max-w-2xl overflow-y-auto sm:max-w-2xl">
        {head && (
          <>
            <DialogHeader className="text-start">
              <DialogTitle className="pe-6 text-base leading-7">
                {whole
                  ? head.doc_title || FILE_REFS_AR.wholeDocument
                  : head.what || FILE_REFS_AR.sectionLabel(head.section_seq!)}
              </DialogTitle>
              <DialogDescription asChild>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5 text-muted-foreground">
                  {!whole && head.doc_title && <span>{head.doc_title}</span>}
                  <span className="tabular-nums">
                    {whole ? FILE_REFS_AR.wholeDocument : FILE_REFS_AR.sectionLabel(head.section_seq!)}
                  </span>
                  {pages && <span className="tabular-nums">{pages}</span>}
                  {head.owner && (
                    <span>
                      {FILE_REFS_AR.owner}: {head.owner}
                    </span>
                  )}
                  {head.brought_by && (
                    <span>
                      {FILE_REFS_AR.broughtBy}: {head.brought_by}
                    </span>
                  )}
                </div>
              </DialogDescription>
            </DialogHeader>

            <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2.5">
              {gone ? (
                <p className="text-sm text-muted-foreground">{FILE_REFS_AR.gone}</p>
              ) : isLoading ? (
                <div className="space-y-2" aria-busy="true">
                  {[0, 1, 2].map((i) => (
                    <div key={i} className="h-4 rounded bg-muted/60 animate-pulse" />
                  ))}
                </div>
              ) : error ? (
                <p className="text-sm text-muted-foreground">{FILE_REFS_AR.loadError}</p>
              ) : text ? (
                <MarkdownRenderer content={text} />
              ) : (
                <p className="text-sm text-muted-foreground">{FILE_REFS_AR.emptySection}</p>
              )}
            </div>

            {!gone && data?.files_item_id && (
              <div className="flex justify-center">
                {/* The one green «افتح …» button (ArtifactChip's look), as a link. */}
                <Link
                  href={filesUi.fileHref(data.files_item_id)}
                  className={cn(buttonVariants({ size: "sm" }), "h-8 gap-1.5 px-3 text-xs")}
                >
                  <FileText className="h-3.5 w-3.5" />
                  {FILE_REFS_AR.openFile}
                </Link>
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
