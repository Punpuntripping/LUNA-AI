"use client";

import { useId, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import type { FilePinnedText } from "@/lib/contracts";
import { FILES_COPY } from "./copy";

/**
 * The library stores each article with a markdown heading line naming it
 * («### المادة العشرون بعد المائة:») and no other markdown (checked on the
 * pinned articles 2026-11-27). The text is shown plain, so the heading marks
 * are dropped and the words kept; its paragraphs are separated by blank lines,
 * which `whitespace-pre-line` would print as wide gaps, so each run of blank
 * lines becomes one line break.
 */
function plainArticleText(text: string): string {
  return text
    .replace(/^[ \t]{0,3}#{1,6}[ \t]*/gm, "")
    .replace(/\n[ \t]*(?:\n[ \t]*)+/g, "\n")
    .trim();
}

/**
 * «الإحالات (n)» — the official articles a section of the file cites,
 * collapsed by default. The same disclosure as «الإحالات» in an opened
 * reference (`CrossRefsSection` in ReferencePanel): a quiet toggle, then one
 * tinted block per article — its title, then its official wording.
 *
 * Renders nothing when there is nothing pinned (the backend sends nothing
 * unless `FILE_PINNED_TEXTS_UI_*` is on for the user).
 */
export function PinnedTexts({
  pins,
  className,
}: {
  pins: FilePinnedText[] | undefined;
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const sectionRef = useRef<HTMLDivElement | null>(null);
  const listId = useId();

  if (!pins || pins.length === 0) return null;

  const toggle = () => {
    setExpanded((wasExpanded) => {
      if (!wasExpanded) {
        requestAnimationFrame(() => {
          sectionRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        });
      }
      return !wasExpanded;
    });
  };

  return (
    <div ref={sectionRef} className={cn("mt-3 border-t border-border/60 pt-2", className)}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        aria-controls={listId}
        className="flex w-full items-center gap-1.5 rounded-md py-1 text-start text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:py-2"
      >
        <ChevronDown
          aria-hidden="true"
          className={cn("h-3.5 w-3.5 shrink-0 transition-transform", expanded && "rotate-180")}
        />
        {FILES_COPY.pinnedTexts} <span className="tabular-nums">({pins.length})</span>
      </button>

      {expanded && (
        <ul id={listId} className="mt-1.5 flex flex-col gap-2.5">
          {pins.map((pin) => (
            <li
              key={pin.article_id}
              className="rounded-lg bg-muted/40 px-2.5 py-2 text-xs leading-relaxed"
            >
              <span className="block font-medium text-foreground">{pin.title}</span>
              <span className="mt-0.5 block whitespace-pre-line text-muted-foreground">
                {plainArticleText(pin.text)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
