"use client";

import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { MarkdownRenderer } from "@/components/chat/MarkdownRenderer";
import {
  ANGLE_KIND_LABELS,
  ANGLE_SIDE_LABELS,
  angleDocumentLabel,
  angleMapSection,
  anglesOfSection,
  coerceAngleKind,
  formatSectionPages,
  stripMarkdownImages,
  type AngleMap,
} from "@/lib/contracts";
import { PinnedTexts } from "../PinnedTexts";
import { ANGLE_KIND_STYLE, ANGLE_SIDE_TAG } from "./angle-style";
import { AngleNumber } from "./AngleMapDiagram";
import { ANGLES_COPY } from "./copy";

/**
 * One section's own text, opened from the map or a card: its document, number,
 * pages, side, owner and «قدّمه», the text, and the angles resting on it (tap
 * one to show it on the map).
 */
export function AngleSectionDialog({
  map,
  sectionRef,
  onClose,
  onSelectAngle,
}: {
  map: AngleMap;
  sectionRef: string | null;
  onClose: () => void;
  onSelectAngle: (seq: number) => void;
}) {
  const section = sectionRef ? angleMapSection(map, sectionRef) : null;
  const angles = useMemo(() => (sectionRef ? anglesOfSection(map, sectionRef) : []), [map, sectionRef]);
  const text = useMemo(() => stripMarkdownImages(section?.content_md ?? "").trim(), [section]);
  const document = section ? map.documents.find((d) => d.index === section.document) : null;
  const pages = section ? formatSectionPages(section.page_from, section.page_to) : "";

  return (
    <Dialog open={section != null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent dir="rtl" className="max-h-[85dvh] max-w-2xl overflow-y-auto sm:max-w-2xl">
        {section && (
          <>
            <DialogHeader className="text-start">
              <DialogTitle className="pe-6 text-base leading-7">{section.what || ANGLES_COPY.sectionLabel(section.seq)}</DialogTitle>
              <DialogDescription asChild>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5 text-muted-foreground">
                  {map.documents.length > 1 && document && (
                    <span>
                      {angleDocumentLabel(document.index)}: {document.title}
                    </span>
                  )}
                  <span className="tabular-nums">{ANGLES_COPY.sectionLabel(section.seq)}</span>
                  {pages && <span className="tabular-nums">{pages}</span>}
                  {section.side && (
                    <span className={cn("rounded-full px-1.5 font-medium", ANGLE_SIDE_TAG[section.side])}>
                      {ANGLE_SIDE_LABELS[section.side]}
                    </span>
                  )}
                  {section.owner && (
                    <span>
                      {ANGLES_COPY.owner}: {section.owner}
                    </span>
                  )}
                  {section.brought_by && (
                    <span>
                      {ANGLES_COPY.broughtBy}: {section.brought_by}
                    </span>
                  )}
                </div>
              </DialogDescription>
            </DialogHeader>

            <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2.5">
              {text ? (
                <MarkdownRenderer content={text} />
              ) : (
                <p className="text-sm text-muted-foreground">{ANGLES_COPY.emptySection}</p>
              )}
              <PinnedTexts pins={section.pinned_texts} />
            </div>

            {angles.length > 0 && (
              <div>
                <p className="mb-1.5 text-xs font-semibold text-muted-foreground">{ANGLES_COPY.anglesOfSection}</p>
                <ul className="space-y-1">
                  {angles.map((a) => {
                    const kind = coerceAngleKind(a.kind);
                    return (
                      <li key={a.seq}>
                        <button
                          type="button"
                          onClick={() => {
                            onSelectAngle(a.seq);
                            onClose();
                          }}
                          className="flex w-full items-start gap-2 rounded-md px-1.5 py-1 text-start text-sm leading-6 transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <AngleNumber seq={a.seq} kind={kind} className="mt-0.5" />
                          <span className="min-w-0 flex-1">
                            {a.title}
                            <span className={cn("ms-1.5 text-xs font-semibold", ANGLE_KIND_STYLE[kind].text)}>
                              {ANGLE_KIND_LABELS[kind]}
                            </span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
